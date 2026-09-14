from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
from datetime import UTC, datetime

from app.config import DOWNLOADS_DIR
from app.db import connect


def _binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        return "missing"
    try:
        result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=4, check=False)
        first = (result.stdout or result.stderr).splitlines()[0].strip()
        return first[:100] or "ok"
    except Exception:
        return "unavailable"


def health_snapshot() -> dict:
    components: dict[str, str] = {}
    try:
        conn = connect()
        conn.execute("SELECT 1").fetchone()
        heartbeat = conn.execute("SELECT updated_at FROM worker_heartbeat ORDER BY updated_at DESC LIMIT 1").fetchone()
        conn.close()
        components["database"] = "ok"
        if heartbeat:
            age = (datetime.now(UTC) - datetime.fromisoformat(heartbeat["updated_at"])).total_seconds()
            components["worker"] = "ok" if age < 15 else "stale"
        else:
            components["worker"] = "starting"
    except Exception:
        components["database"] = "unavailable"
        components["worker"] = "unknown"
    for package in ("spotdl", "yt-dlp", "yt-dlp-ejs"):
        try:
            components[package.replace("-", "_")] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            components[package.replace("-", "_")] = "missing"
    components["ffmpeg"] = _binary("ffmpeg")
    components["ffprobe"] = _binary("ffprobe")
    components["deno"] = _binary("deno")
    try:
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        test_file = DOWNLOADS_DIR / f".write-test-{os.getpid()}"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        components["downloads"] = "writable"
    except OSError:
        components["downloads"] = "not_writable"
    bad = {"missing", "unavailable", "not_writable", "stale", "unknown"}
    status = "healthy" if not any(value in bad for value in components.values()) else "degraded"
    return {"status": status, "components": components}

