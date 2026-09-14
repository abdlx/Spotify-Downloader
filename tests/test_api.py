from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import app
from app.db import connect, transaction, utcnow
from app.repository import upsert_track
from tests.test_repository import song


def test_api_two_stage_flow_and_controls(isolated_db):
    with TestClient(app) as client:
        bad = client.post("/api/resolve", json={"url": "https://example.com/nope"})
        assert bad.status_code == 422

        response = client.post(
            "/api/resolve",
            json={"url": "https://open.spotify.com/playlist/37i9dQZF1DX4WYpdgoIcn6?si=abc"},
        )
        assert response.status_code == 202
        collection = response.json()["collection"]
        assert collection["status"] == "RESOLVING"
        assert collection["source_url"].endswith("37i9dQZF1DX4WYpdgoIcn6")

        with transaction(immediate=True) as conn:
            for position in range(1, 4):
                upsert_track(conn, song(position), collection["id"], position)
            conn.execute(
                "UPDATE collections SET status='READY', name='Fixture Mix', track_count=3, resolved_count=3, updated_at=? WHERE id=?",
                (utcnow(), collection["id"]),
            )

        preview = client.get(f"/api/collections/{collection['id']}/tracks?limit=2")
        assert preview.status_code == 200
        assert preview.json()["total"] == 3
        assert len(preview.json()["items"]) == 2

        job = client.post(f"/api/collections/{collection['id']}/download")
        assert job.status_code == 202
        job_id = job.json()["id"]
        assert job.json()["total"] == 3
        paused = client.post(f"/api/jobs/{job_id}/pause")
        assert paused.status_code == 200
        assert paused.json()["status"] == "PAUSED"
        resumed = client.post(f"/api/jobs/{job_id}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "DOWNLOADING"


def test_settings_validate_and_redact_proxy_secret(isolated_db):
    with TestClient(app) as client:
        saved = client.patch(
            "/api/settings",
            json={
                "concurrency": 5,
                "network_mode": "http",
                "proxy_url": "http://127.0.0.1:8080",
                "proxy_username": "local-user",
                "proxy_password": "never-return-this",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["concurrency"] == 5
        assert saved.json()["proxy_password"] == ""
        assert saved.json()["proxy_password_configured"] is True
        invalid = client.patch("/api/settings", json={"filename_template": "../../escape/{title}"})
        assert invalid.status_code == 422
        conn = connect()
        stored = conn.execute("SELECT value_json FROM settings WHERE key='proxy_password'").fetchone()[0]
        conn.close()
        assert "never-return-this" in stored

