from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class YtDlpCandidate:
    video_id: str
    url: str
    title: str
    channel: str
    duration_ms: int | None


class YtDlpAdapter:
    """Narrow, read-only yt-dlp boundary for future lower-level matching tools."""

    def __init__(self, *, proxy: str | None = None) -> None:
        self.proxy = proxy

    def search(self, query: str, *, limit: int = 10) -> list[YtDlpCandidate]:
        if not query.strip():
            raise ValueError("Search query cannot be empty.")
        if not 1 <= limit <= 25:
            raise ValueError("Search limit must be between 1 and 25.")

        from yt_dlp import YoutubeDL

        options: dict[str, Any] = {
            "extract_flat": True,
            "noplaylist": True,
            "quiet": True,
            "skip_download": True,
            "no_warnings": True,
        }
        if self.proxy:
            options["proxy"] = self.proxy
        with YoutubeDL(options) as downloader:
            result = downloader.extract_info(f"ytsearch{limit}:{query.strip()}", download=False)

        candidates: list[YtDlpCandidate] = []
        for entry in (result or {}).get("entries") or []:
            if not entry or not entry.get("id"):
                continue
            duration = entry.get("duration")
            candidates.append(
                YtDlpCandidate(
                    video_id=str(entry["id"]),
                    url=entry.get("webpage_url") or entry.get("url") or f"https://www.youtube.com/watch?v={entry['id']}",
                    title=entry.get("title") or "Unknown title",
                    channel=entry.get("channel") or entry.get("uploader") or "Unknown channel",
                    duration_ms=round(float(duration) * 1000) if duration is not None else None,
                )
            )
        return candidates
