from __future__ import annotations

from app.engine.ytdlp_adapter import YtDlpAdapter


def test_ytdlp_adapter_is_read_only_and_normalizes_candidates(monkeypatch):
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            captured["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, query, *, download):
            captured["query"] = query
            captured["download"] = download
            return {
                "entries": [
                    {
                        "id": "abc123",
                        "title": "Exact Song (Live)",
                        "channel": "Artist - Topic",
                        "duration": 201.25,
                    }
                ]
            }

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYoutubeDL)
    candidates = YtDlpAdapter(proxy="socks5h://127.0.0.1:1080").search(
        "Artist - Exact Song (Live)", limit=5
    )

    assert captured["query"] == "ytsearch5:Artist - Exact Song (Live)"
    assert captured["download"] is False
    assert captured["options"]["skip_download"] is True
    assert captured["options"]["proxy"] == "socks5h://127.0.0.1:1080"
    assert candidates[0].url == "https://www.youtube.com/watch?v=abc123"
    assert candidates[0].duration_ms == 201250


def test_ytdlp_adapter_rejects_unbounded_searches():
    adapter = YtDlpAdapter()
    for limit in (0, 26):
        try:
            adapter.search("song", limit=limit)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected a bounded-search validation error")
