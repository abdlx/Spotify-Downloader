from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import requests

from app.config import MAX_ATTEMPTS
from app.diagnostics import record_attempt, retry_at
from app.core import classify_error
from app.db import connect, emit_event, get_settings, init_db, json_value, transaction, utcnow
from app.engine.filesystem import ensure_capacity, expected_output_path, reuse_file
from app.engine.matcher import MatchResult, MusicMatcher, proxy_with_credentials
from app.engine.spotdl_adapter import SpotdlAdapter
from app.logging_config import configure_logging, redact_secrets
from app.repository import existing_completed_path, new_id, update_job_counts


logger = logging.getLogger("download")


def _load(item_id: str) -> dict:
    conn = connect()
    try:
        row = conn.execute(
            """SELECT ji.*, j.collection_id, j.total AS job_total, j.status AS job_status,
                      c.name AS collection_name, c.artwork_url AS collection_artwork_url, t.*
               FROM job_items ji JOIN jobs j ON j.id=ji.job_id
               JOIN collections c ON c.id=j.collection_id JOIN tracks t ON t.id=ji.track_id
               WHERE ji.id=?""", (item_id,)
        ).fetchone()
        if not row:
            raise LookupError("Job item not found")
        data = dict(row)
        data["artists"] = json.loads(data.pop("artists_json"))
        data["genres"] = json.loads(data.pop("genres_json"))
        data["metadata"] = json.loads(data.pop("metadata_json"))
        return data
    finally:
        conn.close()


def _set_state(item: dict, status: str, progress: float, *, error_code=None, error=None, output_path=None, warning=None) -> None:
    with transaction(immediate=True) as conn:
        conn.execute(
            """UPDATE job_items SET status=?, progress=?, error_code=?, error=?,
               output_path=COALESCE(?, output_path), warning=COALESCE(?, warning), updated_at=? WHERE id=?""",
            (status, progress, error_code, error, output_path, warning, utcnow(), item["id"]),
        )
    event_name = "track.download.progress"
    if status == "COMPLETE": event_name = "track.complete"
    elif status == "COMPLETE_WITH_WARNINGS": event_name = "track.complete"
    elif status == "FAILED": event_name = "track.failed"
    elif status == "SEARCHING": event_name = "track.matching"
    elif status == "MATCHED": event_name = "track.matched"
    elif status == "DOWNLOADING": event_name = "track.download.started"
    elif status == "TRANSCODING": event_name = "track.transcoding"
    elif status == "TAGGING": event_name = "track.tagging"
    emit_event(event_name, collection_id=item["collection_id"], job_id=item["job_id"], track_id=item["track_id"], payload={"status": status, "progress": progress, "error": error, "warning": warning})


def _cover_warning(
    path: Path,
    artwork_url: str | None,
    settings: dict,
    collection_artwork_url: str | None = None,
) -> str | None:
    if not artwork_url:
        return "Spotify did not provide artwork for this track."
    try:
        from mutagen.id3 import ID3
        tags = ID3(path)
        if not tags.getall("APIC"):
            return "Audio completed, but cover artwork could not be embedded."
    except Exception:
        return "Audio completed, but embedded artwork could not be verified."
    if settings.get("save_cover"):
        cover = path.parent / "cover.jpg"
        if not cover.exists():
            temporary_cover = cover.with_name(f".cover-{os.getpid()}.tmp")
            try:
                proxy = proxy_with_credentials(settings)
                proxies = {"http": proxy, "https": proxy} if proxy else None
                response = requests.get(
                    collection_artwork_url or artwork_url, timeout=15, proxies=proxies
                )
                response.raise_for_status()
                temporary_cover.write_bytes(response.content)
                os.replace(temporary_cover, cover)
            except Exception:
                return "Artwork is embedded, but cover.jpg could not be saved."
            finally:
                temporary_cover.unlink(missing_ok=True)
    return None


def run(item_id: str) -> int:
    item = _load(item_id)
    settings = get_settings(include_secret=True)
    song = SpotdlAdapter.record_to_song(item, list_name=item["collection_name"], position=item["position"], list_length=item["job_total"])
    stage = "PREPARING"
    match = None
    query = f"{', '.join(song.artists)} - {song.name}"
    record_attempt(item, stage, search_query=query)
    try:
        ensure_capacity()
        target = expected_output_path(song, settings["filename_template"])
        duplicate = existing_completed_path(item["track_id"], item_id)
        if duplicate:
            path = reuse_file(duplicate, target)
            record_attempt(item, "COMPLETE", search_query=query)
            _set_state(item, "COMPLETE", 100, output_path=str(path))
            update_job_counts(item["job_id"])
            return 0

        stage = "SEARCHING"
        record_attempt(item, stage, search_query=query)
        _set_state(item, "SEARCHING", 5)
        conn = connect()
        try:
            excluded = {row["youtube_video_id"] for row in conn.execute(
                "SELECT youtube_video_id FROM download_attempts WHERE job_item_id=? AND normalized_failure_category='YOUTUBE_UNAVAILABLE' AND youtube_video_id IS NOT NULL",
                (item_id,),
            )}
            previous = conn.execute(
                """SELECT da.*, m.title AS youtube_title, m.channel, m.duration_ms AS youtube_duration_ms,
                          m.metadata_json AS match_metadata_json
                   FROM download_attempts da LEFT JOIN matches m ON m.track_id=da.track_id
                     AND m.provider_id=da.youtube_video_id
                   WHERE da.job_item_id=? AND da.attempt<? AND da.youtube_video_id IS NOT NULL
                   ORDER BY da.attempt DESC, m.updated_at DESC LIMIT 1""",
                (item_id, item["attempt"]),
            ).fetchone()
        finally:
            conn.close()
        if previous and previous["normalized_failure_category"] in {"NETWORK_ERROR", "TIMEOUT", "HTTP_429", "BOT_DETECTION", "YOUTUBE_RATE_LIMIT"} and previous["youtube_title"]:
            match = MatchResult(
                provider="youtube", url=previous["youtube_url"], video_id=previous["youtube_video_id"],
                title=previous["youtube_title"], channel=previous["channel"],
                duration_ms=previous["youtube_duration_ms"] or 0,
                confidence=previous["match_confidence"],
                upstream_score=json.loads(previous["match_metadata_json"]).get("upstream_score", 0),
                search_query=previous["search_query"] or query,
            )
        else:
            match = MusicMatcher().find_match(song, settings, exclude_video_ids=excluded)
        query = match.search_query
        record_attempt(item, "MATCHED", match=match)
        with transaction(immediate=True) as conn:
            conn.execute(
                """INSERT INTO matches(id, track_id, provider, provider_id, url, title, channel,
                   duration_ms, confidence, metadata_json, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_id(), item["track_id"], match.provider, match.video_id, match.url, match.title,
                 match.channel, match.duration_ms, match.confidence, json_value({"upstream_score": match.upstream_score}), utcnow(), utcnow()),
            )
        song.download_url = match.url
        stage = "MATCHED"
        _set_state(item, "MATCHED", 20)

        def progress(message: str, value: float) -> None:
            status = {"Downloading": "DOWNLOADING", "Converting": "TRANSCODING", "Embedding metadata": "TAGGING", "Getting audio meta": "DOWNLOADING"}.get(message)
            if status:
                nonlocal stage
                stage = status
                record_attempt(item, stage, match=match)
                _set_state(item, status, value)

        stage = "DOWNLOADING"
        record_attempt(item, stage, match=match)
        _set_state(item, "DOWNLOADING", 30)
        path = SpotdlAdapter.download(song, settings, progress)
        warning = _cover_warning(
            path,
            item.get("artwork_url"),
            settings,
            item.get("collection_artwork_url"),
        )
        if match.confidence < float(settings.get("low_confidence_threshold", 0.72)):
            review = f"Low-confidence match ({round(match.confidence * 100)}%). Review this track."
            warning = f"{review} {warning}" if warning else review
        record_attempt(item, "COMPLETE_WITH_WARNINGS" if warning else "COMPLETE", match=match)
        _set_state(item, "COMPLETE_WITH_WARNINGS" if warning else "COMPLETE", 100, output_path=str(path), warning=warning)
        update_job_counts(item["job_id"])
        return 0
    except Exception as exc:
        code, friendly = classify_error(exc)
        record_attempt(item, getattr(exc, "stage", None) or stage, match=match, search_query=query,
                       category=code, error=exc)
        attempt = int(item["attempt"])
        if code == "DISK_FULL":
            with transaction(immediate=True) as conn:
                conn.execute("UPDATE jobs SET status='PAUSED', error_code=?, error=?, updated_at=? WHERE id=?", (code, friendly, utcnow(), item["job_id"]))
                conn.execute(
                    """UPDATE job_items SET status='QUEUED', progress=0, error_code=?, error=?,
                       next_attempt_at=NULL, updated_at=? WHERE id=?""",
                    (code, friendly, utcnow(), item_id),
                )
            emit_event(
                "job.pause",
                collection_id=item["collection_id"],
                job_id=item["job_id"],
                track_id=item["track_id"],
                payload={"code": code, "message": friendly},
            )
            update_job_counts(item["job_id"])
            logger.warning(
                "download paused because storage is low",
                extra={"job_id": item["job_id"], "track_id": item["track_id"], "item_id": item_id, "error_code": code},
            )
            return 1
        retry = retry_at(code, attempt) if attempt < MAX_ATTEMPTS else None
        if retry:
            next_at, delay = retry
            with transaction(immediate=True) as conn:
                conn.execute("UPDATE job_items SET status='QUEUED', progress=0, error_code=?, error=?, next_attempt_at=?, updated_at=? WHERE id=?", (code, friendly, next_at, utcnow(), item_id))
            emit_event("track.retry_scheduled", collection_id=item["collection_id"], job_id=item["job_id"], track_id=item["track_id"], payload={"attempt": attempt, "delay_seconds": delay, "code": code})
        else:
            _set_state(item, "FAILED", 100, error_code=code, error=friendly)
        update_job_counts(item["job_id"])
        logger.exception("download failed", extra={"job_id": item["job_id"], "track_id": item["track_id"], "item_id": item_id, "error_code": code})
        return 1


if __name__ == "__main__":
    configure_logging()
    init_db()
    raise SystemExit(run(sys.argv[1]))
