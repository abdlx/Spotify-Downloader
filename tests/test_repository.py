from __future__ import annotations

from app.core import parse_spotify_url
from app.db import connect, transaction, utcnow
from app.repository import (
    create_download_job,
    create_or_restart_resolution,
    existing_completed_path,
    list_collection_tracks,
    upsert_track,
)


def song(number: int) -> dict:
    return {
        "source_id": f"track{number:017d}", "source_url": f"https://open.spotify.com/track/track{number:017d}",
        "title": f"Track {number}", "artists": ["Fixture Artist"], "album": "Load Test",
        "album_artist": "Fixture Artist", "duration_ms": 180000, "track_number": number,
        "track_count": 750, "disc_number": 1, "disc_count": 1, "release_date": "2026-01-01",
        "isrc": None, "artwork_url": None, "genres": [], "explicit": False,
        "publisher": "Fixture", "copyright": None, "metadata": {},
    }


def test_large_collection_persists_without_truncation(isolated_db):
    collection, _ = create_or_restart_resolution(parse_spotify_url("https://open.spotify.com/playlist/37i9dQZF1DX4WYpdgoIcn6"))
    with transaction(immediate=True) as conn:
        for position in range(1, 751):
            upsert_track(conn, song(position), collection["id"], position)
        conn.execute("UPDATE collections SET status='READY', track_count=750, resolved_count=750, updated_at=? WHERE id=?", (utcnow(), collection["id"]))
    first = list_collection_tracks(collection["id"], 0, 500)
    second = list_collection_tracks(collection["id"], 500, 500)
    assert first["total"] == 750
    assert len(first["items"]) == 500
    assert len(second["items"]) == 250
    assert second["items"][-1]["position"] == 750
    job = create_download_job(collection["id"])
    conn = connect()
    assert conn.execute("SELECT COUNT(*) FROM job_items WHERE job_id=?", (job["id"],)).fetchone()[0] == 750
    conn.close()


def test_duplicate_lookup_only_reuses_existing_file(isolated_db, tmp_path):
    collection, _ = create_or_restart_resolution(parse_spotify_url("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"))
    with transaction(immediate=True) as conn:
        track_id = upsert_track(conn, song(1), collection["id"], 1)
        conn.execute("UPDATE collections SET status='READY', track_count=1, resolved_count=1 WHERE id=?", (collection["id"],))
    first = create_download_job(collection["id"])
    output = tmp_path / "song.mp3"
    output.write_bytes(b"fixture")
    conn = connect()
    item = conn.execute("SELECT id FROM job_items WHERE job_id=?", (first["id"],)).fetchone()
    conn.execute("UPDATE job_items SET status='COMPLETE', output_path=? WHERE id=?", (str(output), item["id"]))
    conn.close()
    assert existing_completed_path(track_id, "different-item") == str(output)
    output.unlink()
    assert existing_completed_path(track_id, "different-item") is None


def test_duplicate_lookup_can_reuse_same_isrc(isolated_db, tmp_path):
    first_ref = parse_spotify_url("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy")
    second_ref = parse_spotify_url("https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX")
    first_collection, _ = create_or_restart_resolution(first_ref)
    second_collection, _ = create_or_restart_resolution(second_ref)
    first_song = song(1) | {"isrc": "USABC2600001"}
    second_song = song(2) | {"isrc": "USABC2600001"}
    with transaction(immediate=True) as conn:
        first_track = upsert_track(conn, first_song, first_collection["id"], 1)
        second_track = upsert_track(conn, second_song, second_collection["id"], 1)
        now = utcnow()
        conn.execute(
            "INSERT INTO jobs(id, collection_id, kind, status, total, created_at, updated_at) VALUES('finished-job', ?, 'DOWNLOAD', 'COMPLETE', 1, ?, ?)",
            (first_collection["id"], now, now),
        )
        output = tmp_path / "same-recording.mp3"
        output.write_bytes(b"fixture")
        conn.execute(
            """INSERT INTO job_items(id, job_id, track_id, position, status, output_path, created_at, updated_at)
               VALUES('finished-item', 'finished-job', ?, 1, 'COMPLETE', ?, ?, ?)""",
            (first_track, str(output), now, now),
        )
    assert existing_completed_path(second_track, "new-item") == str(output)


def test_track_listing_can_select_a_historical_job(isolated_db):
    collection, _ = create_or_restart_resolution(
        parse_spotify_url("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
    )
    with transaction(immediate=True) as conn:
        upsert_track(conn, song(1), collection["id"], 1)
        conn.execute(
            "UPDATE collections SET status='READY', track_count=1, resolved_count=1 WHERE id=?",
            (collection["id"],),
        )
    first = create_download_job(collection["id"])
    with transaction(immediate=True) as conn:
        conn.execute("UPDATE job_items SET status='COMPLETE', progress=100 WHERE job_id=?", (first["id"],))
        conn.execute("UPDATE jobs SET status='COMPLETE' WHERE id=?", (first["id"],))
    second = create_download_job(collection["id"])

    historical = list_collection_tracks(collection["id"], 0, 100, first["id"])
    latest = list_collection_tracks(collection["id"], 0, 100, second["id"])
    assert historical["items"][0]["status"] == "COMPLETE"
    assert latest["items"][0]["status"] == "QUEUED"
