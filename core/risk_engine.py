import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def save_trade(route, profit, symbol="BTC/USDT"):
    """Standalone helper to save a trade from any module."""
    try:
        db = DatabaseCore(db_path="arbitrage.db")
        db.save_trade_record(route, profit, symbol)
    except Exception as exc:
        logger.error(f"Global save_trade failed: {exc}")


class DatabaseCore:
    def __init__(self, db_path="arbitrage.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                cursor = conn.cursor()

                cursor.execute("PRAGMA table_info(trades)")
                cols = [c[1] for c in cursor.fetchall()]

                if not cols:
                    conn.execute(
                        """
                        CREATE TABLE trades (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                            symbol TEXT DEFAULT 'BTC/USDT',
                            route TEXT,
                            profit REAL
                        )
                        """
                    )
                else:
                    if "time" in cols and "timestamp" not in cols:
                        conn.execute("ALTER TABLE trades RENAME COLUMN time TO timestamp")
                    if "symbol" not in cols:
                        conn.execute("ALTER TABLE trades ADD COLUMN symbol TEXT DEFAULT 'BTC/USDT'")
                    if "route" not in cols:
                        conn.execute("ALTER TABLE trades ADD COLUMN route TEXT")
                    if "profit_usdt" in cols and "profit" not in cols:
                        conn.execute("ALTER TABLE trades RENAME COLUMN profit_usdt TO profit")
                    if "profit" not in cols:
                        conn.execute("ALTER TABLE trades ADD COLUMN profit REAL")

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS llm_decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        binance_price REAL,
                        bybit_price REAL,
                        spread REAL,
                        decision TEXT,
                        confidence INTEGER,
                        position_size REAL,
                        reasoning TEXT
                    )
                    """
                )

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS market_analysis (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        market_regime TEXT,
                        volatility_score REAL,
                        sentiment REAL,
                        support_level REAL,
                        resistance_level REAL
                    )
                    """
                )

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

            logger.info(f"Database {self.db_path} schema verified.")
        except Exception as exc:
            logger.error(f"Migration/Init Error: {exc}")

    def save_trade_record(self, route, profit, symbol="BTC/USDT"):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO trades (symbol, route, profit)
                    VALUES (?, ?, ?)
                    """,
                    (symbol, route, profit),
                )
        except Exception as exc:
            logger.error(f"Failed to save trade: {exc}")

    def log_llm_decision(self, binance_price, bybit_price, spread, llm_output):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO llm_decisions 
                    (binance_price, bybit_price, spread, decision, confidence, position_size, reasoning)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        binance_price,
                        bybit_price,
                        spread,
                        llm_output.get("decision", "WAIT"),
                        llm_output.get("confidence", 0),
                        llm_output.get("position_size", 0.0),
                        llm_output.get("reasoning", "No reason provided"),
                    ),
                )
        except Exception as exc:
            logger.error(f"Failed to log LLM decision: {exc}")

    def cache_market_analysis(self, analysis_data):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO market_analysis
                    (market_regime, volatility_score, sentiment, support_level, resistance_level)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        analysis_data.get("regime", "UNKNOWN"),
                        float(analysis_data.get("volatility", 5.0)),
                        float(analysis_data.get("sentiment", 0.0)),
                        float(analysis_data.get("support", 0.0)),
                        float(analysis_data.get("resistance", 0.0)),
                    ),
                )
        except Exception as exc:
            logger.error(f"Failed to cache market analysis: {exc}")
