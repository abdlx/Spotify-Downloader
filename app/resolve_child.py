from __future__ import annotations

import logging
import sys

from app.core import classify_error
from app.db import connect, emit_event, init_db, transaction, utcnow
from app.engine.spotdl_adapter import SpotdlAdapter
from app.logging_config import configure_logging, redact_secrets
from app.repository import upsert_track


logger = logging.getLogger("resolver")


def run(job_id: str) -> int:
    conn = connect()
    try:
        job = conn.execute(
            """SELECT j.*, c.source_url, c.type FROM jobs j JOIN collections c ON c.id=j.collection_id
               WHERE j.id=? AND j.kind='RESOLVE'""", (job_id,)
        ).fetchone()
    finally:
        conn.close()
    if not job:
        return 2
    collection_id = job["collection_id"]
    try:
        adapter = SpotdlAdapter()
        discovery = adapter.discover(job["source_url"], job["type"])
        now = utcnow()
        with transaction(immediate=True) as conn:
            conn.execute(
                """UPDATE collections SET name=?, artwork_url=?, description=?, track_count=?,
                   status='RESOLVING', error=NULL, error_code=NULL, updated_at=? WHERE id=?""",
                (discovery.name, discovery.artwork_url, discovery.description, discovery.total, now, collection_id),
            )
            conn.execute("UPDATE jobs SET total=?, status='RESOLVING', started_at=COALESCE(started_at, ?), updated_at=? WHERE id=?", (discovery.total, now, now, job_id))
        resolved = 0
        for position, song in discovery.songs:
            with transaction(immediate=True) as conn:
                track_id = upsert_track(conn, song, collection_id, position)
                resolved = conn.execute(
                    "SELECT COUNT(*) FROM collection_tracks WHERE collection_id=? AND status='RESOLVED'",
                    (collection_id,),
                ).fetchone()[0]
                conn.execute("UPDATE collections SET resolved_count=?, updated_at=? WHERE id=?", (resolved, utcnow(), collection_id))
                conn.execute("UPDATE jobs SET completed=?, updated_at=? WHERE id=?", (resolved, utcnow(), job_id))
            emit_event("track.resolved", collection_id=collection_id, job_id=job_id, track_id=track_id, payload={"position": position, "resolved": resolved, "total": discovery.total})
        now = utcnow()
        with transaction(immediate=True) as conn:
            conn.execute(
                "DELETE FROM collection_tracks WHERE collection_id=? AND status='DISCOVERED'",
                (collection_id,),
            )
            conn.execute("UPDATE collections SET status='READY', resolved_count=?, updated_at=? WHERE id=?", (resolved, now, collection_id))
            conn.execute("UPDATE jobs SET status='COMPLETE', completed=?, completed_at=?, updated_at=? WHERE id=?", (resolved, now, now, job_id))
        emit_event("collection.resolved", collection_id=collection_id, job_id=job_id, payload={"resolved": resolved, "total": discovery.total})
        return 0
    except Exception as exc:
        code, friendly = classify_error(exc)
        if code not in {"NETWORK_ERROR", "HTTP_429", "TIMEOUT"}:
            code, friendly = "COLLECTION_RESOLUTION_FAILED", "Spotify metadata could not be resolved. Check the link and your connection."
        now = utcnow()
        with transaction(immediate=True) as conn:
            conn.execute("UPDATE collections SET status='FAILED', error_code=?, error=?, updated_at=? WHERE id=?", (code, friendly, now, collection_id))
            conn.execute("UPDATE jobs SET status='FAILED', error_code=?, error=?, completed_at=?, updated_at=? WHERE id=?", (code, redact_secrets(exc)[:2000], now, now, job_id))
        emit_event("collection.failed", collection_id=collection_id, job_id=job_id, payload={"code": code, "message": friendly})
        logger.exception("resolution failed", extra={"job_id": job_id, "error_code": code})
        return 1


if __name__ == "__main__":
    configure_logging()
    init_db()
    raise SystemExit(run(sys.argv[1]))
