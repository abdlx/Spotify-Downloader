from __future__ import annotations

import random
import re
from datetime import UTC, datetime, timedelta

from app.db import json_value, transaction, utcnow
from app.logging_config import redact_secrets


RETRYABLE = {"NETWORK_ERROR", "TIMEOUT", "HTTP_429", "YOUTUBE_UNAVAILABLE"}


def retry_at(category: str, attempt: int, *, random_fraction: float | None = None) -> tuple[str, int] | None:
    if category not in RETRYABLE:
        return None
    base = (60, 180, 600) if category == "HTTP_429" else ((5, 15, 30) if category == "YOUTUBE_UNAVAILABLE" else (10, 30, 90))
    seconds = base[min(max(attempt - 1, 0), len(base) - 1)]
    jitter = random.random() if random_fraction is None else random_fraction
    delay = round(seconds * (1 + 0.25 * jitter))
    return (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(), delay


def record_attempt(item: dict, stage: str, *, match=None, search_query: str | None = None,
                   category: str | None = None, error: BaseException | None = None,
                   subprocess_exit_code: int | None = None) -> None:
    raw = redact_secrets(getattr(error, "raw_error", None) or error) if error else None
    spotdl = redact_secrets(getattr(error, "spotdl_error", "")) if error else None
    ytdlp = redact_secrets(getattr(error, "ytdlp_error", "")) if error else None
    ffmpeg = redact_secrets(getattr(error, "ffmpeg_error", "")) if error else None
    exit_code = subprocess_exit_code if subprocess_exit_code is not None else getattr(error, "exit_code", None)
    status_match = re.search(r"(?<!\d)(403|429)(?!\d)", raw or "")
    http_status = int(status_match.group(1)) if status_match else None
    now = utcnow()
    with transaction(immediate=True) as conn:
        conn.execute("""INSERT INTO download_attempts (
            job_item_id, track_id, attempt, spotify_track_id, spotify_url, title, artists_json,
            duration_ms, youtube_url, youtube_video_id, search_query, match_confidence,
            pipeline_stage, normalized_failure_category, raw_error, raw_spotdl_error,
            raw_ytdlp_error, raw_ffmpeg_error, subprocess_exit_code, http_status, started_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(job_item_id, attempt) DO UPDATE SET
            youtube_url=COALESCE(excluded.youtube_url, download_attempts.youtube_url),
            youtube_video_id=COALESCE(excluded.youtube_video_id, download_attempts.youtube_video_id),
            search_query=COALESCE(excluded.search_query, download_attempts.search_query),
            match_confidence=COALESCE(excluded.match_confidence, download_attempts.match_confidence),
            pipeline_stage=excluded.pipeline_stage,
            normalized_failure_category=excluded.normalized_failure_category,
            raw_error=excluded.raw_error, raw_spotdl_error=excluded.raw_spotdl_error,
            raw_ytdlp_error=excluded.raw_ytdlp_error, raw_ffmpeg_error=excluded.raw_ffmpeg_error,
            subprocess_exit_code=excluded.subprocess_exit_code, http_status=excluded.http_status,
            updated_at=excluded.updated_at""", (
            item["id"], item["track_id"], int(item["attempt"]), item["source_id"], item["source_url"],
            item["title"], json_value(item["artists"]), item.get("duration_ms"),
            getattr(match, "url", None), getattr(match, "video_id", None),
            search_query or getattr(match, "search_query", None), getattr(match, "confidence", None),
            stage, category, raw, spotdl, ytdlp, ffmpeg, exit_code, http_status, now, now,
        ))
