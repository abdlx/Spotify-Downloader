from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.config import DOWNLOADS_DIR
from app.core import safe_download_path


MIN_FREE_BYTES = 512 * 1024 * 1024


def ensure_capacity() -> None:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(DOWNLOADS_DIR).free
    if free < MIN_FREE_BYTES:
        raise OSError(f"Disk full: only {free // (1024 * 1024)} MiB remains")


def expected_output_path(song, template: str) -> Path:
    from spotdl.utils.formatter import create_file_name

    output_template = str(DOWNLOADS_DIR / template)
    path = create_file_name(song, output_template, "mp3", file_name_length=180)
    return safe_download_path(DOWNLOADS_DIR, str(path.resolve().relative_to(DOWNLOADS_DIR.resolve())))


def reuse_file(source: str, target: Path) -> Path:
    source_path = Path(source).resolve()
    safe_download_path(DOWNLOADS_DIR, str(source_path.relative_to(DOWNLOADS_DIR.resolve())))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    try:
        os.link(source_path, target)
    except OSError:
        shutil.copy2(source_path, target)
    return target

