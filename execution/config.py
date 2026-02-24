import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Database
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'data' / 'auction_analyzer.db'}")

# Scraping delays (seconds)
SCRAPING_DELAY_SECONDS = float(os.getenv("SCRAPING_DELAY_SECONDS", "2.5"))
AUTOSCOUT24_DELAY_SECONDS = float(os.getenv("AUTOSCOUT24_DELAY_SECONDS", "3.0"))

# Analysis
AUCTION_DISCOUNT = float(os.getenv("AUCTION_DISCOUNT", "0.20"))
LOW_COMPETITION_THRESHOLD = int(os.getenv("LOW_COMPETITION_THRESHOLD", "5"))
MILEAGE_ADJUSTMENT_PER_KM = float(os.getenv("MILEAGE_ADJUSTMENT_PER_KM", "0.05"))

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Debug
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# Directories
TMP_DIR = PROJECT_ROOT / ".tmp"
DATA_DIR = PROJECT_ROOT / "data"
TMP_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
