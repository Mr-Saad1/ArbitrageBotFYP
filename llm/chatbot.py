import asyncio
import json
import logging
import os
import re

from google import genai
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class AIAgent:
    def __init__(self, api_key: str = None, groq_api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.groq_api_key = groq_api_key or os.getenv("GROQ_API_KEY")

        self.client = genai.Client(api_key=self.api_key) if self.api_key else None
        self.model_id = "gemini-2.0-flash"
        self.groq_client = Groq(api_key=self.groq_api_key) if self.groq_api_key else None

    def _clean_and_parse_json(self, raw_text: str) -> dict:
        fallback_schema = {
            "decision": "REJECT",
            "confidence": 0,
            "position_size": 0.0,
            "reasoning": "Failsafe triggered: LLM output was unparsable or API failed.",
        }
        if not raw_text:
            return fallback_schema

        try:
            cleaned_text = re.sub(r"```json\s*(.*?)\s*```", r"\1", raw_text, flags=re.DOTALL | re.IGNORECASE).strip()
            if "```" in cleaned_text:
                cleaned_text = cleaned_text.replace("```", "").strip()
            parsed_data = json.loads(cleaned_text)

            for key in ["decision", "confidence", "reasoning"]:
                if key not in parsed_data:
                    logger.warning(f"Missing key '{key}' in LLM response. Using fallback.")
                    return fallback_schema
            if "position_size" not in parsed_data:
                parsed_data["position_size"] = 0.0
            return parsed_data
        except json.JSONDecodeError as exc:
            logger.error(f"JSON Parsing Error: {exc}. Raw text: {raw_text}")
            return fallback_schema
        except Exception as exc:
            logger.error(f"Unexpected error in JSON parser: {exc}")
            return fallback_schema

    async def analyze_opportunity(self, binance_price: float, bybit_price: float, spread: float) -> dict:
        prompt = f"""
        Arbitrage Opportunity Detected!
        Binance Price: ${binance_price}
        Bybit Price: ${bybit_price}
        Spread: {spread}%

        Analyze this spread. Is it safe to trade?
        Consider fee/slippage overhead (~0.25%).

        Respond ONLY in valid JSON format:
        {{
            "decision": "EXECUTE" or "REJECT",
            "reasoning": "Short explanation",
            "confidence": 0-100,
            "position_size": 0.0
        }}
        """

        if self.client:
            try:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_id,
                    contents=prompt,
                )
                return self._clean_and_parse_json(response.text)
            except Exception as exc:
                logger.error(f"❌ Gemini API Error: {exc}")
                logger.info("🔄 Switching to Fallback AI (Groq)...")
        else:
            logger.warning("No Gemini Client configured. Switching to Fallback AI (Groq)...")

        if self.groq_client:
            try:
                chat_completion = self.groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model="llama-3.1-8b-instant",
                    response_format={"type": "json_object"},
                )
                content = chat_completion.choices[0].message.content
                parsed = json.loads(content)
                return {
                    "decision": parsed.get("decision", "REJECT"),
                    "reasoning": parsed.get("reasoning", "Fallback decision."),
                    "confidence": int(parsed.get("confidence", 0)),
                    "position_size": float(parsed.get("position_size", 0.0)),
                }
            except Exception as exc:
                logger.error(f"❌ Fallback AI Error: {exc}")
        else:
            logger.warning("No Groq client configured. Cannot use fallback.")

        return {
            "decision": "REJECT",
            "reasoning": "All AI models unreachable. Safety abort.",
            "confidence": 0,
            "position_size": 0.0,
        }

    async def chat(self, question: str, system_prompt: str = "You are a helpful assistant.") -> str:
        if not self.client:
            return "AI Agent is not configured. Please check your API keys."

        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=[
                    {"role": "user", "parts": [{"text": f"System Instruction: {system_prompt}\n\nUser Question: {question}"}]}
                ],
            )
            return response.text
        except Exception as exc:
            logger.error(f"Chat Error: {exc}")
            return "I'm having trouble connecting to my brain right now. Please try again later."
