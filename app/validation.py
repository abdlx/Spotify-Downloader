from __future__ import annotations

import json

from app.db import connect, emit_event, transaction, utcnow
from app.logging_config import redact_secrets
from app.repository import get_job, new_id


RECOVERY_PLAYLIST_ID = "54lC3gzGGfunn44zJkMHC6"
EXPECTED_TITLES = {
    162: "Count on Me", 58: "Tum Hi Ho", 17: "Tujh Mein Rab Dikhta Hai",
    144: "Pehli Dafa", 49: "Duniyaa", 1: "Paparazzi", 2: "let it happen",
    3: "Into Your Arms x Alone", 4: "Reflections", 9: "Self Aware",
}
SAMPLE = (
    (162, "mainstream: Count on Me"),
    (58, "mainstream: Tum Hi Ho"),
    (17, "mainstream: Tujh Mein Rab Dikhta Hai"),
    (144, "mainstream: Pehli Dafa"),
    (49, "mainstream: Duniyaa"),
    (1, "modified: hoodtrap"),
    (2, "modified: slowed and pitched down"),
    (3, "modified: mashup"),
    (4, "modified: sped up"),
    (9, "modified: slowed and reverb"),
)


def validation_plan(source_job_id: str) -> dict | None:
    conn = connect()
    try:
        source = conn.execute("""SELECT j.id, j.status, j.total, j.collection_id, c.source_id
                                 FROM jobs j JOIN collections c ON c.id=j.collection_id
                                 WHERE j.id=? AND j.kind='DOWNLOAD'""", (source_job_id,)).fetchone()
        if not source:
            return None
        rows = conn.execute("""SELECT ji.position, ji.status, ji.error_code, ji.track_id,
                                      t.title, t.artists_json, t.source_id AS spotify_track_id
                               FROM job_items ji JOIN tracks t ON t.id=ji.track_id
                               WHERE ji.job_id=?""", (source_job_id,)).fetchall()
        by_position = {row["position"]: row for row in rows}
        tracks = []
        for position, label in SAMPLE:
            row = by_position.get(position)
            tracks.append({"position": position, "reason": label,
                           "status": row["status"] if row else "MISSING",
                           "title": row["title"] if row else None,
                           "artists": json.loads(row["artists_json"]) if row else [],
                           "spotify_track_id": row["spotify_track_id"] if row else None,
                           "previous_category": row["error_code"] if row else None})
        ready = (source["source_id"] == RECOVERY_PLAYLIST_ID and source["total"] == 162
                 and source["status"] == "COMPLETE_WITH_ERRORS"
                 and all(track["status"] == "FAILED" and
                         EXPECTED_TITLES[track["position"]].casefold() in (track["title"] or "").casefold()
                         for track in tracks))
        return {"source_job_id": source_job_id, "ready": ready, "tracks": tracks,
                "count": len(tracks), "download_scope": "these ten positions only"}
    finally:
        conn.close()


def create_validation_sample(source_job_id: str) -> dict:
    plan = validation_plan(source_job_id)
    if plan is None:
        raise KeyError("source job")
    if not plan["ready"]:
        raise ValueError("The ten-track plan requires the completed 162-track recovery job and ten previously failed positions.")
    positions = [row["position"] for row in plan["tracks"]]
    now = utcnow()
    with transaction(immediate=True) as conn:
        source = conn.execute("SELECT collection_id, created_at FROM jobs WHERE id=?", (source_job_id,)).fetchone()
        previous = conn.execute("""SELECT id FROM jobs WHERE collection_id=? AND kind='DOWNLOAD'
                                   AND total=10 AND created_at>? ORDER BY created_at DESC""",
                                (source["collection_id"], source["created_at"])).fetchall()
        for job in previous:
            prior_positions = [row["position"] for row in conn.execute(
                "SELECT position FROM job_items WHERE job_id=? ORDER BY position", (job["id"],))]
            if prior_positions == sorted(positions):
                return get_job(job["id"], include_items=False)
        active = conn.execute("""SELECT id FROM jobs WHERE collection_id=? AND kind='DOWNLOAD'
                                 AND status IN ('QUEUED','DOWNLOADING','PAUSED','CANCEL_REQUESTED')
                                 LIMIT 1""", (source["collection_id"],)).fetchone()
        if active:
            raise ValueError("An active download job already exists for the recovery playlist.")
        items = conn.execute("""SELECT track_id, position FROM job_items WHERE job_id=?
                                AND status='FAILED' AND position IN (?,?,?,?,?,?,?,?,?,?)""",
                             (source_job_id, *positions)).fetchall()
        if len(items) != 10:
            raise ValueError("One or more planned tracks are no longer failed; refresh the plan.")
        job_id = new_id()
        conn.execute("""INSERT INTO jobs(id,collection_id,kind,status,total,created_at,updated_at)
                        VALUES(?,?,'DOWNLOAD','QUEUED',10,?,?)""",
                     (job_id, source["collection_id"], now, now))
        conn.executemany("""INSERT INTO job_items(id,job_id,track_id,position,status,created_at,updated_at)
                            VALUES(?,?,?,?,'QUEUED',?,?)""",
                         [(new_id(), job_id, row["track_id"], row["position"], now, now)
                          for row in items])
    emit_event("job.queued", collection_id=source["collection_id"], job_id=job_id,
               payload={"total": 10, "validation_of": source_job_id})
    return get_job(job_id, include_items=False)


def validation_result(job_id: str) -> dict | None:
    conn = connect()
    try:
        job = conn.execute("""SELECT j.*, c.source_id AS collection_source_id
                              FROM jobs j JOIN collections c ON c.id=j.collection_id
                              WHERE j.id=? AND j.kind='DOWNLOAD' AND j.total=10""", (job_id,)).fetchone()
        if not job or job["collection_source_id"] != RECOVERY_PLAYLIST_ID:
            return None
        rows = conn.execute("""SELECT ji.*, t.title, t.artists_json, t.source_url,
                                    da.search_query, da.youtube_url, da.youtube_video_id,
                                    da.match_confidence, da.pipeline_stage,
                                    da.normalized_failure_category, da.raw_error, da.raw_ytdlp_error
                             FROM job_items ji JOIN tracks t ON t.id=ji.track_id
                             LEFT JOIN download_attempts da ON da.job_item_id=ji.id AND da.attempt=ji.attempt
                             WHERE ji.job_id=? ORDER BY ji.position""", (job_id,)).fetchall()
        tracks = []
        categories = {}
        for row in rows:
            transcode_seen = bool(conn.execute(
                "SELECT 1 FROM events WHERE job_id=? AND track_id=? AND type='track.transcoding' LIMIT 1",
                (job_id, row["track_id"])).fetchone())
            success = row["status"] in ("COMPLETE", "COMPLETE_WITH_WARNINGS")
            failure = row["status"] == "FAILED"
            category = row["normalized_failure_category"] or row["error_code"]
            if failure:
                categories[category or "UNKNOWN"] = categories.get(category or "UNKNOWN", 0) + 1
            if success and not row["youtube_url"]:
                ytdlp_result = ffmpeg_result = "REUSED_EXISTING_FILE"
            elif success:
                ytdlp_result = "SUCCESS"
                ffmpeg_result = "SUCCESS" if transcode_seen else "NOT_OBSERVED_OR_SKIPPED"
            elif not row["youtube_url"] or row["pipeline_stage"] in ("SEARCHING", "PREPARING"):
                ytdlp_result, ffmpeg_result = "NOT_RUN", "NOT_RUN"
            else:
                ytdlp_result = "FAILED" if row["raw_ytdlp_error"] else "UNKNOWN"
                ffmpeg_result = "FAILED" if row["pipeline_stage"] == "TRANSCODING" else "NOT_RUN"
            tracks.append({
                "spotify_track": row["title"], "spotify_url": row["source_url"],
                "artists": json.loads(row["artists_json"]), "position": row["position"],
                "search_query": row["search_query"], "matched_youtube_candidate": row["youtube_url"],
                "youtube_video_id": row["youtube_video_id"], "match_confidence": row["match_confidence"],
                "pipeline_stage": row["pipeline_stage"], "attempts": row["attempt"],
                "yt_dlp_result": ytdlp_result, "ffmpeg_result": ffmpeg_result,
                "final_result": row["status"], "failure_category": category if failure else None,
                "raw_error_tail": redact_secrets(row["raw_error"])[-800:] if row["raw_error"] else None,
            })
        return {"job_id": job_id, "status": job["status"], "tested": len(rows),
                "successful": sum(item["final_result"] in ("COMPLETE", "COMPLETE_WITH_WARNINGS") for item in tracks),
                "failed": sum(item["final_result"] == "FAILED" for item in tracks),
                "pending": sum(item["final_result"] not in ("COMPLETE", "COMPLETE_WITH_WARNINGS", "FAILED") for item in tracks),
                "failure_categories": categories, "tracks": tracks}
    finally:
        conn.close()
