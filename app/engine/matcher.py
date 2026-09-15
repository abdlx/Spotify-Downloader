from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import quote, urlsplit, urlunsplit

from app.core import calculate_match_confidence, extract_version_tokens


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
    """Version-aware, deterministic wrapper around spotDL's YouTube matching."""

    def find_match(self, song, settings: dict, *, exclude_video_ids: set[str] | None = None) -> MatchResult:
        from spotdl.providers.audio.youtube import YouTube
        from spotdl.utils.matching import order_results

        proxy = proxy_with_credentials(settings)
        provider = YouTube(output_format="mp3", filter_results=True)
        if proxy:
            provider.audio_handler.params["proxy"] = proxy
        artist = ", ".join(song.artists)
        primary = f"{artist} - {song.name}"
        queries = list(dict.fromkeys((primary, f"{song.artists[0]} - {song.name} official audio",
                                      f"{song.artists[0]} - {song.name} audio")))
        excluded = exclude_video_ids or set()
        best: MatchResult | None = None
        search_errors: list[str] = []
        expected_versions = set(extract_version_tokens(song.name))
        for query in queries:
            try:
                candidates = provider.get_results(query)
                if not candidates:
                    continue
                ordered = order_results(candidates, song)
                for candidate, upstream_score in sorted(ordered.items(), key=lambda entry: entry[1], reverse=True)[:10]:
                    if candidate.result_id in excluded:
                        continue
                    actual_versions = set(extract_version_tokens(candidate.name))
                    if expected_versions != actual_versions:
                        continue
                    confidence = calculate_match_confidence(
                        track_title=song.name, artists=song.artists,
                        duration_ms=int(song.duration * 1000) if song.duration else None,
                        candidate_title=candidate.name, candidate_channel=candidate.author,
                        candidate_duration_ms=int(candidate.duration * 1000) if candidate.duration else None,
                        upstream_score=upstream_score,
                        prefer_official=bool(settings.get("prefer_official", True)),
                        duration_tolerance_seconds=int(settings.get("duration_tolerance_seconds", 12)),
                    )
                    result = MatchResult(
                        provider="youtube", url=candidate.url, video_id=candidate.result_id,
                        title=candidate.name, channel=candidate.author,
                        duration_ms=int(candidate.duration * 1000) if candidate.duration else 0,
                        confidence=confidence, upstream_score=float(upstream_score), search_query=query,
                    )
                    if best is None or result.confidence > best.confidence:
                        best = result
                if best and best.confidence >= 0.9:
                    break
            except Exception as exc:
                search_errors.append(f"{query}: {exc}")
        threshold = max(0.72, float(settings.get("low_confidence_threshold", 0.72)))
        if best and best.confidence >= threshold:
            return best
        if search_errors and best is None:
            raise RuntimeError("YouTube search failed: " + " | ".join(search_errors))
        raise LookupError(f"No suitable YouTube match for {song.display_name}; queries: {' | '.join(queries)}; "
                          f"best confidence: {best.confidence if best else 'none'}")
