from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import quote, urlsplit, urlunsplit

from app.core import calculate_match_confidence


@dataclass(frozen=True)
class MatchResult:
    provider: str
    url: str
    video_id: str
    title: str
    channel: str
    duration_ms: int
    confidence: float
    upstream_score: float


def proxy_with_credentials(settings: dict) -> str | None:
    if settings.get("network_mode") == "direct" or not settings.get("proxy_url"):
        return None
    parts = urlsplit(settings["proxy_url"])
    username = settings.get("proxy_username") or ""
    password = settings.get("proxy_password") or ""
    if not username:
        return settings["proxy_url"]
    auth = quote(username, safe="")
    if password:
        auth += f":{quote(password, safe='')}"
    host = parts.hostname or ""
    if parts.port:
        host += f":{parts.port}"
    return urlunsplit((parts.scheme, f"{auth}@{host}", parts.path, parts.query, parts.fragment))


class MusicMatcher:
    """Version-aware, deterministic wrapper around spotDL's YouTube matching."""

    def find_match(self, song, settings: dict) -> MatchResult:
        from spotdl.providers.audio.youtube import YouTube
        from spotdl.utils.matching import order_results

        proxy = proxy_with_credentials(settings)
        provider = YouTube(output_format="mp3", filter_results=True)
        if proxy:
            provider.audio_handler.params["proxy"] = proxy
        query = f"{', '.join(song.artists)} - {song.name}"
        candidates = provider.get_results(query)
        if not candidates:
            raise LookupError(f"No results found for song: {song.display_name}")
        ordered = order_results(candidates, song)
        candidate, upstream_score = provider.get_best_result(ordered)
        confidence = calculate_match_confidence(
            track_title=song.name,
            artists=song.artists,
            duration_ms=int(song.duration * 1000) if song.duration else None,
            candidate_title=candidate.name,
            candidate_channel=candidate.author,
            candidate_duration_ms=int(candidate.duration * 1000) if candidate.duration else None,
            upstream_score=upstream_score,
            prefer_official=bool(settings.get("prefer_official", True)),
            duration_tolerance_seconds=int(settings.get("duration_tolerance_seconds", 12)),
        )
        return MatchResult(
            provider="youtube", url=candidate.url, video_id=candidate.result_id,
            title=candidate.name, channel=candidate.author,
            duration_ms=int(candidate.duration * 1000), confidence=confidence,
            upstream_score=float(upstream_score),
        )
