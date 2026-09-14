# Pinned runtime dependencies

| Component | Pin | Inspected source | Role |
|---|---:|---|---|
| Python | 3.12.10 | Official slim-bookworm image | Backend runtime |
| spotDL | 4.5.2 | `cd4a4203f5b12bd6dbbdf22d7674807858d35e05` | Spotify resolution, matching orchestration, tagging |
| yt-dlp | 2026.08.19 | `bbc809a1161d3bfca51fa36f59dda35556ee85a0` | YouTube extraction and download |
| yt-dlp-ejs | 0.8.0 | `6f8587bb7009a1fc81038538071d2cb66b8b8ed0` | YouTube JavaScript challenge components |
| Deno | 2.6.3 | Versioned official release archive | JavaScript runtime recommended by yt-dlp |
| FFmpeg / ffprobe | Debian bookworm package | Debian package repository | Audio conversion and inspection |
| FastAPI | 0.103.2 | Published Python package | Local REST/SSE API; constrained by spotDL 4.5.2 |
| Uvicorn | 0.23.2 | Published Python package | ASGI server; constrained by spotDL 4.5.2 |
| SQLite | Python 3.12 runtime | Python standard library | Durable local job and library database |
| Node.js | 22.19.0 | Official Alpine image | Web build/runtime |
| Next.js | 16.3.5 | Published npm package | Local web interface |
| React | 19.3.0 | Published npm package | Interface runtime |

Python dependencies are locked in `requirements.lock`; web dependencies and their transitive graph are locked in `apps/web/package-lock.json`. Container base tags and the Deno archive version are declared in the Dockerfiles. The Deno AMD64 and ARM64 archives are verified against the SHA-256 values published with the immutable v2.6.3 release.

## Deliberate update procedure

1. Clone the latest spotDL, yt-dlp, and EJS releases outside the production image.
2. Review their changelogs and the integration seams documented in `RESEARCH.md`.
3. Update exact pins in `requirements.lock`, `pyproject.toml`, and Docker build arguments.
4. Regenerate `apps/web/package-lock.json` only when web dependencies change.
5. Run unit/integration tests, the 750-track fixture, a no-cache Compose build, restart smoke test, and an authorized single-track acceptance test.
6. Verify MP3 title, artist, album, and APIC artwork frames programmatically.

The containers never fetch a Git branch at startup.
