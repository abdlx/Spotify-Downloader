# Upstream research

Research was performed against fresh shallow clones on 2026-09-14 before application code was written.

## spotDL

- Inspected commit `cd4a4203f5b12bd6dbbdf22d7674807858d35e05`, release 4.5.2.
- `Playlist.get_metadata` and `Album.get_metadata` follow Spotify's `next` links until exhaustion. The application uses the same `SpotifyClient` boundary but persists each page's tracks incrementally rather than waiting for a fully materialized list.
- `Song` is the canonical metadata model. `Downloader.async_search_and_download` handles yt-dlp retrieval, FFmpeg conversion, Mutagen tags, artwork, temporary-file cleanup, and existing-file skip behavior.
- `AudioProvider.search`, `order_results`, and `get_best_result` are the supported matching seam. Our `MusicMatcher` preserves spotDL's result ordering and adds an explicit 0–1 confidence score with exact version-token consistency.
- `ProgressHandler(update_callback=...)` is the stable progress seam used to map upstream stages to our durable states.
- No spotDL source changes or patches are required.

## yt-dlp

- Inspected repository commit `bbc809a1161d3bfca51fa36f59dda35556ee85a0`; the current stable package version in that checkout is 2026.08.19.
- The Python `YoutubeDL` API is already used by spotDL. `YtDlpAdapter` exposes a bounded, read-only search seam for future manual matching while all v1 downloads continue through spotDL; there is no duplicate download pipeline.
- Current YouTube support enables Deno by default and recommends the external EJS package. Both are installed in the worker/API image.
- No yt-dlp source changes or patches are required.

## yt-dlp EJS

- Inspected commit `6f8587bb7009a1fc81038538071d2cb66b8b8ed0`.
- yt-dlp pins the `yt-dlp-ejs` Python distribution to 0.8.0. The image installs that exact package.
- The EJS release workflow uses Deno 2.6.3; the image pins that runtime.

## Integration decision

The application imports upstream code only from `app/engine/`. URL resolution and matching are isolated from database and API code. Each active download runs in its own child process group. This supplies a hard cancellation boundary around yt-dlp, Deno, and FFmpeg without forking or patching any upstream project.
