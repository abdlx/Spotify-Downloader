from __future__ import annotations

from types import SimpleNamespace

from app.engine.spotdl_adapter import SpotdlAdapter


def test_spotdl_download_adapter_contains_upstream_configuration(monkeypatch, tmp_path):
    output = tmp_path / "song.mp3"
    output.write_bytes(b"fixture")
    captured = {}

    class FakeSpotdl:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs
            self.downloader = SimpleNamespace(
                audio_providers=[SimpleNamespace(audio_handler=SimpleNamespace(params={}))],
                progress_handler=SimpleNamespace(update_callback=None, web_ui=False,
                                                 get_new_tracker=lambda song: SimpleNamespace(notify_error=lambda *_: None)),
            )
            captured["instance"] = self

        def download(self, song):
            self.downloader.progress_handler.update_callback(
                SimpleNamespace(progress=61), "Downloading"
            )
            return song, output

    monkeypatch.setattr("spotdl.Spotdl", FakeSpotdl)
    monkeypatch.setattr(
        "spotdl.utils.config.GlobalConfig.set_parameter",
        lambda name, value: captured.update({"global": (name, value)}),
    )
    settings = {
        "filename_template": "{list-name}/{list-position} - {title}.{output-ext}",
        "bitrate": "192k",
        "network_mode": "http",
        "proxy_url": "http://127.0.0.1:8080",
        "proxy_username": "",
        "proxy_password": "",
    }
    progress = []

    result = SpotdlAdapter.download(
        SimpleNamespace(name="Fixture"), settings, lambda stage, value: progress.append((stage, value))
    )

    assert result == output
    assert captured["kwargs"]["downloader_settings"]["format"] == "mp3"
    assert captured["kwargs"]["downloader_settings"]["bitrate"] == "192k"
    assert captured["global"] == (
        "proxies",
        {"http": "http://127.0.0.1:8080", "https": "http://127.0.0.1:8080"},
    )
    assert captured["instance"].downloader.audio_providers[0].audio_handler.params["proxy"] == "http://127.0.0.1:8080"
    assert progress == [("Downloading", 61.0)]


def test_resolved_playlist_record_does_not_force_spotify_refetch():
    track = {
        "title": "Count on Me", "artists": ["Bruno Mars"],
        "source_id": "spotify-id", "source_url": "https://open.spotify.com/track/spotify-id",
        "album": "Doo-Wops", "album_artist": "Bruno Mars", "duration_ms": 197000,
        "disc_number": 1, "disc_count": None, "track_number": 3, "track_count": 12,
        "genres": [], "metadata": {"album_id": "album-id"},
    }
    song = SpotdlAdapter.record_to_song(track, list_name="test", position=1, list_length=162)
    required = ("genres", "disc_count", "tracks_count", "track_number", "album_id", "album_artist")
    assert all(getattr(song, field) is not None for field in required)
    assert song.disc_count == 1
