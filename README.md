# Lilt — Local Music Library

Lilt is a private, single-user music utility that resolves public Spotify tracks, albums, and playlists, matches them to YouTube through spotDL/yt-dlp, and saves authorized media as tagged MP3 files on your own computer.

It runs locally. There are no accounts, analytics, hosted queues, or cloud database. Use it only for media you are authorized to download.

## Start

Install current Docker Desktop (or Docker Engine with Compose 2.24+), then:

```bash
git clone <repository-url>
cd <repository-directory>
docker compose up -d --build
```

Open <http://localhost:3000>.

That command builds and starts the web interface, local API, worker, database migrations, spotDL, yt-dlp, EJS, Deno, FFmpeg, and ffprobe. Do not install Python, Node, or any media tool on the host.

## How it works

1. Paste a public `open.spotify.com/track/...`, `/album/...`, or `/playlist/...` URL.
2. Select **Fetch**. Metadata resolution begins in the worker and every track is persisted as it is discovered.
3. Review the collection. No audio is downloaded during this stage.
4. Select **Download all**. The worker searches with the exact Spotify title and artists, preserving remix, slowed, reverb, acoustic, live, radio-edit, extended-mix, instrumental, and remaster distinctions.
5. Follow matching, downloading, conversion, tagging, and completion through live events in the interface.

MP3 files appear directly under:

```text
./downloads/<collection>/001 - Artist - Title.mp3
```

The folder is a bind mount, so the files are never trapped in a Docker-only volume.

## Architecture

```text
Browser :3000
  └─ Next.js web service
       └─ FastAPI local API
            ├─ SQLite /app/data/app.db
            ├─ Server-Sent Events
            └─ durable job commands
                 └─ worker (bounded concurrency 1–5; default 2)
                      └─ supervised track process
                           ├─ spotDL adapter
                           ├─ yt-dlp + EJS + Deno
                           ├─ FFmpeg / ffprobe
                           └─ MP3 metadata + artwork
```

The API never performs downloads. SQLite runs in WAL mode with a busy timeout. The worker claims queued rows, and each track executes in a separate process group so cancellation terminates the actual yt-dlp/FFmpeg tree. A pause stops new work and lets active tracks finish.

On startup, interrupted resolving jobs and non-terminal track states return to the queue. Completed rows remain complete. If a completed source track is requested again, Lilt safely hard-links or copies the existing MP3 instead of downloading the media again.

## Storage and backups

Persistent host paths:

| Host path | Container path | Contents |
|---|---|---|
| `./data` | `/app/data` | SQLite database, WAL files, metadata cache |
| `./downloads` | `/downloads` | Finished music and optional `cover.jpg` files |

Stop the stack before making a simple file-level database backup:

```bash
docker compose stop
```

Copy `data/` and `downloads/`, then restart with `docker compose start`.

## Settings

The Settings screen supports:

- MP3 bitrate: Auto, 128, 192, 256, or 320 kbps. “Auto” follows the available source; fixed values are transcode targets and do not imply higher source quality.
- Filename template with validated fields. Absolute paths and `..` segments are rejected.
- Download concurrency from 1–5.
- Official-source preference, duration tolerance, and low-confidence threshold.
- Optional `cover.jpg` next to completed tracks.
- Direct, HTTP(S), or SOCKS proxy connectivity. Credentials are stored locally and omitted from API responses and structured logs.

The internal download root is always `/downloads`; changing it in the browser is intentionally disabled because Compose owns the host mapping.

### Optional official Spotify API

spotDL 4.5.2 includes a credential-free Spotify client for public links. To use your own official Spotify application instead, copy `.env.example` to `.env` and set both:

```text
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
```

Never commit `.env`.

## Job behavior

- A track gets up to four attempts, with 10, 30, and 90 second delays between retries.
- One failed track does not abort its collection.
- **Retry failed** resets only failed rows.
- **Pause** prevents new rows from starting. Active tracks finish when practical.
- **Cancel** stops queued rows and terminates active child process groups.
- Below 512 MiB free, the job pauses with a disk-full message.
- Optional artwork failure produces `COMPLETE_WITH_WARNINGS`; successful audio is retained.
- Matching is automatic in v1. Confidence and chosen candidate metadata remain stored for review.

## Health and logs

The health endpoint is available through the web service:

```text
http://localhost:3000/api/health
```

It checks the database, worker heartbeat, downloads mount, spotDL, yt-dlp, EJS, Deno, FFmpeg, and ffprobe.

View structured logs:

```bash
docker compose logs -f
docker compose logs -f worker
```

Proxy passwords, authorization headers, cookies, and tokens are not written to application logs.

## Updates

```bash
git pull
docker compose up -d --build
```

The database migrates automatically and bind-mounted data remains in place. Read `DEPENDENCIES.md` before changing upstream pins.

## Development

Hot reload for the API and web interface:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Build the isolated engine test stage (tests and test dependencies are not shipped in the runtime image):

```bash
docker build --target test -f docker/engine.Dockerfile .
```

Run the full build/restart smoke check on Windows:

```powershell
./scripts/docker_smoke.ps1
```

The automated suite includes URL validation, filename safety, version extraction, confidence scoring, state transitions, retry policy, duplicate reuse, restart recovery, and a 750-track persistence/queue fixture. It does not download copyrighted media.

### Authorized real-track acceptance test

After the stack is healthy, choose a Spotify track whose corresponding media you are permitted to save:

```bash
docker compose exec api python /app/scripts/authorized_e2e.py \
  "https://open.spotify.com/track/..." --i-confirm-authorized
```

The script resolves the track, starts the real worker path, waits for completion, opens the produced MP3, and asserts title, artist, album, and embedded APIC artwork frames.

## Troubleshooting

**The page does not open**

Run `docker compose ps` and `docker compose logs api worker web`. Port 3000 may already be used; set `WEB_PORT=3010` in `.env` and reopen `http://localhost:3010`.

**Spotify link fails**

Confirm it is an HTTPS public Spotify track, album, or playlist link. Private and removed resources are not supported. Query parameters are accepted and removed during normalization.

**YouTube reports unavailable or rate-limited**

Wait for the bounded retry. Source availability changes over time. Lilt does not bypass CAPTCHAs, access controls, DRM, or anti-abuse protections. A legitimate network proxy can be configured in Settings.

**Files do not appear on the host**

Confirm `downloads/` is writable by Docker and inspect `/api/health`. On Linux, correct the host folder ownership for the Docker daemon user if required.

**Database recovery**

Keep `app.db`, `app.db-wal`, and `app.db-shm` together when copying a live database. The recommended backup procedure stops the stack first.

## API overview

The local REST API exposes:

```text
POST  /api/resolve
GET   /api/collections
GET   /api/collections/{id}
GET   /api/collections/{id}/tracks?offset=0&limit=100
POST  /api/collections/{id}/download
GET   /api/jobs
GET   /api/jobs/{id}
POST  /api/jobs/{id}/pause
POST  /api/jobs/{id}/resume
POST  /api/jobs/{id}/cancel
POST  /api/jobs/{id}/retry-failed
POST  /api/job-items/{id}/retry
GET   /api/settings
PATCH /api/settings
GET   /api/events
GET   /api/health
```

## Upstream and licenses

See `RESEARCH.md` for inspected commits and integration decisions, `DEPENDENCIES.md` for pins and the update procedure, and `LICENSES.md` for the dependency audit. No upstream source is vendored, modified, or patched.
