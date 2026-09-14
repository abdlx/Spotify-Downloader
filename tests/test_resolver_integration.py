from __future__ import annotations

from app.core import parse_spotify_url
from app.engine.spotdl_adapter import CollectionDiscovery
from app.repository import create_or_restart_resolution, get_collection, list_collection_tracks
from app.resolve_child import run
from tests.test_repository import song


class FakeSpotdlAdapter:
    def discover(self, _url, _kind):
        return CollectionDiscovery(
            name="Mocked complete playlist",
            artwork_url="https://example.invalid/cover.jpg",
            description="Fixture",
            total=725,
            songs=((position, song(position)) for position in range(1, 726)),
        )


def test_resolver_persists_large_stream_incrementally(isolated_db, monkeypatch):
    import app.resolve_child as resolver

    monkeypatch.setattr(resolver, "SpotdlAdapter", FakeSpotdlAdapter)
    collection, job = create_or_restart_resolution(
        parse_spotify_url("https://open.spotify.com/playlist/37i9dQZF1DX4WYpdgoIcn6")
    )
    assert run(job["id"]) == 0
    complete = get_collection(collection["id"])
    assert complete["status"] == "READY"
    assert complete["track_count"] == 725
    assert complete["resolved_count"] == 725
    assert list_collection_tracks(collection["id"], 700, 100)["items"][-1]["position"] == 725

