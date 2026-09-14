from app.core import parse_spotify_url
from app.db import connect, transaction, utcnow
from app.repository import create_download_job, create_or_restart_resolution, upsert_track
from app.worker import recover
from tests.test_repository import song


def test_restart_requeues_only_incomplete_work(isolated_db):
    collection, _ = create_or_restart_resolution(parse_spotify_url("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy"))
    with transaction(immediate=True) as conn:
        for position in range(1, 4):
            upsert_track(conn, song(position), collection["id"], position)
        conn.execute("UPDATE collections SET status='READY', track_count=3, resolved_count=3 WHERE id=?", (collection["id"],))
    job = create_download_job(collection["id"])
    conn = connect()
    items = conn.execute("SELECT id FROM job_items WHERE job_id=? ORDER BY position", (job["id"],)).fetchall()
    conn.execute("UPDATE jobs SET status='DOWNLOADING' WHERE id=?", (job["id"],))
    conn.execute("UPDATE job_items SET status='COMPLETE', progress=100 WHERE id=?", (items[0]["id"],))
    conn.execute("UPDATE job_items SET status='TRANSCODING', progress=80 WHERE id=?", (items[1]["id"],))
    conn.close()
    recover()
    conn = connect()
    states = [row["status"] for row in conn.execute("SELECT status FROM job_items WHERE job_id=? ORDER BY position", (job["id"],))]
    conn.close()
    assert states == ["COMPLETE", "QUEUED", "QUEUED"]

