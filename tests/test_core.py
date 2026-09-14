from pathlib import Path

import pytest

from app.core import (
    InputError,
    calculate_match_confidence,
    extract_version_tokens,
    parse_spotify_url,
    safe_download_path,
    sanitize_filename,
    validate_filename_template,
)


def test_spotify_url_normalization_removes_query_and_locale():
    ref = parse_spotify_url(" https://open.spotify.com/intl-de/playlist/37i9dQZF1DX4WYpdgoIcn6?si=secret ")
    assert ref.kind == "playlist"
    assert ref.source_id == "37i9dQZF1DX4WYpdgoIcn6"
    assert ref.url == "https://open.spotify.com/playlist/37i9dQZF1DX4WYpdgoIcn6"


@pytest.mark.parametrize("value", [
    "http://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
    "https://evil.example/track/4cOdK2wGLETKBW3PvgPWqT",
    "https://open.spotify.com/artist/4cOdK2wGLETKBW3PvgPWqT",
    "not a url",
])
def test_unsupported_urls_are_rejected(value):
    with pytest.raises(InputError):
        parse_spotify_url(value)


def test_filename_sanitization_is_cross_platform():
    assert sanitize_filename('CON') == "_CON"
    assert sanitize_filename('  A/B: C*?  ') == "A_B_ C__"
    assert sanitize_filename("... ") == "Untitled"


def test_filename_template_cannot_escape_downloads():
    with pytest.raises(InputError):
        validate_filename_template("../outside/{title}")
    with pytest.raises(InputError):
        validate_filename_template("C:/outside/{title}")
    assert validate_filename_template("{list-name}/{title}").endswith(".{output-ext}")


def test_safe_download_path_blocks_traversal(tmp_path):
    assert safe_download_path(tmp_path, "Album/song.mp3").is_relative_to(tmp_path)
    with pytest.raises(InputError):
        safe_download_path(tmp_path, "../escape.mp3")


def test_version_tokens_preserve_semantic_variants():
    assert extract_version_tokens("Memory Reboot (Slowed + Reverb)") == ["slowed + reverb"]
    assert extract_version_tokens("Song - Live Acoustic Version") == ["acoustic", "live", "version"]


def test_match_confidence_penalizes_wrong_version():
    exact = calculate_match_confidence(
        track_title="Memory Reboot (Slowed)", artists=["VØJ", "Narvent"], duration_ms=236000,
        candidate_title="VØJ x Narvent - Memory Reboot (Slowed)", candidate_channel="VØJ",
        candidate_duration_ms=235000, upstream_score=94,
    )
    wrong = calculate_match_confidence(
        track_title="Memory Reboot (Slowed)", artists=["VØJ", "Narvent"], duration_ms=236000,
        candidate_title="VØJ x Narvent - Memory Reboot", candidate_channel="VØJ",
        candidate_duration_ms=235000, upstream_score=94,
    )
    assert exact > wrong
    assert exact >= 0.75

