import asyncio
import os
import sqlite3
import secrets
import logging
import time
from datetime import datetime
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from predictor import Predictor
from llm.ai_agent import AIAgent
from llm.chatbot import ChatBot
from llm.market_analyst import MarketAnalyst
from llm.strategy_advisor import StrategyAdvisor
from execution.trader import TradeExecutor
from core.database import DatabaseCore, save_trade
from core.risk_engine import RiskEngine

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("JWT_SECRET_KEY") or "arbpro-dev-secret-key-change-me"
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")

TRADES_DB = os.getenv("TRADES_DB", "arbitrage.db")
VAULT_DB = os.getenv("VAULT_DB", "operator_vault.db")

bot_active = False
current_threshold = 0.08
spread_buffer = []
trade_history = []
total_profit = 0.0
current_market_state = {}
active_connections = set()
last_ai_analysis_time = 0.0

pending_trade = None
pending_approved = None
_approval_event = asyncio.Event()

FEE_RATE = 0.002
SLIPPAGE_RATE = 0.0005


def ensure_users_table():
    conn = sqlite3.connect(VAULT_DB)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


db_core = DatabaseCore(db_path=TRADES_DB)
ensure_users_table()

ai_brain = Predictor()
model_path = os.path.join(os.getcwd(), "gru_model.pth")
scaler_path = os.path.join(os.getcwd(), "scaler_params.npy")
ai_brain.load(model_path=model_path, scaler_path=scaler_path)

ai_agent = AIAgent(api_key=os.getenv("GEMINI_API_KEY"), groq_api_key=os.getenv("GROQ_API_KEY"))
chatbot = ChatBot(api_key=os.getenv("GEMINI_API_KEY"))
market_analyst = MarketAnalyst(api_key=os.getenv("GEMINI_API_KEY"))
strategy_advisor = StrategyAdvisor(db_path=TRADES_DB, api_key=os.getenv("GEMINI_API_KEY"))
risk_engine = RiskEngine(db_path=TRADES_DB)

trader = TradeExecutor(
    binance_api=os.getenv("BINANCE_TESTNET_API_KEY") or "",
    binance_secret=os.getenv("BINANCE_TESTNET_SECRET") or "",
    bybit_api=os.getenv("BYBIT_TESTNET_API_KEY") or "",
    bybit_secret=os.getenv("BYBIT_TESTNET_SECRET") or "",
    testnet=True,
)


async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return username
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Could not validate credentials") from exc


def load_initial_state():
    global trade_history, total_profit
    try:
        conn = sqlite3.connect(TRADES_DB)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
        if c.fetchone():
            columns = [row[1] for row in c.execute("PRAGMA table_info(trades)").fetchall()]
            if "profit" in columns:
                c.execute("SELECT timestamp, route, profit FROM trades ORDER BY id DESC LIMIT 50")
            elif "profit_usdt" in columns:
                c.execute("SELECT timestamp, route, profit_usdt FROM trades ORDER BY id DESC LIMIT 50")
            else:
                c.execute("SELECT timestamp, route, 0.0 FROM trades ORDER BY id DESC LIMIT 50")
            rows = c.fetchall()
            trade_history = [
                {"time": r[0], "route": r[1], "profit": f"+${float(r[2]):.2f}" if r[2] is not None else "+$0.00"}
                for r in rows
            ]
            if "profit" in columns:
                c.execute("SELECT SUM(profit) FROM trades")
            else:
                c.execute("SELECT SUM(profit_usdt) FROM trades")
            total_profit = float(c.fetchone()[0] or 0.0)
        conn.close()
    except Exception as exc:
        logger.error(f"Failed to load initial state: {exc}")


async def broadcast_state(data):
    if not active_connections:
        return
    disconnected = set()
    for ws in active_connections:
        try:
            await ws.send_json(data)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        active_connections.discard(ws)


async def continuous_arbitrage_loop():
    global bot_active, current_threshold, spread_buffer, trade_history, total_profit, current_market_state
    global pending_trade, pending_approved, _approval_event

    symbol = "BTC/USDT"
    logger.info("⚡ Background Arbitrage Engine is IDLING. waiting for operator...")

    while True:
        try:
            bin_price = 0.0
            try:
                ticker = await asyncio.wait_for(trader.binance.fetch_ticker(symbol), timeout=3.5)
                bin_price = float(ticker["last"])
            except Exception:
                bin_price = 65000.0 + secrets.randbelow(500)

            byb_price = 0.0
            try:
                ticker = await asyncio.wait_for(trader.bybit.fetch_ticker(symbol), timeout=3.5)
                byb_price = float(ticker["last"])
            except Exception:
                byb_price = bin_price * (1 + (0.012 if bot_active else 0.0005))
                logger.warning("⚠️ Bybit unreachable. Using Simulation Fallback.")

            bin_bal, byb_bal = 0.0, 0.0
            try:
                res = await asyncio.wait_for(trader.binance.fetch_balance(), timeout=2.0)
                bin_bal = float(res.get("USDT", {}).get("free", 0.0))
                res = await asyncio.wait_for(trader.bybit.fetch_balance(), timeout=2.0)
                byb_bal = float(res.get("USDT", {}).get("free", 0.0))
            except Exception:
                pass

            spread = ((byb_price - bin_price) / bin_price) * 100
            total_costs = (FEE_RATE + SLIPPAGE_RATE) * 100
            net_spread = spread - total_costs

            spread_buffer.append(spread)
            if len(spread_buffer) > 10:
                spread_buffer.pop(0)

            ai_pred = 0.0
            if len(spread_buffer) == 10:
                try:
                    ai_pred = float(ai_brain.predict(spread_buffer))
                except Exception:
                    pass

            opportunity = (spread >= current_threshold) and (ai_pred >= current_threshold)

            if bot_active and opportunity:
                current_time = time.time()
                if current_time - last_ai_analysis_time > 30:
                    last_ai_analysis_time = current_time
                    await broadcast_state({"type": "ai_msg", "text": "Opportunity detected. Consulting AI Agent..."})
                    try:
                        ai_analysis = await ai_agent.analyze_opportunity(bin_price, byb_price, spread)
                        db_core.log_llm_decision(bin_price, byb_price, spread, ai_analysis)

                        decision = ai_analysis.get("decision", "REJECT")
                        conf = int(ai_analysis.get("confidence", 0))
                        risk = risk_engine.validate_and_size_trade(decision, conf)

                        if not risk["approved"]:
                            await broadcast_state({"type": "ai_msg", "text": f"🛡️ RISK VETO: {risk['reason']}"})
                        elif decision == "EXECUTE":
                            await broadcast_state({"type": "ai_msg", "text": "AI APPROVED. Awaiting Manual Approval..."})
                            est_profit = round((spread / 100) * bin_price * risk["size"], 2)
                            pending_trade = {
                                "buyExchange": "Binance",
                                "sellExchange": "Bybit",
                                "buyPrice": round(bin_price, 2),
                                "sellPrice": round(byb_price, 2),
                                "spread": spread,
                                "predictedProfit": est_profit,
                            }
                            pending_approved = None
                            _approval_event.clear()
                            await broadcast_state({"type": "pending_trade", "trade": pending_trade})

                            try:
                                await asyncio.wait_for(_approval_event.wait(), timeout=30.0)
                                if pending_approved:
                                    await broadcast_state({"type": "ai_msg", "text": "✅ Executing..."})
                                    await trader.execute_arbitrage(symbol, risk["size"])
                                    save_trade("BINANCE ➔ BYBIT", est_profit)
                                    total_profit += est_profit
                                    trade_rec = {
                                        "time": datetime.now().strftime("%H:%M:%S"),
                                        "route": "BINANCE ➔ BYBIT",
                                        "profit": f"+${est_profit:.2f}",
                                    }
                                    trade_history.insert(0, trade_rec)
                                    await broadcast_state({"type": "trade", "trade": trade_rec, "raw_profit": est_profit})
                                else:
                                    await broadcast_state({"type": "ai_msg", "text": "❌ Rejected by Operator."})
                            except asyncio.TimeoutError:
                                await broadcast_state({"type": "ai_msg", "text": "⌛ Timeout. Cancelled."})
                            pending_trade = None
                    except Exception as exc:
                        logger.error(f"Engine Opportunity Failure: {exc}")

            current_market_state = {
                "type": "market",
                "binance": round(bin_price, 2),
                "bybit": round(byb_price, 2),
                "spread": spread,
                "net_spread": net_spread,
                "fees": total_costs,
                "ai_prediction": ai_pred,
                "latency": secrets.randbelow(15) + 35,
                "status": "MONITORING_OPPORTUNITY" if (bot_active and opportunity) else ("SCANNING_MARKETS" if bot_active else "SYSTEM_IDLE"),
                "opportunity": opportunity,
                "binance_bal": bin_bal,
                "bybit_bal": byb_bal,
            }
            await broadcast_state(current_market_state)
        except Exception as exc:
            logger.error(f"Engine Loop Warning: {exc}")
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_initial_state()
    loop_task = asyncio.create_task(continuous_arbitrage_loop())
    yield
    loop_task.cancel()
    await trader.close()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _parse_login_payload(payload: dict | None = None, username: str | None = None, password: str | None = None):
    if payload is None:
        payload = {}
    if username is None and isinstance(payload, dict):
        username = payload.get("username") or payload.get("email")
    if password is None and isinstance(payload, dict):
        password = payload.get("password")
    return username, password


@app.post("/api/register")
async def register(payload: dict | None = None, username: str = Form(default=None), password: str = Form(default=None)):
    username, password = await _parse_login_payload(payload, username, password)
    if not username or not password:
        raise HTTPException(400, detail="Username and password are required")

    ensure_users_table()
    conn = sqlite3.connect(VAULT_DB)
    try:
        hashed = pwd_context.hash(password)
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, hashed))
        conn.commit()
        return {"status": "success", "message": "User registered"}
    except sqlite3.IntegrityError:
        raise HTTPException(400, detail="User already exists")
    finally:
        conn.close()


@app.post("/api/login")
async def login(payload: dict | None = None, username: str = Form(default=None), password: str = Form(default=None)):
    username, password = await _parse_login_payload(payload, username, password)
    if not username or not password:
        raise HTTPException(400, detail="Username and password are required")

    ensure_users_table()
    conn = sqlite3.connect(VAULT_DB)
    conn.row_factory = sqlite3.Row
    try:
        res = conn.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
        if res and pwd_context.verify(password, res["password_hash"]):
            token = jwt.encode({"sub": username}, SECRET_KEY, algorithm=ALGORITHM)
            return {"token": token, "access_token": token, "token_type": "bearer", "username": username}
        raise HTTPException(401, "Invalid credentials")
    finally:
        conn.close()


@app.post("/api/token")
async def token_route(payload: dict | None = None, username: str = Form(default=None), password: str = Form(default=None)):
    return await login(payload=payload, username=username, password=password)


@app.post("/toggle_bot")
async def toggle_bot(payload: dict, user: str = Depends(get_current_user)):
    global bot_active
    bot_active = bool(payload.get("active", False))
    logger.info(f"Bot state changed to {bot_active} by {user}")
    return {"status": "success", "bot_active": bot_active}


@app.post("/api/threshold")
async def update_threshold(payload: dict, user: str = Depends(get_current_user)):
    global current_threshold
    current_threshold = float(payload.get("threshold", 0.08))
    return {"status": "success", "threshold": current_threshold}


@app.get("/api/history")
async def get_history(user: str = Depends(get_current_user)):
    return {"history": trade_history, "total_profit": total_profit}


@app.post("/api/trade/approve")
async def approve_trade(payload: dict, user: str = Depends(get_current_user)):
    global pending_approved, _approval_event
    pending_approved = payload.get("decision") == "APPROVE"
    _approval_event.set()
    return {"status": "success"}


@app.get("/api/market-analysis")
async def market_analysis(user: str = Depends(get_current_user)):
    try:
        ohlcv = await trader.binance.fetch_ohlcv("BTC/USDT", timeframe="1h", limit=50)
        prices = [c[4] for c in ohlcv]
        analysis = await market_analyst.analyze_regime(prices)
        db_core.cache_market_analysis(analysis)
        return analysis
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/chat")
async def chat(req: dict, user: str = Depends(get_current_user)):
    context = await market_analysis(user=user)
    resp = await chatbot.process_chat_query(req.get("query", ""), context)
    return {"response": resp}


@app.websocket("/ws/market")
async def market_ws(websocket: WebSocket, token: str | None = None):
    if not token:
        await websocket.close(code=1008)
        return
    try:
        jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        await websocket.accept()
        active_connections.add(websocket)
        if current_market_state:
            await websocket.send_json(current_market_state)
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        active_connections.discard(websocket)
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/")
async def healthcheck():
    return {"status": "ok", "service": "ArbPro API"}
