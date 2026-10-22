import os
import logging
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    print("\n" + "=" * 40)
    print("🚀 ARBPRO CORE SYSTEM STARTING")
    print(f"Host: {host}:{port}")
    print("=" * 40)
    uvicorn.run("api:app", host=host, port=port, reload=False)
