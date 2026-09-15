from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core import SpotifyReference, extract_version_tokens
from app.db import connect, emit_event, json_value, transaction, utcnow


TERMINAL_ITEM_STATES = ("COMPLETE", "COMPLETE_WITH_WARNINGS", "FAILED", "SKIPPED", "CANCELLED")


def new_id() -> str:
    return str(uuid.uuid4())


def create_or_restart_resolution(ref: SpotifyReference) -> tuple[dict[str, Any], dict[str, Any]]:
    now = utcnow()
    with transaction(immediate=True) as conn:
        existing = conn.execute(
            "SELECT * FROM collections WHERE source='spotify' AND source_id=?", (ref.source_id,)
        ).fetchone()
        if existing and existing["status"] == "READY":
            collection = dict(existing)
            job = conn.execute(
                "SELECT * FROM jobs WHERE collection_id=? AND kind='RESOLVE' ORDER BY created_at DESC LIMIT 1",
                (collection["id"],),
            ).fetchone()
            return collection, dict(job) if job else {}
        collection_id = existing["id"] if existing else new_id()
        if existing:
            active = conn.execute(
                """SELECT * FROM jobs WHERE collection_id=? AND kind='RESOLVE'
                   AND status IN ('QUEUED','RESOLVING') ORDER BY created_at DESC LIMIT 1""",
                (collection_id,),
            ).fetchone()
            if not active:
                conn.execute(
                    "UPDATE collection_tracks SET status='DISCOVERED' WHERE collection_id=?",
                    (collection_id,),
                )
            conn.execute(
                """UPDATE collections SET status='RESOLVING', track_count=CASE WHEN ? THEN 0 ELSE track_count END,
                   resolved_count=CASE WHEN ? THEN 0 ELSE resolved_count END,
                   error=NULL, error_code=NULL, updated_at=? WHERE id=?""",
                (not bool(active), not bool(active), now, collection_id),
            )
        else:
            conn.execute(
                """INSERT INTO collections
                   (id, source, source_id, source_url, type, name, status, created_at, updated_at)
                   VALUES(?, 'spotify', ?, ?, ?, 'Resolving…', 'RESOLVING', ?, ?)""",
                (collection_id, ref.source_id, ref.url, ref.kind, now, now),
            )
        queued = conn.execute(
            "SELECT * FROM jobs WHERE collection_id=? AND kind='RESOLVE' AND status IN ('QUEUED','RESOLVING') ORDER BY created_at DESC LIMIT 1",
            (collection_id,),
        ).fetchone()
        if queued:
            job_id = queued["id"]
        else:
            job_id = new_id()
            conn.execute(
                """INSERT INTO jobs(id, collection_id, kind, status, created_at, updated_at)
                   VALUES(?, ?, 'RESOLVE', 'QUEUED', ?, ?)""",
                (job_id, collection_id, now, now),
            )
        collection = dict(conn.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone())
        job = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    emit_event("collection.resolving", collection_id=collection_id, job_id=job_id, payload={"url": ref.url})
    return collection, job


def create_download_job(collection_id: str) -> dict[str, Any]:
    now = utcnow()
    with transaction(immediate=True) as conn:
        collection = conn.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()
        if not collection:
            raise KeyError("collection")
        if collection["status"] != "READY":
            raise ValueError("Collection is still resolving.")
        active = conn.execute(
            "SELECT * FROM jobs WHERE collection_id=? AND kind='DOWNLOAD' AND status IN ('QUEUED','DOWNLOADING','PAUSED','CANCEL_REQUESTED') ORDER BY created_at DESC LIMIT 1",
            (collection_id,),
        ).fetchone()
        if active:
            return dict(active)
        rows = conn.execute(
            "SELECT track_id, position FROM collection_tracks WHERE collection_id=? ORDER BY position",
            (collection_id,),
        ).fetchall()
        if not rows:
            raise ValueError("Collection contains no downloadable tracks.")
        job_id = new_id()
        conn.execute(
            """INSERT INTO jobs(id, collection_id, kind, status, total, created_at, updated_at)
               VALUES(?, ?, 'DOWNLOAD', 'QUEUED', ?, ?, ?)""",
            (job_id, collection_id, len(rows), now, now),
        )
        conn.executemany(
            """INSERT INTO job_items(id, job_id, track_id, position, status, created_at, updated_at)
               VALUES(?, ?, ?, ?, 'QUEUED', ?, ?)""",
            [(new_id(), job_id, row["track_id"], row["position"], now, now) for row in rows],
        )
        job = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    emit_event("job.queued", collection_id=collection_id, job_id=job_id, payload={"total": len(rows)})
    return job


def get_collection(collection_id: str) -> dict[str, Any] | None:
    conn = connect()
    try:
        row = conn.execute(
            """SELECT c.*,
                      (SELECT id FROM jobs j WHERE j.collection_id=c.id AND j.kind='DOWNLOAD' ORDER BY j.created_at DESC LIMIT 1) AS latest_job_id
               FROM collections c WHERE c.id=?""",
            (collection_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_collections(limit: int = 100) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT c.*,
                      (SELECT id FROM jobs j WHERE j.collection_id=c.id AND j.kind='DOWNLOAD' ORDER BY j.created_at DESC LIMIT 1) AS latest_job_id
               FROM collections c ORDER BY c.updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_collection_tracks(
    collection_id: str, offset: int, limit: int, job_id: str | None = None
) -> dict[str, Any]:
    conn = connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM collection_tracks WHERE collection_id=?", (collection_id,)).fetchone()[0]
        rows = conn.execute(
               """WITH chosen_job(id) AS (
                   SELECT COALESCE(
                     (SELECT id FROM jobs WHERE id=? AND collection_id=? AND kind='DOWNLOAD'),
                     (SELECT id FROM jobs WHERE collection_id=? AND kind='DOWNLOAD'
                      ORDER BY created_at DESC LIMIT 1)
                   )
               )
               SELECT t.*, ct.position, ji.id AS job_item_id,
                      COALESCE(ji.status, ct.status) AS status,
                      ji.progress,
                      ji.error,
                      (SELECT m.title FROM matches m WHERE m.track_id=t.id ORDER BY m.updated_at DESC LIMIT 1) AS match_title,
                      (SELECT m.channel FROM matches m WHERE m.track_id=t.id ORDER BY m.updated_at DESC LIMIT 1) AS match_channel,
                      (SELECT m.duration_ms FROM matches m WHERE m.track_id=t.id ORDER BY m.updated_at DESC LIMIT 1) AS match_duration_ms,
                      (SELECT m.confidence FROM matches m WHERE m.track_id=t.id ORDER BY m.updated_at DESC LIMIT 1) AS confidence
               FROM collection_tracks ct
               JOIN tracks t ON t.id=ct.track_id
               LEFT JOIN job_items ji ON ji.job_id=(SELECT id FROM chosen_job)
                 AND ji.track_id=t.id AND ji.position=ct.position
               WHERE ct.collection_id=? ORDER BY ct.position LIMIT ? OFFSET ?""",
            (job_id, collection_id, collection_id, collection_id, limit, offset),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["artists"] = json.loads(item.pop("artists_json"))
            item["genres"] = json.loads(item.pop("genres_json"))
            item["version_tokens"] = json.loads(item.pop("version_tokens_json"))
            item.pop("metadata_json", None)
            items.append(item)
        return {"items": items, "total": total, "offset": offset, "limit": limit}
    finally:
        conn.close()


def list_jobs(limit: int = 100) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT j.*, c.name AS collection_name, c.artwork_url, c.type AS collection_type
               FROM jobs j JOIN collections c ON c.id=j.collection_id
               WHERE j.kind='DOWNLOAD' ORDER BY j.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [enrich_job(dict(row), conn) for row in rows]
    finally:
        conn.close()


def get_job(job_id: str, *, include_items: bool = True) -> dict[str, Any] | None:
    conn = connect()
    try:
        row = conn.execute(
            """SELECT j.*, c.name AS collection_name, c.artwork_url, c.type AS collection_type
               FROM jobs j JOIN collections c ON c.id=j.collection_id WHERE j.id=?""",
            (job_id,),
        ).fetchone()
        if not row:
            return None
        job = enrich_job(dict(row), conn)
        if include_items and job["kind"] == "DOWNLOAD":
            items = conn.execute(
                """SELECT ji.*, t.title, t.artists_json, t.artwork_url, t.duration_ms
                   FROM job_items ji JOIN tracks t ON t.id=ji.track_id
                   WHERE ji.job_id=? ORDER BY ji.position""",
                (job_id,),
            ).fetchall()
            job["items"] = []
            for item_row in items:
                item = dict(item_row)
                item["artists"] = json.loads(item.pop("artists_json"))
                job["items"].append(item)
        return job
    finally:
        conn.close()


def enrich_job(job: dict[str, Any], conn=None) -> dict[str, Any]:
    own = conn is None
    conn = conn or connect()
    try:
        if job["kind"] == "DOWNLOAD":
            counts = {
                row["status"]: row["count"]
                for row in conn.execute("SELECT status, COUNT(*) AS count FROM job_items WHERE job_id=? GROUP BY status", (job["id"],))
            }
            job["completed"] = counts.get("COMPLETE", 0) + counts.get("COMPLETE_WITH_WARNINGS", 0)
            job["failed"] = counts.get("FAILED", 0)
            job["resolved"] = job["total"]
            job["matched"] = conn.execute(
                """SELECT COUNT(*) FROM job_items ji WHERE ji.job_id=?
                   AND EXISTS (SELECT 1 FROM matches m WHERE m.track_id=ji.track_id)""",
                (job["id"],),
            ).fetchone()[0]
            job["pending"] = counts.get("QUEUED", 0)
            job["downloading"] = sum(counts.get(s, 0) for s in ("SEARCHING", "MATCHED", "DOWNLOADING", "TRANSCODING", "TAGGING"))
            job["progress_percent"] = round(
                (conn.execute("SELECT COALESCE(SUM(progress),0) FROM job_items WHERE job_id=?", (job["id"],)).fetchone()[0] / max(1, job["total"])), 1
            )
        return job
    finally:
        if own:
            conn.close()


def set_job_action(job_id: str, action: str) -> dict[str, Any] | None:
    mapping = {"pause": (("QUEUED", "DOWNLOADING"), "PAUSED"), "resume": (("PAUSED",), "DOWNLOADING"), "cancel": (None, "CANCEL_REQUESTED")}
    expected, target = mapping[action]
    now = utcnow()
    with transaction(immediate=True) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=? AND kind='DOWNLOAD'", (job_id,)).fetchone()
        if not row:
            return None
        if expected and row["status"] not in expected:
            raise ValueError(f"Job cannot {action} from {row['status'].lower()} state.")
        if action == "cancel" and row["status"] in ("COMPLETE", "COMPLETE_WITH_ERRORS", "CANCELLED"):
            raise ValueError("Completed jobs cannot be cancelled.")
        if action == "resume":
            conn.execute(
                "UPDATE jobs SET status=?, error=NULL, error_code=NULL, updated_at=? WHERE id=?",
                (target, now, job_id),
            )
        else:
            conn.execute("UPDATE jobs SET status=?, updated_at=? WHERE id=?", (target, now, job_id))
        if action == "cancel":
            conn.execute("UPDATE job_items SET status='CANCELLED', updated_at=? WHERE job_id=? AND status='QUEUED'", (now, job_id))
    emit_event(f"job.{action}", job_id=job_id)
    return get_job(job_id, include_items=False)


def retry_failed(job_id: str) -> dict[str, Any] | None:
    now = utcnow()
    with transaction(immediate=True) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=? AND kind='DOWNLOAD'", (job_id,)).fetchone()
        if not row:
            return None
        failed = conn.execute(
            "SELECT COUNT(*) FROM job_items WHERE job_id=? AND status='FAILED'",
            (job_id,),
        ).fetchone()[0]
        if not failed:
            raise ValueError("Job has no failed tracks to retry.")
        conn.execute(
            """UPDATE job_items SET status='QUEUED', progress=0, error=NULL,
               error_code=NULL, next_attempt_at=NULL, updated_at=? WHERE job_id=? AND status='FAILED'""",
            (now, job_id),
        )
        conn.execute(
            """UPDATE jobs SET status='DOWNLOADING', completed_at=NULL, error=NULL,
               error_code=NULL, updated_at=? WHERE id=?""",
            (now, job_id),
        )
    emit_event("job.retry", job_id=job_id)
    return get_job(job_id, include_items=False)


def retry_item(item_id: str) -> dict[str, Any] | None:
    now = utcnow()
    with transaction(immediate=True) as conn:
        row = conn.execute(
            """SELECT ji.status, ji.job_id, ji.track_id, j.collection_id
               FROM job_items ji JOIN jobs j ON j.id=ji.job_id WHERE ji.id=?""",
            (item_id,),
        ).fetchone()
        if not row:
            return None
        if row["status"] != "FAILED":
            raise ValueError("Only failed tracks can be retried.")
        conn.execute(
            """UPDATE job_items SET status='QUEUED', progress=0, error=NULL,
               error_code=NULL, next_attempt_at=NULL, updated_at=? WHERE id=?""",
            (now, item_id),
        )
        conn.execute(
            """UPDATE jobs SET status='DOWNLOADING', completed_at=NULL, error=NULL,
               error_code=NULL, updated_at=? WHERE id=?""",
            (now, row["job_id"]),
        )
    emit_event(
        "track.retry",
        collection_id=row["collection_id"],
        job_id=row["job_id"],
        track_id=row["track_id"],
        payload={"item_id": item_id},
    )
    return get_job(row["job_id"], include_items=False)


def upsert_track(conn, song: dict[str, Any], collection_id: str, position: int) -> str:
    now = utcnow()
    existing = conn.execute("SELECT id FROM tracks WHERE source='spotify' AND source_id=?", (song["source_id"],)).fetchone()
    track_id = existing["id"] if existing else new_id()
    values = (
        song["source_id"], song["source_url"], song["title"], json_value(song.get("artists", [])),
        song.get("album"), song.get("album_artist"), song.get("duration_ms"), song.get("track_number"),
        song.get("track_count"), song.get("disc_number"), song.get("disc_count"), song.get("release_date"),
        song.get("isrc"), song.get("artwork_url"), json_value(song.get("genres", [])),
        int(bool(song.get("explicit"))) if song.get("explicit") is not None else None,
        song.get("publisher"), song.get("copyright"), json_value(extract_version_tokens(song["title"])),
        json_value(song.get("metadata", {})), now, track_id,
    )
    if existing:
        conn.execute(
            """UPDATE tracks SET source_id=?, source_url=?, title=?, artists_json=?, album=?, album_artist=?,
               duration_ms=?, track_number=?, track_count=?, disc_number=?, disc_count=?, release_date=?, isrc=?,
               artwork_url=?, genres_json=?, explicit=?, publisher=?, copyright=?, version_tokens_json=?,
               metadata_json=?, updated_at=? WHERE id=?""", values,
        )
    else:
        conn.execute(
            """INSERT INTO tracks(id, source_id, source_url, title, artists_json, album, album_artist,
               duration_ms, track_number, track_count, disc_number, disc_count, release_date, isrc, artwork_url,
               genres_json, explicit, publisher, copyright, version_tokens_json, metadata_json, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (track_id, *values[:-2], now, now),
        )
    conn.execute(
        """INSERT INTO collection_tracks(collection_id, track_id, position, status)
           VALUES(?, ?, ?, 'RESOLVED')
           ON CONFLICT(collection_id, position) DO UPDATE SET track_id=excluded.track_id, status='RESOLVED'""",
        (collection_id, track_id, position),
    )
    return track_id


def update_job_counts(job_id: str) -> None:
    now = utcnow()
    completion_payload = None
    progress_payload = None
    with transaction(immediate=True) as conn:
        row = conn.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN status IN ('COMPLETE','COMPLETE_WITH_WARNINGS') THEN 1 ELSE 0 END) completed,
                      SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) failed,
                      SUM(CASE WHEN status NOT IN ('COMPLETE','COMPLETE_WITH_WARNINGS','FAILED','SKIPPED','CANCELLED') THEN 1 ELSE 0 END) active
               FROM job_items WHERE job_id=?""",
            (job_id,),
        ).fetchone()
        if not row:
            return
        conn.execute(
            "UPDATE jobs SET total=?, completed=?, failed=?, updated_at=? WHERE id=?",
            (row["total"], row["completed"] or 0, row["failed"] or 0, now, job_id),
        )
        job = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        progress_payload = {
            "total": row["total"],
            "completed": row["completed"] or 0,
            "failed": row["failed"] or 0,
            "pending": row["active"] or 0,
        }
        if job and not row["active"] and job["status"] not in ("CANCELLED", "CANCEL_REQUESTED"):
            status = "COMPLETE_WITH_ERRORS" if row["failed"] else "COMPLETE"
            conn.execute("UPDATE jobs SET status=?, completed_at=?, updated_at=? WHERE id=?", (status, now, now, job_id))
            completion_payload = {"completed": row["completed"] or 0, "failed": row["failed"] or 0}
    if progress_payload is not None:
        emit_event("job.progress", job_id=job_id, payload=progress_payload)
    if completion_payload is not None:
        emit_event("job.complete", job_id=job_id, payload=completion_payload)


def existing_completed_path(track_id: str, excluding_item: str) -> str | None:
    conn = connect()
    try:
        row = conn.execute(
            """SELECT ji.output_path FROM tracks target
               JOIN job_items ji ON ji.id<>?
               JOIN tracks candidate ON candidate.id=ji.track_id
               WHERE target.id=?
               AND ji.status IN ('COMPLETE','COMPLETE_WITH_WARNINGS')
               AND ji.output_path IS NOT NULL
               AND (
                 candidate.id=target.id OR
                 (target.isrc IS NOT NULL AND target.isrc<>'' AND candidate.isrc=target.isrc)
               )
               ORDER BY CASE WHEN candidate.id=target.id THEN 0 ELSE 1 END, ji.updated_at DESC
               LIMIT 1""",
            (excluding_item, track_id),
        ).fetchone()
        if row and Path(row["output_path"]).is_file():
            return row["output_path"]
        return None
    finally:
        conn.close()
