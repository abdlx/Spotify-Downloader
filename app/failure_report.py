from __future__ import annotations

import importlib.metadata
import json
import shutil
import subprocess
from collections import Counter

from app.core import classify_error
from app.db import connect
from app.logging_config import redact_secrets


MAINSTREAM = ("Count on Me", "Tum Hi Ho", "Tujh Mein Rab Dikhta Hai", "Kesariya", "Afsanay")


def _preview(value: str | None, limit: int = 1000) -> str | None:
    return redact_secrets(value)[-limit:] if value else None


def build_failure_report(job_id: str | None = None, *, examples: int = 15) -> dict | None:
    conn = connect()
    try:
        job = conn.execute("SELECT * FROM jobs WHERE id=? AND kind='DOWNLOAD'" if job_id else
                           "SELECT * FROM jobs WHERE kind='DOWNLOAD' ORDER BY created_at DESC LIMIT 1",
                           (job_id,) if job_id else ()).fetchone()
        if not job:
            return None
        items = conn.execute("""SELECT ji.*, t.source_id, t.title, t.artists_json,
                                    da.pipeline_stage, da.normalized_failure_category,
                                    da.search_query, da.youtube_url, da.youtube_video_id,
                                    da.match_confidence, da.raw_error, da.raw_spotdl_error,
                                    da.raw_ytdlp_error, da.raw_ffmpeg_error,
                                    da.subprocess_exit_code, da.http_status
                             FROM job_items ji JOIN tracks t ON t.id=ji.track_id
                             LEFT JOIN download_attempts da ON da.job_item_id=ji.id AND da.attempt=ji.attempt
                             WHERE ji.job_id=? ORDER BY ji.position""", (job["id"],)).fetchall()
        failures = [row for row in items if row["status"] == "FAILED"]
        codes = Counter(row["error_code"] or "NONE" for row in failures)
        categories = Counter(row["normalized_failure_category"] or "NO_ATTEMPT_DIAGNOSTIC" for row in failures)
        inferred_categories = Counter(classify_error(RuntimeError(row["raw_error"]))[0]
                                      if row["raw_error"] else "NO_RAW_ERROR" for row in failures)
        stages = Counter(row["pipeline_stage"] or "UNKNOWN" for row in failures)
        priority = [row for row in failures if any(name.lower() in row["title"].lower() for name in MAINSTREAM)]
        priority_ids = {row["id"] for row in priority}
        for row in failures + list(reversed(failures)):
            category = row["normalized_failure_category"] or row["error_code"] or "UNKNOWN"
            if category not in {item["normalized_failure_category"] or item["error_code"] or "UNKNOWN" for item in priority}:
                priority.append(row)
                priority_ids.add(row["id"])
        priority.extend(row for row in failures if row["id"] not in priority_ids)
        sample = []
        for row in priority[:examples]:
            sample.append({
                "position": row["position"], "title": row["title"],
                "artists": json.loads(row["artists_json"]), "spotify_track_id": row["source_id"],
                "attempt": row["attempt"], "stage": row["pipeline_stage"],
                "category": row["normalized_failure_category"] or row["error_code"],
                "category_inferred_from_raw": classify_error(RuntimeError(row["raw_error"]))[0]
                if row["raw_error"] else None,
                "youtube_url": row["youtube_url"], "search_query": row["search_query"],
                "confidence": row["match_confidence"], "http_status": row["http_status"],
                "exit_code": row["subprocess_exit_code"],
                "raw_error_tail": _preview(row["raw_error"]),
                "spotdl_error_tail": _preview(row["raw_spotdl_error"], 500),
                "yt_dlp_error_tail": _preview(row["raw_ytdlp_error"], 500),
                "ffmpeg_error_tail": _preview(row["raw_ffmpeg_error"], 500),
            })
        versions = {}
        for package in ("spotdl", "yt-dlp", "yt-dlp-ejs"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = "missing"
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            try:
                result = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=5)
                versions["ffmpeg"] = result.stdout.splitlines()[0] if result.stdout else f"exit {result.returncode}"
            except (OSError, subprocess.TimeoutExpired):
                versions["ffmpeg"] = "unavailable"
        quartiles = Counter()
        for row in items:
            if row["status"] in ("COMPLETE", "COMPLETE_WITH_WARNINGS", "FAILED"):
                quartile = min(4, 1 + (row["position"] - 1) * 4 // max(1, job["total"]))
                quartiles[f"Q{quartile}:{row['status']}"] += 1
        return {
            "job_id": job["id"], "job_total": job["total"], "completed": job["completed"],
            "failed": len(failures),
            "failed_with_current_attempt_match": sum(bool(row["youtube_video_id"]) for row in failures),
            "job_created_at": job["created_at"], "job_completed_at": job["completed_at"],
            "runtime_versions": versions, "failure_codes": dict(codes),
            "diagnostic_categories": dict(categories),
            "categories_inferred_from_raw_errors": dict(inferred_categories),
            "failure_stages": dict(stages),
            "outcomes_by_position_quartile": dict(quartiles),
            "first_failed_at": min((row["updated_at"] for row in failures), default=None),
            "last_failed_at": max((row["updated_at"] for row in failures), default=None),
            "examples": sample,
        }
    finally:
        conn.close()
