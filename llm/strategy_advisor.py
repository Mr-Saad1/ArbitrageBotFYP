import asyncio
import json
import logging
import os
import re

from dotenv import load_dotenv
from google import genai

load_dotenv()
logger = logging.getLogger(__name__)


class MarketAnalyst:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
            self.model_id = "gemini-2.0-flash"
        else:
            self.client = None
            logger.error("CRITICAL: MarketAnalyst initialized without API Key!")

    def _clean_and_parse_json(self, raw_text: str) -> dict:
        fallback = {
            "regime": "VOLATILE",
            "volatility": 5,
            "sentiment": 0.0,
            "support": 0.0,
            "resistance": 0.0,
            "reasoning": "Failsafe triggered: Parse failed or API offline.",
        }
        if not raw_text:
            return fallback
        try:
            cleaned = re.sub(r"```json\s*(.*?)\s*```", r"\1", raw_text, flags=re.DOTALL | re.IGNORECASE).strip()
            if "```" in cleaned:
                cleaned = cleaned.replace("```", "").strip()
            parsed_data = json.loads(cleaned)
            for key in ["regime", "volatility", "sentiment", "support", "resistance"]:
                if key not in parsed_data:
                    logger.warning(f"Missing key '{key}' in MarketAnalyst response. Using fallback.")
                    return fallback
            return parsed_data
        except Exception as exc:
            logger.error(f"MarketAnalyst JSON Error: {exc}")
            return fallback

    async def analyze_regime(self, recent_prices: list) -> dict:
        if not self.client:
            return self._clean_and_parse_json("")

        prompt = f"""
        You are ArbPro's Market Intelligence Engine.
        Analyze this recent BTC price action: {recent_prices}

        Identify the market regime, volatility (0-10 scale), and sentiment (-1.0 to 1.0).
        Estimate primary support and resistance levels.

        Respond ONLY in raw JSON matching this schema exactly:
        {{"regime": "TRENDING" or "RANGING" or "VOLATILE", "volatility": integer 0-10, "sentiment": float -1.0 to 1.0, "support": float, "resistance": float, "reasoning": "brief string explanation"}}
        """

        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model_id,
                contents=prompt,
            )
            return self._clean_and_parse_json(response.text)
        except Exception as exc:
            logger.error(f"MarketAnalyst API Execution Error: {exc}")
            return self._clean_and_parse_json("")
