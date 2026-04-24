"""FastAPI application for the Auction Analyzer dashboard."""
import sys
from pathlib import Path

from fastapi import FastAPI

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from execution.webapp.routes import auto, goods, api

app = FastAPI(title="Auction Analyzer")

# Include routers
app.include_router(auto.router)
app.include_router(goods.router)
app.include_router(api.router)
