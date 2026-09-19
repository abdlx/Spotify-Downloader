from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.db as db
from app.validation import EXPECTED_TITLES, RECOVERY_PLAYLIST_ID, SAMPLE, create_validation_sample, validation_plan, validation_result


class ValidationSampleTests(unittest.TestCase):
    def test_sample_queues_exactly_ten_failed_tracks_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(db, "DB_PATH", Path(root) / "app.db"), patch.object(db, "DOWNLOADS_DIR", Path(root) / "downloads"):
                db.init_db()
                now = db.utcnow()
                chosen = {position for position, _ in SAMPLE}
                with db.transaction(immediate=True) as conn:
                    conn.execute("INSERT INTO collections(id,source,source_id,source_url,type,name,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                 ("collection", "spotify", RECOVERY_PLAYLIST_ID,
                                  "https://open.spotify.com/playlist/" + RECOVERY_PLAYLIST_ID,
                                  "playlist", "Recovery", "READY", now, now))
                    conn.execute("INSERT INTO jobs(id,collection_id,kind,status,total,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                                 ("source", "collection", "DOWNLOAD", "COMPLETE_WITH_ERRORS", 162, now, now))
                    for position in range(1, 163):
                        title = EXPECTED_TITLES.get(position, f"Other {position}")
                        track_id = f"track-{position}"
                        conn.execute("INSERT INTO tracks(id,source_id,source_url,title,artists_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                                     (track_id, track_id, "https://open.spotify.com/track/" + track_id,
                                      title, '["Artist"]', now, now))
                        conn.execute("INSERT INTO job_items(id,job_id,track_id,position,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                                     (f"item-{position}", "source", track_id, position,
                                      "FAILED" if position in chosen else "COMPLETE", now, now))
                plan = validation_plan("source")
                self.assertTrue(plan["ready"])
                self.assertEqual(plan["count"], 10)
                sample = create_validation_sample("source")
                self.assertEqual(sample["total"], 10)
                self.assertEqual(create_validation_sample("source")["id"], sample["id"])
                conn = db.connect()
                try:
                    rows = conn.execute("SELECT position FROM job_items WHERE job_id=? ORDER BY position", (sample["id"],)).fetchall()
                    self.assertEqual([row["position"] for row in rows], sorted(chosen))
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM job_items WHERE job_id=?", (sample["id"],)).fetchone()[0], 10)
                finally:
                    conn.close()
                report = validation_result(sample["id"])
                self.assertEqual((report["tested"], report["pending"]), (10, 10))

    def test_sample_refuses_wrong_source_or_unfinished_job(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(db, "DB_PATH", Path(root) / "app.db"), patch.object(db, "DOWNLOADS_DIR", Path(root) / "downloads"):
                db.init_db()
                now = db.utcnow()
                with db.transaction(immediate=True) as conn:
                    conn.execute("INSERT INTO collections(id,source,source_id,source_url,type,name,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                 ("collection", "spotify", "other", "https://open.spotify.com/playlist/other", "playlist", "Other", now, now))
                    conn.execute("INSERT INTO jobs(id,collection_id,kind,status,total,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                                 ("source", "collection", "DOWNLOAD", "DOWNLOADING", 162, now, now))
                self.assertFalse(validation_plan("source")["ready"])
                with self.assertRaises(ValueError):
                    create_validation_sample("source")


if __name__ == "__main__":
    unittest.main()
