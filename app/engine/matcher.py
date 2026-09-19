from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import quote, urlsplit, urlunsplit

from app.core import _fold, calculate_match_confidence, extract_version_tokens
from app.engine.ytdlp_adapter import YtDlpAdapter


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
    search_query: str


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
    """Conservative YouTube search with a flat extractor that skips per-video authentication checks."""

    def find_match(self, song, settings: dict, *, exclude_video_ids: set[str] | None = None) -> MatchResult:
        adapter = YtDlpAdapter(proxy=proxy_with_credentials(settings))
        artist = ", ".join(song.artists)
        score_title = re.sub(r"\s*[\[(]\s*from\s+[^)\]]+[)\]]", "", song.name, flags=re.I).strip() or song.name
        queries = list(dict.fromkeys((f"{artist} - {song.name}",
                                      f"{song.artists[0]} - {score_title} official audio",
                                      f"{song.artists[0]} - {score_title} audio")))
        excluded = exclude_video_ids or set()
        best: MatchResult | None = None
        search_errors: list[str] = []
        candidate_count = 0
        rejected_versions = 0
        rejected_durations = 0
        rejected_identity = 0
        expected_versions = set(extract_version_tokens(song.name))
        threshold = max(0.72, float(settings.get("low_confidence_threshold", 0.72)))
        duration_ms = int(song.duration * 1000) if song.duration else None
        for query in queries:
            try:
                candidates = adapter.search(query, limit=10)
            except Exception as exc:
                search_errors.append(f"{query}: {exc}")
                continue
            for candidate in candidates:
                candidate_count += 1
                if candidate.video_id in excluded:
                    continue
                if expected_versions != set(extract_version_tokens(candidate.title)):
                    rejected_versions += 1
                    continue
                if re.search(r"\s+[x\u00d7]\s+", song.name, re.I) and not re.search(r"\s+[x\u00d7]\s+", candidate.title, re.I):
                    rejected_versions += 1
                    continue
                if expected_versions or re.search(r"\s+[x\u00d7]\s+", song.name, re.I):
                    identity = _fold(f"{candidate.title} {candidate.channel}")
                    if not any(_fold(artist_name) and _fold(artist_name) in identity for artist_name in song.artists):
                        rejected_identity += 1
                        continue
                if duration_ms and candidate.duration_ms:
                    max_delta_ms = max(20000, min(45000, int(duration_ms * 0.1)))
                    if abs(duration_ms - candidate.duration_ms) > max_delta_ms:
                        rejected_durations += 1
                        continue
                confidence = calculate_match_confidence(
                    track_title=score_title, artists=song.artists, duration_ms=duration_ms,
                    candidate_title=candidate.title, candidate_channel=candidate.channel,
                    candidate_duration_ms=candidate.duration_ms,
                    prefer_official=bool(settings.get("prefer_official", True)),
                    duration_tolerance_seconds=int(settings.get("duration_tolerance_seconds", 12)),
                )
                result = MatchResult(
                    provider="youtube", url=candidate.url, video_id=candidate.video_id,
                    title=candidate.title, channel=candidate.channel,
                    duration_ms=candidate.duration_ms or 0, confidence=confidence,
                    upstream_score=0.0, search_query=query,
                )
                if best is None or result.confidence > best.confidence:
                    best = result
            if best and best.confidence >= threshold:
                return best
        if search_errors and candidate_count == 0:
            raise RuntimeError("YouTube search failed: " + " | ".join(search_errors))
        best_description = (f"{best.title} [{best.url}] confidence={best.confidence}"
                            if best else "none")
        raise LookupError(
            f"No suitable YouTube match for {song.display_name}; queries: {' | '.join(queries)}; "
            f"candidates={candidate_count}, rejected_versions={rejected_versions}, "
            f"rejected_durations={rejected_durations}, rejected_identity={rejected_identity}, "
            f"best={best_description}; "
            f"search_errors={' | '.join(search_errors) if search_errors else 'none'}"
        )
