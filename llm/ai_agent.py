import sqlite3
import logging

logger = logging.getLogger(__name__)


class RiskEngine:
    def __init__(self, db_path="arbitrage.db", max_risk_per_trade=0.02, kelly_fraction=0.2, daily_loss_limit=50.0):
        self.db_path = db_path
        self.max_risk_per_trade = max_risk_per_trade
        self.kelly_fraction = kelly_fraction
        self.daily_loss_limit = daily_loss_limit

    def get_daily_pnl(self) -> float:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                columns = [row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()]
                if "profit" in columns:
                    result = conn.execute(
                        "SELECT SUM(profit) AS daily_pnl FROM trades WHERE timestamp >= datetime('now', '-1 day')"
                    ).fetchone()
                elif "profit_usdt" in columns:
                    result = conn.execute(
                        "SELECT SUM(profit_usdt) AS daily_pnl FROM trades WHERE timestamp >= datetime('now', '-1 day')"
                    ).fetchone()
                else:
                    return 0.0
                return float(result["daily_pnl"] or 0.0)
        except Exception as exc:
            logger.error(f"Risk Engine DB Error: {exc}")
            return 0.0

    def get_recent_trades(self, limit=100):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                columns = [row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()]
                if "profit" in columns:
                    rows = conn.execute("SELECT profit FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
                    return [float(row["profit"] or 0.0) for row in rows]
                if "profit_usdt" in columns:
                    rows = conn.execute("SELECT profit_usdt FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
                    return [float(row["profit_usdt"] or 0.0) for row in rows]
                return []
        except Exception:
            return []

    def check_circuit_breaker(self) -> dict:
        daily_pnl = self.get_daily_pnl()
        if daily_pnl <= -abs(self.daily_loss_limit):
            logger.critical(f"CIRCUIT BREAKER: Daily loss limit (${self.daily_loss_limit}) exceeded.")
            return {"halted": True, "reason": "Daily Drawdown Exceeded"}

        trades = self.get_recent_trades(limit=3)
        if len(trades) == 3 and all(p < 0 for p in trades):
            logger.warning("CIRCUIT BREAKER: 3 consecutive losses detected.")
            return {"halted": True, "reason": "Consecutive Loss Streak"}

        return {"halted": False, "reason": "System Healthy"}

    def calculate_kelly_position(self, ai_confidence: int) -> float:
        trades = self.get_recent_trades(limit=100)
        base_size = self.max_risk_per_trade * 0.10

        if len(trades) < 10:
            return base_size

        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t <= 0]

        if not losses or not wins:
            return base_size

        historical_win_rate = len(wins) / len(trades)
        blended_win_rate = (historical_win_rate * 0.5) + ((ai_confidence / 100.0) * 0.5)
        loss_rate = 1.0 - blended_win_rate

        avg_win = sum(wins) / len(wins)
        avg_loss = abs(sum(losses) / len(losses))

        if avg_loss == 0:
            return base_size

        b = avg_win / avg_loss
        kelly_fraction = (b * blended_win_rate - loss_rate) / b
        adjusted_kelly = max(0, kelly_fraction * self.kelly_fraction)
        return min(adjusted_kelly, self.max_risk_per_trade)

    def validate_and_size_trade(self, llm_decision: str, llm_confidence: int) -> dict:
        breaker_status = self.check_circuit_breaker()
        if breaker_status["halted"]:
            return {"approved": False, "reason": breaker_status["reason"], "size": 0}

        if llm_decision in ["WAIT", "REJECT"]:
            return {"approved": False, "reason": "AI Veto", "size": 0}

        optimal_size = self.calculate_kelly_position(ai_confidence=llm_confidence)

        if optimal_size <= 0:
            return {"approved": False, "reason": "Kelly sizing <= 0 (EV is negative)", "size": 0}

        return {"approved": True, "reason": "Risk checks passed", "size": optimal_size}
