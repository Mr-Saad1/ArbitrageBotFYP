import asyncio
import logging
import os

from dotenv import load_dotenv
from google import genai

from llm.ai_agent import AIAgent

load_dotenv()
logger = logging.getLogger(__name__)


class ChatBot:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.agent = AIAgent(api_key=self.api_key)

        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
            self.model_id = "gemini-2.0-flash"
        else:
            self.client = None
            logger.error("CRITICAL: ChatBot initialized without API key!")

        self.system_prompt = (
            "You are ArbPro, a Senior Quantitative Cryptocurrency Arbitrage Analyst. "
            "Your job is to explain trading decisions, analyze spreads between Binance and Bybit, "
            "and evaluate market risk. Do not answer general knowledge or non-financial questions. "
            "Keep answers concise, professional, and focused on actionable quantitative metrics."
        )

    async def process_chat_query(self, user_query: str, market_context: dict = None) -> str:
        if not self.client:
            return "System Error: AI is offline due to missing API key."

        context_str = f"\n[Current Market Context]: {market_context}" if market_context else "\n[Current Market Context]: None provided."
        full_prompt = f"{self.system_prompt}{context_str}\n\nUser Question: {user_query}"

        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model_id,
                contents=full_prompt,
            )
            return response.text
        except Exception as exc:
            logger.error(f"ChatBot Execution Error: {exc}")
            return "I am currently experiencing technical difficulties connecting to the market oracle."

    async def analyze_opportunity(self, binance_price: float, bybit_price: float, spread: float) -> dict:
        return await self.agent.analyze_opportunity(binance_price, bybit_price, spread)


analyst = ChatBot(api_key=os.getenv("GEMINI_API_KEY"))
