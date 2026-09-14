from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from app.config import DATA_DIR, DOWNLOADS_DIR, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, USE_OFFICIAL_SPOTIFY


@dataclass
class CollectionDiscovery:
    name: str
    artwork_url: str | None
    description: str | None
    total: int
    songs: Iterator[tuple[int, dict[str, Any]]]


class SpotdlAdapter:
    """The only application boundary that imports Spotify-resolution internals."""

    def __init__(self) -> None:
        from spotdl.utils.spotify import SpotifyClient

        SpotifyClient.init(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            use_official_api=USE_OFFICIAL_SPOTIFY,
            headless=True,
            cache_path=str(DATA_DIR / "spotify-cache"),
            max_retries=3,
        )
        self.client = SpotifyClient()

    def discover(self, url: str, kind: str) -> CollectionDiscovery:
        if kind == "track":
            from spotdl.types.song import Song

            song = Song.from_url(url)
            return CollectionDiscovery(
                name=song.name,
                artwork_url=song.cover_url,
                description=None,
                total=1,
                songs=iter([(1, self.song_to_record(song))]),
            )
        if kind == "album":
            return self._discover_album(url)
        if kind == "playlist":
            return self._discover_playlist(url)
        raise ValueError(f"Unsupported Spotify resource: {kind}")

    def _discover_playlist(self, url: str) -> CollectionDiscovery:
        playlist = self.client.playlist(url)
        if not playlist:
            raise LookupError("Spotify playlist was not found or is not public.")
        response = self.client.playlist_items(url)
        if not response:
            raise LookupError("Spotify returned no playlist items.")
        total = int(response.get("total") or playlist.get("tracks", {}).get("total") or 0)

        def songs() -> Iterator[tuple[int, dict[str, Any]]]:
            page = response
            position = 0
            while page:
                for wrapper in page.get("items", []):
                    position += 1
                    raw = (wrapper or {}).get("track") or (wrapper or {}).get("item")
                    if not raw or raw.get("is_local") or raw.get("type") != "track" or not raw.get("id"):
                        continue
                    yield position, self.raw_track_to_record(raw)
                if not page.get("next"):
                    break
                page = self.client.next(page)
                if page is None:
                    raise ConnectionError(f"Spotify pagination stopped after item {position} of {total}.")

        images = playlist.get("images") or []
        art = max(images, key=lambda image: (image.get("width") or 0) * (image.get("height") or 0)).get("url") if images else None
        return CollectionDiscovery(
            name=playlist.get("name") or "Spotify playlist",
            artwork_url=art,
            description=playlist.get("description"),
            total=total,
            songs=songs(),
        )

    def _discover_album(self, url: str) -> CollectionDiscovery:
        album = self.client.album(url)
        if not album:
            raise LookupError("Spotify album was not found.")
        response = self.client.album_tracks(url)
        if not response:
            raise LookupError("Spotify returned no album tracks.")
        total = int(response.get("total") or album.get("total_tracks") or 0)

        def songs() -> Iterator[tuple[int, dict[str, Any]]]:
            page = response
            position = 0
            while page:
                for raw in page.get("items", []):
                    position += 1
                    if not raw or raw.get("is_local") or not raw.get("id"):
                        continue
                    merged = dict(raw)
                    merged["album"] = album
                    yield position, self.raw_track_to_record(merged)
                if not page.get("next"):
                    break
                page = self.client.next(page)
                if page is None:
                    raise ConnectionError(f"Spotify pagination stopped after item {position} of {total}.")

        images = album.get("images") or []
        art = max(images, key=lambda image: (image.get("width") or 0) * (image.get("height") or 0)).get("url") if images else None
        return CollectionDiscovery(
            name=album.get("name") or "Spotify album",
            artwork_url=art,
            description=None,
            total=total,
            songs=songs(),
        )

    @staticmethod
    def raw_track_to_record(raw: dict[str, Any]) -> dict[str, Any]:
        album = raw.get("album") or {}
        artists = [artist.get("name", "") for artist in raw.get("artists", []) if artist.get("name")]
        album_artists = album.get("artists") or []
        images = album.get("images") or []
        art = max(images, key=lambda image: (image.get("width") or 0) * (image.get("height") or 0)).get("url") if images else None
        copyrights = album.get("copyrights") or []
        return {
            "source_id": raw["id"],
            "source_url": (raw.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/track/{raw['id']}",
            "title": raw.get("name") or "Unknown title",
            "artists": artists or ["Unknown artist"],
            "album": album.get("name"),
            "album_artist": album_artists[0].get("name") if album_artists else (artists[0] if artists else None),
            "duration_ms": raw.get("duration_ms"),
            "track_number": raw.get("track_number"),
            "track_count": album.get("total_tracks"),
            "disc_number": raw.get("disc_number"),
            "disc_count": None,
            "release_date": album.get("release_date"),
            "isrc": (raw.get("external_ids") or {}).get("isrc"),
            "artwork_url": art,
            "genres": album.get("genres") or [],
            "explicit": raw.get("explicit"),
            "publisher": album.get("label"),
            "copyright": copyrights[0].get("text") if copyrights else None,
            "metadata": {"album_id": album.get("id"), "album_type": album.get("album_type")},
        }

    @staticmethod
    def song_to_record(song) -> dict[str, Any]:
        return {
            "source_id": song.song_id,
            "source_url": song.url,
            "title": song.name,
            "artists": song.artists,
            "album": song.album_name,
            "album_artist": song.album_artist,
            "duration_ms": int(song.duration * 1000) if song.duration is not None else None,
            "track_number": song.track_number,
            "track_count": song.tracks_count,
            "disc_number": song.disc_number,
            "disc_count": song.disc_count,
            "release_date": song.date,
            "isrc": song.isrc,
            "artwork_url": song.cover_url,
            "genres": song.genres or [],
            "explicit": song.explicit,
            "publisher": song.publisher,
            "copyright": song.copyright_text,
            "metadata": {"album_id": song.album_id, "album_type": song.album_type},
        }

    @staticmethod
    def record_to_song(track: dict[str, Any], *, list_name: str, position: int, list_length: int):
        from spotdl.types.song import Song

        metadata = track.get("metadata") or {}
        release_date = track.get("release_date") or ""
        return Song.from_missing_data(
            name=track["title"], artists=track["artists"], artist=track["artists"][0],
            genres=track.get("genres") or [], disc_number=track.get("disc_number"),
            disc_count=track.get("disc_count"), album_name=track.get("album"),
            album_artist=track.get("album_artist"), duration=int((track.get("duration_ms") or 0) / 1000),
            year=int(release_date[:4]) if len(release_date) >= 4 and release_date[:4].isdigit() else None,
            date=release_date, track_number=track.get("track_number"), tracks_count=track.get("track_count"),
            song_id=track["source_id"], explicit=bool(track.get("explicit")), publisher=track.get("publisher") or "",
            url=track["source_url"], isrc=track.get("isrc"), cover_url=track.get("artwork_url"),
            copyright_text=track.get("copyright"), album_id=metadata.get("album_id"),
            album_type=metadata.get("album_type"), list_name=list_name, list_position=position,
            list_length=max(list_length, 999),
        )

    @staticmethod
    def download(song, settings: dict[str, Any], on_progress: Callable[[str, float], None]) -> Path:
        from spotdl import Spotdl
        from spotdl.utils.config import GlobalConfig

        from app.engine.matcher import proxy_with_credentials

        proxy = proxy_with_credentials(settings)
        GlobalConfig.set_parameter("proxies", {"http": proxy, "https": proxy} if proxy else None)
        spotdl = Spotdl(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            use_official_api=USE_OFFICIAL_SPOTIFY,
            headless=True,
            cache_path=str(DATA_DIR / "spotify-cache"),
            downloader_settings={
                "audio_providers": ["youtube"],
                "lyrics_providers": [],
                "output": str(DOWNLOADS_DIR / settings["filename_template"]),
                "format": "mp3",
                "bitrate": settings["bitrate"],
                "threads": 1,
                "overwrite": "skip",
                "scan_for_songs": False,
                "simple_tui": True,
                "max_filename_length": 180,
                "yt_dlp_args": None,
                "skip_album_art": False,
                "print_errors": False,
            },
        )
        if proxy:
            for provider in spotdl.downloader.audio_providers:
                provider.audio_handler.params["proxy"] = proxy

        def progress(tracker, message: str) -> None:
            on_progress(message, float(tracker.progress))

        spotdl.downloader.progress_handler.update_callback = progress
        spotdl.downloader.progress_handler.web_ui = True
        _, output = spotdl.download(song)
        if not output or not Path(output).is_file():
            raise RuntimeError("spotDL did not produce an output file")
        return Path(output)
