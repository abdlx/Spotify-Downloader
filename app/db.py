from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.config import DATA_DIR, DB_PATH, DOWNLOADS_DIR


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collections (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_url TEXT NOT NULL,
  type TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT 'Resolving…',
  artwork_url TEXT,
  description TEXT,
  track_count INTEGER NOT NULL DEFAULT 0,
  resolved_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'RESOLVING',
  error_code TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_collections_source
  ON collections(source, source_id);

CREATE TABLE IF NOT EXISTS tracks (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL DEFAULT 'spotify',
  source_id TEXT NOT NULL,
  source_url TEXT NOT NULL,
  title TEXT NOT NULL,
  artists_json TEXT NOT NULL,
  album TEXT,
  album_artist TEXT,
  duration_ms INTEGER,
  track_number INTEGER,
  track_count INTEGER,
  disc_number INTEGER,
  disc_count INTEGER,
  release_date TEXT,
  isrc TEXT,
  artwork_url TEXT,
  genres_json TEXT NOT NULL DEFAULT '[]',
  explicit INTEGER,
  publisher TEXT,
  copyright TEXT,
  version_tokens_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_source_id
  ON tracks(source, source_id);
CREATE INDEX IF NOT EXISTS idx_tracks_isrc ON tracks(isrc) WHERE isrc IS NOT NULL;

CREATE TABLE IF NOT EXISTS collection_tracks (
  collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'RESOLVED',
  PRIMARY KEY(collection_id, position)
);
CREATE INDEX IF NOT EXISTS idx_collection_tracks_track ON collection_tracks(track_id);

CREATE TABLE IF NOT EXISTS matches (
  id TEXT PRIMARY KEY,
  track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  provider_id TEXT,
  url TEXT NOT NULL,
  title TEXT,
  channel TEXT,
  duration_ms INTEGER,
  confidence REAL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_matches_track ON matches(track_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  total INTEGER NOT NULL DEFAULT 0,
  completed INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_collection ON jobs(collection_id, created_at DESC);

CREATE TABLE IF NOT EXISTS job_items (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  track_id TEXT NOT NULL REFERENCES tracks(id),
  position INTEGER NOT NULL,
  status TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0,
  progress REAL NOT NULL DEFAULT 0,
  output_path TEXT,
  warning TEXT,
  error_code TEXT,
  error TEXT,
  next_attempt_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(job_id, position)
);
CREATE INDEX IF NOT EXISTS idx_job_items_queue
  ON job_items(status, next_attempt_at, created_at);
CREATE INDEX IF NOT EXISTS idx_job_items_job ON job_items(job_id, position);
CREATE INDEX IF NOT EXISTS idx_job_items_track ON job_items(track_id, status);

CREATE TABLE IF NOT EXISTS download_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_item_id TEXT NOT NULL REFERENCES job_items(id) ON DELETE CASCADE,
  track_id TEXT NOT NULL REFERENCES tracks(id),
  attempt INTEGER NOT NULL,
  spotify_track_id TEXT NOT NULL,
  spotify_url TEXT NOT NULL,
  title TEXT NOT NULL,
  artists_json TEXT NOT NULL,
  duration_ms INTEGER,
  youtube_url TEXT,
  youtube_video_id TEXT,
  search_query TEXT,
  match_confidence REAL,
  pipeline_stage TEXT NOT NULL,
  normalized_failure_category TEXT,
  raw_error TEXT,
  raw_spotdl_error TEXT,
  raw_ytdlp_error TEXT,
  raw_ffmpeg_error TEXT,
  subprocess_exit_code INTEGER,
  http_status INTEGER,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(job_item_id, attempt)
);
CREATE INDEX IF NOT EXISTS idx_download_attempts_category
  ON download_attempts(normalized_failure_category, started_at);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  collection_id TEXT,
  job_id TEXT,
  track_id TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(id);

CREATE TABLE IF NOT EXISTS worker_heartbeat (
  worker_id TEXT PRIMARY KEY,
  pid INTEGER,
  version TEXT,
  updated_at TEXT NOT NULL
);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "filename_template": "{list-name}/{list-position} - {artist} - {title}.{output-ext}",
    "format": "mp3",
    "bitrate": "auto",
    "concurrency": 2,
    "prefer_official": True,
    "duration_tolerance_seconds": 12,
    "low_confidence_threshold": 0.72,
    "save_cover": True,
    "network_mode": "direct",
    "proxy_url": "",
    "proxy_username": "",
    "proxy_password": "",
}


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def transaction(immediate: bool = False) -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        now = utcnow()
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)",
            (now,),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO settings(key, value_json, updated_at) VALUES(?, ?, ?)",
            [(key, json.dumps(value), now) for key, value in DEFAULT_SETTINGS.items()],
        )
    finally:
        conn.close()


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def emit_event(
    event_type: str,
    *,
    collection_id: str | None = None,
    job_id: str | None = None,
    track_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    conn = connect()
    try:
        cursor = conn.execute(
            """INSERT INTO events(type, collection_id, job_id, track_id, payload_json, created_at)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (event_type, collection_id, job_id, track_id, json_value(payload or {}), utcnow()),
        )
        return int(cursor.lastrowid)
    finally:
        conn.close()


def get_settings(*, include_secret: bool = False) -> dict[str, Any]:
    conn = connect()
    try:
        result = {row["key"]: json.loads(row["value_json"]) for row in conn.execute("SELECT key, value_json FROM settings")}
    finally:
        conn.close()
    if not include_secret:
        secret = str(result.get("proxy_password", ""))
        result["proxy_password"] = ""
        result["proxy_password_configured"] = bool(secret)
    return result
