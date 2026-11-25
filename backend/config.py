"""
Configuration constants for the Travel Budget Planner backend.
Reads from environment variables if set; falls back to sensible defaults
so the app works without a .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Ollama
OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

# Agent safety cap — purely defensive, NOT the stopping mechanism.
# The model decides when to stop tool-calling; this only prevents runaway loops.
MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "6"))

# Backend
BACKEND_PORT: int = int(os.getenv("BACKEND_PORT", "8000"))
BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("TRAVEL_PLANNER_DATA_DIR", str(BACKEND_DIR / "data")))
DATABASE_PATH = Path(
    os.getenv("TRAVEL_PLANNER_DATABASE", str(DATA_DIR / "travel_planner.sqlite3"))
)
DESTINATION_GUIDE_PATH = Path(
    os.getenv(
        "DESTINATION_GUIDE_PATH",
        str(DATA_DIR / "destination_cost_guide.txt"),
    )
)

# Exchange rates — Frankfurter serves ECB reference rates and needs no API key.
# FX_ENABLED=false keeps the bundled demonstration table (used by the tests).
FX_ENABLED: bool = os.getenv("FX_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
}
FX_RATES_URL: str = os.getenv("FX_RATES_URL", "https://api.frankfurter.dev/v1/latest")
FX_CACHE_PATH = Path(
    os.getenv("FX_CACHE_PATH", str(DATA_DIR / "exchange_rates.json"))
)
FX_REFRESH_HOURS: float = float(os.getenv("FX_REFRESH_HOURS", "6"))
FX_TIMEOUT_SECONDS: float = float(os.getenv("FX_TIMEOUT_SECONDS", "4"))

# CORS — allow the Vite dev server
_DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
]
_DEPLOYMENT_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv("TRAVEL_PLANNER_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
CORS_ORIGINS: list[str] = list(dict.fromkeys([*_DEFAULT_CORS_ORIGINS, *_DEPLOYMENT_ORIGINS]))
