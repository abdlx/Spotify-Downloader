from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.core import parse_spotify_url
from app.db import connect, transaction
from app.download_child import _cover_warning, run as run_download
from app.logging_config import JsonFormatter, redact_secrets
from app.repository import create_download_job, create_or_restart_resolution, upsert_track
from tests.test_repository import song


def ready_collection(track_count: int = 1):
    collection, _ = create_or_restart_resolution(
        parse_spotify_url("https://open.spotify.com/playlist/37i9dQZF1DX4WYpdgoIcn6")
    )
    with transaction(immediate=True) as connection:
        for position in range(1, track_count + 1):
            upsert_track(connection, song(position), collection["id"], position)
        connection.execute(
            "UPDATE collections SET status='READY', track_count=?, resolved_count=? WHERE id=?",
            (track_count, track_count, collection["id"]),
        )
    return collection


def test_secret_redaction_covers_proxy_urls_headers_and_tracebacks():
    raw = (
        "proxy=http://alice:hunter2@127.0.0.1:8080 "
        "Authorization: Bearer abc.def password=visible client_secret='secret-value'"
    )
    redacted = redact_secrets(raw)
    assert "hunter2" not in redacted
    assert "abc.def" not in redacted
    assert "visible" not in redacted
    assert "secret-value" not in redacted

    record = logging.LogRecord("test", logging.ERROR, __file__, 1, raw, (), None)
    formatted = JsonFormatter().format(record)
    assert "hunter2" not in formatted
    assert "abc.def" not in formatted


def test_disk_full_pauses_job_and_leaves_item_retryable(isolated_db, monkeypatch):
    collection = ready_collection()
    job = create_download_job(collection["id"])
    connection = connect()
    item_id = connection.execute(
        "SELECT id FROM job_items WHERE job_id=?", (job["id"],)
    ).fetchone()["id"]
    connection.execute(
        "UPDATE job_items SET status='SEARCHING', attempt=1 WHERE id=?", (item_id,)
    )
    connection.close()

    import app.download_child as download_child

    def no_capacity():
        raise OSError("disk full")

    monkeypatch.setattr(download_child, "ensure_capacity", no_capacity)
    assert run_download(item_id) == 1

    connection = connect()
    item = connection.execute("SELECT status, error_code FROM job_items WHERE id=?", (item_id,)).fetchone()
    stored_job = connection.execute("SELECT status, error_code FROM jobs WHERE id=?", (job["id"],)).fetchone()
    connection.close()
    assert dict(item) == {"status": "QUEUED", "error_code": "DISK_FULL"}
    assert dict(stored_job) == {"status": "PAUSED", "error_code": "DISK_FULL"}


def test_cover_file_uses_collection_art_and_leaves_no_temporary_file(monkeypatch, tmp_path):
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"fixture")
    requested = {}

    class FakeTags:
        def getall(self, name):
            return [object()] if name == "APIC" else []

    class FakeResponse:
        content = b"jpeg fixture"

        def raise_for_status(self):
            return None

    def fake_get(url, **_kwargs):
        requested["url"] = url
        return FakeResponse()

    monkeypatch.setattr("mutagen.id3.ID3", lambda _path: FakeTags())
    monkeypatch.setattr("app.download_child.requests.get", fake_get)
    warning = _cover_warning(
        audio,
        "https://example.test/track.jpg",
        {"save_cover": True, "network_mode": "direct"},
        "https://example.test/collection.jpg",
    )

    assert warning is None
    assert requested["url"] == "https://example.test/collection.jpg"
    assert (tmp_path / "cover.jpg").read_bytes() == b"jpeg fixture"
    assert not list(tmp_path.glob(".cover-*.tmp"))


def test_failed_resolution_restart_preserves_partial_rows(isolated_db):
    reference = parse_spotify_url("https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy")
    collection, job = create_or_restart_resolution(reference)
    with transaction(immediate=True) as connection:
        upsert_track(connection, song(1), collection["id"], 1)
        connection.execute("UPDATE collections SET status='FAILED', resolved_count=1 WHERE id=?", (collection["id"],))
        connection.execute("UPDATE jobs SET status='FAILED' WHERE id=?", (job["id"],))

    restarted, restarted_job = create_or_restart_resolution(reference)
    connection = connect()
    persisted = connection.execute(
        "SELECT status FROM collection_tracks WHERE collection_id=?", (collection["id"],)
    ).fetchall()
    connection.close()
    assert restarted["resolved_count"] == 0
    assert [row["status"] for row in persisted] == ["DISCOVERED"]
    assert restarted_job["id"] != job["id"]


def test_empty_collection_cannot_create_stuck_job(isolated_db):
    collection = ready_collection(track_count=0)
    with pytest.raises(ValueError, match="no downloadable tracks"):
        create_download_job(collection["id"])


def test_retry_without_failures_returns_conflict(isolated_db):
    collection = ready_collection()
    job = create_download_job(collection["id"])
    with TestClient(app) as client:
        response = client.post(f"/api/jobs/{job['id']}/retry-failed")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NO_FAILED_TRACKS"


def test_single_failed_track_can_be_retried(isolated_db):
    collection = ready_collection()
    job = create_download_job(collection["id"])
    connection = connect()
    item_id = connection.execute(
        "SELECT id FROM job_items WHERE job_id=?", (job["id"],)
    ).fetchone()["id"]
    connection.execute(
        "UPDATE job_items SET status='FAILED', attempt=4, error='source unavailable' WHERE id=?",
        (item_id,),
    )
    connection.execute("UPDATE jobs SET status='COMPLETE_WITH_ERRORS' WHERE id=?", (job["id"],))
    connection.close()

    with TestClient(app) as client:
        response = client.post(f"/api/job-items/{item_id}/retry")
        tracks = client.get(f"/api/collections/{collection['id']}/tracks").json()["items"]

    assert response.status_code == 200
    assert response.json()["status"] == "DOWNLOADING"
    assert tracks[0]["job_item_id"] == item_id
    assert tracks[0]["status"] == "QUEUED"
