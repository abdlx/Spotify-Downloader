from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


SPOTIFY_RE = re.compile(r"^/(?:intl-[a-zA-Z-]+/)?(track|playlist|album)/([A-Za-z0-9]{10,32})/?$")
VERSION_PATTERNS = (
    ("lofi", re.compile(r"\blo[ -]?fi\b", re.I)),
    ("flip", re.compile(r"\bflip\b", re.I)),
    ("hoodtrap", re.compile(r"\bhoodtrap\b", re.I)),
    ("mylancore", re.compile(r"\bmylancore\b", re.I)),
    ("nightcore", re.compile(r"\bnightcore\b", re.I)),
    ("mashup", re.compile(r"\bmash[ -]?up\b", re.I)),
    ("tiktok", re.compile(r"\btik[ -]?tok\b", re.I)),
    ("8d audio", re.compile(r"\b8[ -]?d(?:\s+audio)?\b", re.I)),
    ("pitched down", re.compile(r"\bpitch(?:ed)?\s+down\b", re.I)),
    ("cover", re.compile(r"\bcover\b", re.I)),
    ("karaoke", re.compile(r"\bkaraoke\b", re.I)),
    ("vocals only", re.compile(r"\bvocals?\s+only\b", re.I)),
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
    value = "".join(char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char))
    return re.sub(r"[_\W]+", " ", value.casefold(), flags=re.UNICODE).strip()


def _similarity(left: str, right: str) -> float:
    folded_left, folded_right = _fold(left), _fold(right)
    return SequenceMatcher(None, folded_left, folded_right).ratio() if folded_left and folded_right else 0.0


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
    if _fold(track_title) and _fold(track_title) in _fold(candidate_title):
        title_score = max(title_score, 0.98)
    candidate_identity = f"{candidate_title} {candidate_channel}"
    artist_scores = [1.0 if _fold(artist) and _fold(artist) in _fold(candidate_identity)
                     else _similarity(artist, candidate_identity) for artist in artists]
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
    message = getattr(exc, "classification_error", None) or getattr(exc, "raw_error", None) or str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    if "reinitializing song" in lowered:
        if "429" in lowered or "rate limit" in lowered:
            return "SPOTIFY_RATE_LIMIT", "Spotify temporarily limited metadata requests."
        return "SPOTIFY_METADATA_ERROR", "Spotify metadata could not be refreshed."
    if "no results" in lowered or "no match" in lowered or "no suitable youtube match" in lowered:
        return "NO_MATCH", "No suitable YouTube match was found for this track."
    if "sign in to confirm your age" in lowered or "confirm your age" in lowered:
        return "AUTH_REQUIRED", "The YouTube result requires age verification."
    if "sign in to confirm" in lowered or "not a bot" in lowered:
        return "BOT_DETECTION", "YouTube requested bot verification."
    if "this content isn't available, try again later" in lowered:
        return "YOUTUBE_RATE_LIMIT", "YouTube temporarily limited video requests."
    if "429" in lowered or "rate limit" in lowered:
        return "HTTP_429", "The source is rate-limiting requests."
    if "403" in lowered or "forbidden" in lowered:
        return "HTTP_403", "YouTube refused the media request."
    if "region" in lowered and ("restrict" in lowered or "block" in lowered):
        return "REGION_RESTRICTED", "The matched video is not available in this region."
    if "sign in" in lowered or "login required" in lowered or "age-restricted" in lowered:
        return "AUTH_REQUIRED", "The matched video requires authentication."
    if "requested format" in lowered or "format is not available" in lowered:
        return "FORMAT_UNAVAILABLE", "The matched video has no usable audio format."
    if "extractor" in lowered or "nsig" in lowered or "signature extraction" in lowered:
        return "EXTRACTOR_ERROR", "YouTube extraction failed."
    if "ffmpeg" in lowered or "failed to convert" in lowered:
        return "FFMPEG_ERROR", "Audio conversion failed."
    if "metadata" in lowered or "embed" in lowered:
        return "METADATA_ERROR", "The audio downloaded, but metadata could not be embedded."
    if "permission" in lowered or "read-only file system" in lowered:
        return "FILESYSTEM_ERROR", "The downloads directory is not writable."
    if "no space" in lowered or "disk full" in lowered:
        return "DISK_FULL", "There is not enough free storage to finish this download."
    if "unavailable" in lowered or "private video" in lowered:
        return "YOUTUBE_UNAVAILABLE", "The matched source is no longer available."
    if "timeout" in lowered or "timed out" in lowered:
        return "TIMEOUT", "The media request timed out."
    if "network" in lowered or "connection" in lowered:
        return "NETWORK_ERROR", "A network error interrupted the operation."
    if "process exited" in lowered or "subprocess" in lowered:
        return "PROCESS_ERROR", "The download process stopped unexpectedly."
    return "UNKNOWN", "The download could not be completed."
