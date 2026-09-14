from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


SPOTIFY_RE = re.compile(r"^/(?:intl-[a-zA-Z-]+/)?(track|playlist|album)/([A-Za-z0-9]{10,32})/?$")
VERSION_PATTERNS = (
    ("slowed + reverb", re.compile(r"\bslowed\s*(?:\+|and|&)\s*reverb\b", re.I)),
    ("sped up", re.compile(r"\bsped[ -]?up\b", re.I)),
    ("radio edit", re.compile(r"\bradio edit\b", re.I)),
    ("extended mix", re.compile(r"\bextended mix\b", re.I)),
    ("remastered", re.compile(r"\bremaster(?:ed)?(?:\s+\d{4})?\b", re.I)),
    ("instrumental", re.compile(r"\binstrumental\b", re.I)),
    ("acoustic", re.compile(r"\bacoustic\b", re.I)),
    ("slowed", re.compile(r"\bslowed\b", re.I)),
    ("reverb", re.compile(r"\breverb\b", re.I)),
    ("remix", re.compile(r"\bremix\b", re.I)),
    ("live", re.compile(r"\blive\b", re.I)),
    ("edit", re.compile(r"\bedit\b", re.I)),
    ("mix", re.compile(r"\bmix\b", re.I)),
    ("version", re.compile(r"\bversion\b", re.I)),
)


class InputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SpotifyReference:
    kind: str
    source_id: str
    url: str


def parse_spotify_url(raw: str) -> SpotifyReference:
    value = raw.strip()
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise InputError("INVALID_URL", "Enter a valid Spotify URL.") from exc
    if parts.scheme != "https" or (parts.hostname or "").lower() != "open.spotify.com":
        raise InputError("UNSUPPORTED_URL", "Use a public open.spotify.com track, album, or playlist link.")
    match = SPOTIFY_RE.fullmatch(parts.path)
    if not match:
        raise InputError("UNSUPPORTED_URL", "Only Spotify track, album, and playlist links are supported.")
    kind, source_id = match.groups()
    normalized = urlunsplit(("https", "open.spotify.com", f"/{kind}/{source_id}", "", ""))
    return SpotifyReference(kind=kind, source_id=source_id, url=normalized)


def extract_version_tokens(title: str) -> list[str]:
    found: list[str] = []
    for label, pattern in VERSION_PATTERNS:
        if pattern.search(title) and not (label in {"slowed", "reverb"} and "slowed + reverb" in found):
            found.append(label)
    return found


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _fold(left), _fold(right)).ratio()


def calculate_match_confidence(
    *,
    track_title: str,
    artists: list[str],
    duration_ms: int | None,
    candidate_title: str,
    candidate_channel: str,
    candidate_duration_ms: int | None,
    upstream_score: float | None = None,
    prefer_official: bool = True,
    duration_tolerance_seconds: int = 30,
) -> float:
    title_score = _similarity(track_title, candidate_title)
    if _fold(track_title) in _fold(candidate_title):
        title_score = max(title_score, 0.98)
    candidate_identity = f"{candidate_title} {candidate_channel}"
    artist_scores = [1.0 if _fold(artist) in _fold(candidate_identity) else _similarity(artist, candidate_identity) for artist in artists]
    artist_score = sum(artist_scores) / len(artist_scores) if artist_scores else 0
    if duration_ms and candidate_duration_ms:
        delta = abs(duration_ms - candidate_duration_ms) / 1000
        duration_score = max(0.0, 1.0 - delta / max(1, duration_tolerance_seconds))
    else:
        duration_score = 0.55
    expected = set(extract_version_tokens(track_title))
    actual = set(extract_version_tokens(candidate_title))
    version_score = 1.0 if expected == actual else (0.45 if expected & actual else 0.0)
    score = title_score * 0.38 + artist_score * 0.24 + duration_score * 0.20 + version_score * 0.14
    if upstream_score is not None:
        score += max(0.0, min(1.0, upstream_score / 100.0)) * 0.04
    if prefer_official and re.search(r"official (?:audio|video)|- topic\b|vevo\b", f"{candidate_title} {candidate_channel}", re.I):
        score += 0.025
    if not expected and actual & {"live", "remix", "slowed", "reverb", "acoustic", "instrumental"}:
        score -= 0.22
    return round(max(0.0, min(1.0, score)), 3)


WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_filename(value: str, max_length: int = 120) -> str:
    value = unicodedata.normalize("NFC", value)
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        value = "Untitled"
    if value.upper() in WINDOWS_RESERVED:
        value = f"_{value}"
    return value[:max_length].rstrip(" .") or "Untitled"


ALLOWED_TEMPLATE_FIELDS = {
    "title", "artists", "artist", "album", "album-artist", "track-number",
    "list-length", "list-position", "list-name", "output-ext",
}


def validate_filename_template(template: str) -> str:
    if not template or len(template) > 300:
        raise InputError("INVALID_SETTING", "Filename template must be between 1 and 300 characters.")
    normalized = template.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or ".." in normalized.split("/"):
        raise InputError("INVALID_SETTING", "Filename template must stay inside the downloads directory.")
    fields = set(re.findall(r"\{([^{}]+)\}", normalized))
    unknown = fields - ALLOWED_TEMPLATE_FIELDS
    if unknown:
        raise InputError("INVALID_SETTING", f"Unsupported filename field: {sorted(unknown)[0]}")
    if "output-ext" not in fields:
        normalized += ".{output-ext}"
    return normalized


def safe_download_path(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise InputError("PATH_TRAVERSAL", "The destination escaped the downloads directory.")
    return target


def classify_error(exc: BaseException) -> tuple[str, str]:
    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    if "no results" in lowered or "no match" in lowered:
        return "NO_MATCH", "No suitable YouTube match was found for this track."
    if "429" in lowered or "rate limit" in lowered:
        return "RATE_LIMITED", "The source is rate-limiting requests. The track will retry automatically."
    if "ffmpeg" in lowered:
        return "FFMPEG_FAILED", "Audio conversion failed. Review the technical details and retry."
    if "metadata" in lowered or "embed" in lowered:
        return "TAGGING_FAILED", "The audio downloaded, but metadata could not be embedded."
    if "permission" in lowered:
        return "FILESYSTEM_PERMISSION_ERROR", "The downloads directory is not writable."
    if "no space" in lowered or "disk full" in lowered:
        return "DISK_FULL", "There is not enough free storage to finish this download."
    if "unavailable" in lowered or "private video" in lowered:
        return "SOURCE_UNAVAILABLE", "The matched source is no longer available."
    if "network" in lowered or "connection" in lowered or "timeout" in lowered:
        return "NETWORK_ERROR", "A network error interrupted the operation."
    return "DOWNLOAD_FAILED", "The download could not be completed."
