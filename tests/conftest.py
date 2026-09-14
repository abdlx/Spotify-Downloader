from __future__ import annotations

import pytest


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    import app.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(db, "DOWNLOADS_DIR", tmp_path / "downloads")
    db.init_db()
    return db.DB_PATH

