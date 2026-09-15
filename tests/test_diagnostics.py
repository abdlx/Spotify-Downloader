from __future__ import annotations

import tempfile
import traceback
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app.db as db
from app.core import classify_error
from app.diagnostics import record_attempt, retry_at
from app.engine.matcher import MusicMatcher
from app.engine.spotdl_adapter import SpotdlAdapter, SpotdlDownloadError


@dataclass(frozen=True)
class Candidate:
    name: str
    author: str
    duration: int
    result_id: str
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
        wrong = Candidate("Artist - Song", "Artist", 200, "wrong", "https://youtube.com/watch?v=wrong")
        right = Candidate("Artist - Song (Slowed)", "Artist", 201, "right", "https://youtube.com/watch?v=right")

        class Provider:
            def __init__(self, **kwargs):
                self.audio_handler = SimpleNamespace(params={})

            def get_results(self, query):
                return [wrong] if "official audio" not in query else [right]

        song = SimpleNamespace(artists=["Artist"], name="Song (Slowed)", duration=200,
                               display_name="Artist - Song (Slowed)")
        with patch("spotdl.providers.audio.youtube.YouTube", Provider), patch("spotdl.utils.matching.order_results", lambda results, song: {result: 95 for result in results}):
            result = MusicMatcher().find_match(song, {"low_confidence_threshold": 0.72})
        self.assertEqual(result.video_id, "right")
        self.assertIn("official audio", result.search_query)

    def test_retry_policy(self):
        self.assertIsNone(retry_at("FFMPEG_ERROR", 1))
        self.assertIsNone(retry_at("NO_MATCH", 1))
        self.assertGreater(retry_at("HTTP_429", 1, random_fraction=0)[1],
                           retry_at("NETWORK_ERROR", 1, random_fraction=0)[1])


if __name__ == "__main__":
    unittest.main()
