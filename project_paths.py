"""Stable resource paths; PREDICTIONS_DB selects a disposable/test database."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = os.getenv("PREDICTIONS_DB", str(ROOT / "predictions.db"))
