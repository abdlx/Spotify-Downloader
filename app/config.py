from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", "/downloads"))
DB_PATH = Path(os.getenv("DATABASE_PATH", str(DATA_DIR / "app.db")))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
WORKER_ID = os.getenv("WORKER_ID", "worker-1")

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
USE_OFFICIAL_SPOTIFY = bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)

MAX_ATTEMPTS = 4
RETRY_DELAYS = (10, 30, 90)
