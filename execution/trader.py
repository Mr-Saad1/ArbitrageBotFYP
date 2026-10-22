import asyncio
import json
import logging
import os
import re
import sqlite3

from dotenv import load_dotenv
from google import genai

load_dotenv()
logger = logging.getLogger(__name__)


class StrategyAdvisor:
    def __init__(self, db_path="arbitrage.db", api_key: str = None):
        self.db_path = db_path
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
            self.model_id = "gemini-2.0-flash"
        else:
            self.client = None
            logger.error("CRITICAL: StrategyAdvisor initialized without API Key!")

    def fetch_recent_trades(self, limit=50):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                columns = [row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()]
                if "profit" in columns:
                    cursor = conn.execute("SELECT profit, route, timestamp FROM trades ORDER BY id DESC LIMIT ?", (limit,))
                elif "profit_usdt" in columns:
                    cursor = conn.execute("SELECT profit_usdt, route, timestamp FROM trades ORDER BY id DESC LIMIT ?", (limit,))
                else:
                    return []
                return [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            logger.error(f"StrategyAdvisor DB Error: {exc}")
            return []

    def _clean_and_parse_json(self, raw_text: str) -> dict:
        fallback = {
            "suggested_spread_threshold": 0.5,
            "portfolio_health": "UNKNOWN",
            "recommendation": "Failsafe triggered: Hold steady. Parsing failed or API offline.",
        }
        if not raw_text:
            return fallback

        try:
            cleaned = re.sub(r"```json\s*(.*?)\s*```", r"\1", raw_text, flags=re.DOTALL | re.IGNORECASE).strip()
            if "```" in cleaned:
                cleaned = cleaned.replace("```", "").strip()
            parsed_data = json.loads(cleaned)
            for key in ["suggested_spread_threshold", "portfolio_health", "recommendation"]:
                if key not in parsed_data:
                    logger.warning(f"Missing key '{key}' in StrategyAdvisor response. Using fallback.")
                    return fallback
            return parsed_data
        except json.JSONDecodeError as exc:
            logger.error(f"StrategyAdvisor JSON Parsing Error: {exc}. Raw text: {raw_text}")
            return fallback
        except Exception as exc:
            logger.error(f"StrategyAdvisor JSON Error: {exc}")
            return fallback

    async def generate_weekly_report(self) -> dict:
        trades = self.fetch_recent_trades()
        if not trades or not self.client:
            return self._clean_and_parse_json("")

        prompt = f"""
        You are ArbPro's Strategy Optimizer (Tier 3 AI).
        Review the last {len(trades)} trades:
        {trades}

        Analyze our win rate, calculate an estimated Sharpe Ratio, and recommend a new minimum spread threshold.
        Respond ONLY in raw JSON matching this schema exactly:
        {{"suggested_spread_threshold": float, "portfolio_health": "POOR" or "GOOD" or "EXCELLENT", "recommendation": "detailed optimization strategy"}}
        """

        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model_id,
                contents=prompt,
            )
            return self._clean_and_parse_json(response.text)
        except Exception as exc:
            logger.error(f"StrategyAdvisor API Error: {exc}")
            return self._clean_and_parse_json("")
