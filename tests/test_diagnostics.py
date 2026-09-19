from __future__ import annotations

import json
import tempfile
import traceback
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.db as db
from app.core import calculate_match_confidence, classify_error, extract_version_tokens
from app.diagnostics import record_attempt, retry_at
from app.engine.matcher import MusicMatcher
from app.engine.spotdl_adapter import SpotdlAdapter, SpotdlDownloadError


@dataclass(frozen=True)
class Candidate:
    title: str
    channel: str
    duration_ms: int
    video_id: str
    url: str


class DiagnosticTests(unittest.TestCase):
    def test_attempt_retains_match_stage_raw_error_and_status(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(db, "DB_PATH", Path(root) / "app.db"), patch.object(db, "DOWNLOADS_DIR", Path(root) / "downloads"):
                db.init_db()
                now = db.utcnow()
                with db.transaction(immediate=True) as conn:
                    conn.execute("INSERT INTO collections(id,source,source_id,source_url,type,name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                 ("c", "spotify", "playlist", "https://open.spotify.com/playlist/playlist", "playlist", "test", now, now))
                    conn.execute("INSERT INTO tracks(id,source_id,source_url,title,artists_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                                 ("t", "spotify-track", "https://open.spotify.com/track/spotify-track", "Count on Me", '["Bruno Mars"]', now, now))
                    conn.execute("INSERT INTO jobs(id,collection_id,kind,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                                 ("j", "c", "DOWNLOAD", "DOWNLOADING", now, now))
                    conn.execute("INSERT INTO job_items(id,job_id,track_id,position,status,attempt,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                 ("i", "j", "t", 1, "DOWNLOADING", 1, now, now))
                item = {"id": "i", "track_id": "t", "attempt": 1, "source_id": "spotify-track",
                        "source_url": "https://open.spotify.com/track/spotify-track", "title": "Count on Me",
                        "artists": ["Bruno Mars"], "duration_ms": 197000}
                match = SimpleNamespace(url="https://www.youtube.com/watch?v=abc", video_id="abc",
                                        search_query="Bruno Mars - Count on Me", confidence=0.96)
                record_attempt(item, "DOWNLOADING", match=match)
                record_attempt(item, "DOWNLOADING", match=match, category="HTTP_429",
                               error=RuntimeError("HTTP Error 429: Too Many Requests"))
                conn = db.connect()
                try:
                    row = conn.execute("SELECT * FROM download_attempts WHERE job_item_id='i'").fetchone()
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM download_attempts").fetchone()[0], 1)
                    self.assertEqual(row["normalized_failure_category"], "HTTP_429")
                    self.assertEqual(row["http_status"], 429)
                    self.assertEqual(row["youtube_video_id"], "abc")
                    self.assertIn("Too Many Requests", row["raw_error"])
                finally:
                    conn.close()
                with db.transaction(immediate=True) as conn:
                    conn.execute("UPDATE job_items SET status='FAILED', error_code='HTTP_429' WHERE id='i'")
                from app.api import app, latest_download_diagnostics
                self.assertIn("/api/diagnostics/latest", {route.path for route in app.routes})
                response = latest_download_diagnostics(examples=15)
                payload = json.loads(response.body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(payload["diagnostic_categories"], {"HTTP_429": 1})
                self.assertIn("Too Many Requests", payload["examples"][0]["raw_error_tail"])

    def test_resolved_playlist_record_skips_spotify_refetch(self):
        track = {"title": "Count on Me", "artists": ["Bruno Mars"],
                 "source_id": "spotify-id", "source_url": "https://open.spotify.com/track/spotify-id",
                 "album": "Doo-Wops", "album_artist": "Bruno Mars", "duration_ms": 197000,
                 "disc_number": 1, "disc_count": None, "track_number": 3, "track_count": 12,
                 "genres": [], "metadata": {"album_id": "album-id"}}
        song = SpotdlAdapter.record_to_song(track, list_name="test", position=1, list_length=162)
        required = ("genres", "disc_count", "tracks_count", "track_number", "album_id", "album_artist")
        self.assertTrue(all(getattr(song, field) is not None for field in required))

    def test_spotdl_caught_exception_keeps_yt_dlp_cause(self):
        class Tracker:
            def notify_error(self, text, exception, finish=False):
                pass

        class Progress:
            def get_new_tracker(self, song):
                return Tracker()

        class FakeSpotdl:
            def __init__(self, **kwargs):
                self.downloader = SimpleNamespace(progress_handler=Progress(), audio_providers=[], errors=[])

            def download(self, song):
                root = RuntimeError("HTTP Error 429: Too Many Requests")
                wrapped = RuntimeError("YT-DLP download error")
                wrapped.__cause__ = root
                self.downloader.progress_handler.get_new_tracker(song).notify_error(
                    "".join(traceback.format_exception(wrapped)), wrapped, True)
                self.downloader.errors.append("YT-DLP download error")
                return song, None

        from spotdl.utils.config import GlobalConfig
        with patch("spotdl.Spotdl", FakeSpotdl), patch.object(GlobalConfig, "set_parameter"):
            with self.assertRaises(SpotdlDownloadError) as caught:
                SpotdlAdapter.download(SimpleNamespace(), {"network_mode": "direct",
                                     "filename_template": "{title}.{output-ext}", "bitrate": "auto"}, lambda *_: None)
        self.assertIn("HTTP Error 429", caught.exception.raw_error)
        self.assertEqual(classify_error(caught.exception)[0], "HTTP_429")

    def test_fallback_keeps_exact_version(self):
        wrong = Candidate("Artist - Song", "Artist", 200000, "wrong", "https://youtube.com/watch?v=wrong")
        right = Candidate("Artist - Song (Slowed)", "Artist", 201000, "right", "https://youtube.com/watch?v=right")

        class Adapter:
            def __init__(self, **kwargs):
                pass

            def search(self, query, *, limit=10):
                return [wrong] if "official audio" not in query else [right]

        song = SimpleNamespace(artists=["Artist"], name="Song (Slowed)", duration=200,
                               display_name="Artist - Song (Slowed)")
        with patch("app.engine.matcher.YtDlpAdapter", Adapter):
            result = MusicMatcher().find_match(song, {"low_confidence_threshold": 0.72})
        self.assertEqual(result.video_id, "right")
        self.assertIn("official audio", result.search_query)

    def test_confident_primary_match_does_not_run_fallback_queries(self):
        candidate = Candidate("Artist - Song", "Artist", 200000, "first", "https://youtube.com/watch?v=first")
        queries = []

        class Adapter:
            def __init__(self, **kwargs):
                pass

            def search(self, query, *, limit=10):
                queries.append(query)
                return [candidate]

        song = SimpleNamespace(artists=["Artist"], name="Song", duration=200,
                               display_name="Artist - Song")
        with patch("app.engine.matcher.YtDlpAdapter", Adapter):
            MusicMatcher().find_match(song, {"low_confidence_threshold": 0.72})
        self.assertEqual(queries, ["Artist - Song"])

    def test_spotify_refetch_failure_is_not_a_youtube_429(self):
        error = SpotdlDownloadError(
            "Error occurred while reinitializing song: HTTP 429",
            spotdl_error="Error occurred while reinitializing song: HTTP 429",
        )
        self.assertEqual(error.stage, "SPOTIFY_METADATA")
        self.assertEqual(classify_error(error)[0], "SPOTIFY_RATE_LIMIT")

    def test_mainstream_flat_candidates_find_exact_audio(self):
        candidates = [
            Candidate('"Tum Hi Ho" Aashiqui 2 Full Song With Lyrics', "T-Series", 268000,
                      "video-one", "https://www.youtube.com/watch?v=video-one"),
            Candidate("Tum Hi Ho (Lyrics)|Arijit Singh|Aashiqui 2", "Sankalp", 251000,
                      "video-two", "https://www.youtube.com/watch?v=video-two"),
            Candidate("Tum hi Ho: Arijit Singh: Aashiqui 2: Hq Audio", "Ali Ahmad flac", 266000,
                      "video-three", "https://www.youtube.com/watch?v=video-three"),
        ]

        class Adapter:
            def __init__(self, **kwargs):
                pass

            def search(self, query, *, limit=10):
                return candidates

        song = SimpleNamespace(artists=["Arijit Singh", "Mithoon"], name="Tum Hi Ho",
                               duration=262, display_name="Arijit Singh - Tum Hi Ho")
        with patch("app.engine.matcher.YtDlpAdapter", Adapter):
            match = MusicMatcher().find_match(song, {"low_confidence_threshold": 0.72})
        self.assertEqual(match.video_id, "video-three")
        self.assertGreaterEqual(match.confidence, 0.72)

    def test_original_does_not_select_lofi_flip(self):
        lofi = Candidate("Tum Hi Ho (Lo-fi Flip) - Arijit Singh | Mithoon", "Lo-fi 2307",
                         256000, "wrong-lofi", "https://www.youtube.com/watch?v=wrong-lofi")
        original = Candidate("Tum hi Ho: Arijit Singh: Aashiqui 2: Hq Audio", "Ali Ahmad flac",
                             266000, "original", "https://www.youtube.com/watch?v=original")

        class Adapter:
            def __init__(self, **kwargs):
                pass

            def search(self, query, *, limit=10):
                return [lofi, original]

        song = SimpleNamespace(artists=["Arijit Singh", "Mithoon"], name="Tum Hi Ho",
                               duration=262, display_name="Arijit Singh - Tum Hi Ho")
        with patch("app.engine.matcher.YtDlpAdapter", Adapter):
            result = MusicMatcher().find_match(song, {"low_confidence_threshold": 0.72})
        self.assertEqual(result.video_id, "original")
        self.assertIn("lofi", extract_version_tokens(lofi.title))

    def test_unicode_title_scoring_keeps_script(self):
        exact = calculate_match_confidence(
            track_title="\u0633\u0631 \u0627\u0644\u062d\u064a\u0627\u0629", artists=["Nagham Debal"], duration_ms=210000,
            candidate_title="\u0633\u0631 \u0627\u0644\u062d\u064a\u0627\u0629 - Nagham Debal", candidate_channel="Nagham Debal",
            candidate_duration_ms=210000,
        )
        wrong = calculate_match_confidence(
            track_title="\u0633\u0631 \u0627\u0644\u062d\u064a\u0627\u0629", artists=["Nagham Debal"], duration_ms=210000,
            candidate_title="\u064a\u0627 \u0644\u064a\u0644 - Nagham Debal", candidate_channel="Nagham Debal",
            candidate_duration_ms=210000,
        )
        self.assertGreater(exact, wrong)
        self.assertGreaterEqual(exact, 0.72)

    def test_movie_annotation_is_not_a_track_version(self):
        candidate = Candidate("Lyrical: Duniyaa | Singers: Akhil & Dhvani Bhanushali",
                              "21WaveMusic", 220000, "duniyaa", "https://www.youtube.com/watch?v=duniyaa")

        class Adapter:
            def __init__(self, **kwargs):
                pass

            def search(self, query, *, limit=10):
                return [candidate]

        song = SimpleNamespace(artists=["Akhil", "Dhvani Bhanushali", "Kunaal Vermaa"],
                               name='Duniyaa (From "Luka Chuppi")', duration=223,
                               display_name="Akhil - Duniyaa")
        with patch("app.engine.matcher.YtDlpAdapter", Adapter):
            match = MusicMatcher().find_match(song, {"low_confidence_threshold": 0.72})
        self.assertEqual(match.video_id, "duniyaa")
        self.assertGreaterEqual(match.confidence, 0.72)

    def test_modified_track_requires_creator_identity(self):
        candidate = Candidate("the neighbourhood - reflections (sped up)", "Unrelated Upload",
                              187000, "uncredited", "https://www.youtube.com/watch?v=uncredited")

        class Adapter:
            def __init__(self, **kwargs):
                pass

            def search(self, query, *, limit=10):
                return [candidate]

        song = SimpleNamespace(artists=["OURGRND"], name="Reflections - Sped Up",
                               duration=187, display_name="OURGRND - Reflections - Sped Up")
        with patch("app.engine.matcher.YtDlpAdapter", Adapter):
            with self.assertRaises(LookupError) as caught:
                MusicMatcher().find_match(song, {"low_confidence_threshold": 0.72})
        self.assertIn("rejected_identity", str(caught.exception))

    def test_no_match_and_age_verification_categories(self):
        self.assertEqual(classify_error(LookupError("No suitable YouTube match for Tum Hi Ho"))[0], "NO_MATCH")
        self.assertEqual(classify_error(RuntimeError("Sign in to confirm your age"))[0], "AUTH_REQUIRED")

    def test_retry_policy(self):
        self.assertIsNone(retry_at("FFMPEG_ERROR", 1))
        self.assertIsNone(retry_at("NO_MATCH", 1))
        self.assertGreaterEqual(retry_at("BOT_DETECTION", 1, random_fraction=0)[1], 600)
        self.assertGreater(retry_at("HTTP_429", 1, random_fraction=0)[1],
                           retry_at("NETWORK_ERROR", 1, random_fraction=0)[1])


if __name__ == "__main__":
    unittest.main()
