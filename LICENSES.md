# License inventory

This repository contains original application code and configuration. It does not vendor, copy, or modify spotDL, yt-dlp, EJS, Deno, FFmpeg, Next.js, or React source code. Dependencies are installed as their published packages or operating-system binaries during the image build.

| Dependency | Relationship | License |
|---|---|---|
| spotDL | Unmodified Python dependency | MIT |
| yt-dlp | Unmodified Python dependency | Unlicense |
| yt-dlp EJS | Unmodified Python dependency | Unlicense AND MIT AND ISC |
| Deno | Unmodified binary dependency | MIT |
| FFmpeg / ffprobe | Debian binary dependency | LGPL-2.1-or-later and optional GPL components; see the exact Debian build configuration |
| FastAPI | Unmodified Python dependency | MIT |
| Uvicorn | Unmodified Python dependency | BSD-3-Clause |
| Pydantic | Unmodified Python dependency | MIT |
| Requests | Unmodified Python dependency | Apache-2.0 |
| Mutagen (via spotDL) | Unmodified Python dependency | GPL-2.0-or-later |
| SQLite | Linked through Python standard library | Public domain |
| Next.js | Unmodified npm dependency | MIT |
| React / React DOM | Unmodified npm dependencies | MIT |

The complete transitive Python and npm dependency manifests are retained in the lockfiles. License texts and notices shipped by installed packages remain inside their distributions and container layers. `yt-dlp` also ships its `THIRD_PARTY_LICENSES.txt` in the installed distribution.

No upstream patches are applied. If a patch becomes necessary, it must be added under `patches/<project>/` with its upstream license notice, rationale, and compatibility test.

## Audited Python lock inventory

The following inventory covers every runtime package in `requirements.lock`. Names are grouped only to keep the audit readable; exact versions remain in the lockfile.

| License | Locked packages |
|---|---|
| MIT / MIT License | annotated-types, anyio, beautifulsoup4, brotli, charset-normalizer, curl_cffi, dacite, datastar-py, Deprecated, fastapi, h11, httptools, jaconv, markdown-it-py, mdurl, platformdirs, pydantic, pydantic_core, PyOTP, python-slugify, PyYAML, RapidFuzz, readerwriterlock, redis, rich, six, soundcloud-v2, soupsieve, spotDL, spotipy, spotipyFree, syncedlyrics, typing-inspection, urllib3, validators, ytmusicapi |
| MIT-0 | cffi |
| MIT-CMU | Pillow |
| BSD-3-Clause / BSD | click, colorama, idna, Jinja2, MarkupSafe, pycparser, PySocks, python-dotenv, starlette, uvicorn, websockets |
| BSD-2-Clause | Pygments, wrapt |
| Apache-2.0 | pymongo, python-multipart, requests |
| Apache-2.0 OR BSD-3-Clause | python-dateutil |
| MIT OR Apache-2.0 | sniffio |
| ISC | dnspython |
| MPL-2.0 | certifi |
| PSF-2.0 | typing_extensions |
| BSD-2-Clause and public-domain components | pycryptodomex |
| GPL-2.0-or-later | Mutagen, Unidecode |
| GPL-3.0-or-later | pykakasi |
| GPL-3.0-only | spotapi |
| Artistic-1.0 / GPL dual terms | text-unidecode |
| Unlicense | yt-dlp |
| Unlicense AND MIT AND ISC | yt-dlp-ejs |

The isolated test-stage additions are: coverage (Apache-2.0), httpcore (BSD), httpx (BSD-3-Clause), iniconfig (MIT), packaging (Apache-2.0 OR BSD-2-Clause), pluggy (MIT), pytest (MIT), and pytest-cov (MIT).

## Audited web lock inventory

The production dependency families in `apps/web/package-lock.json` are:

| License | Locked packages / package families |
|---|---|
| MIT | @emnapi/runtime, @img/colour, @next/env, all @next/swc platform packages, client-only, nanoid, Next.js, postcss, React, React DOM, scheduler, styled-jsx |
| Apache-2.0 | @swc/helpers, baseline-browser-mapping, detect-libc, sharp, and the @img/sharp platform packages except the mixed-license WASM/Windows entries |
| LGPL-3.0-or-later | all @img/sharp-libvips platform packages |
| Apache-2.0 AND LGPL-3.0-or-later, with MIT also applying to the WASM entry | @img/sharp-wasm32 and the mixed-license @img/sharp-win32 packages |
| CC-BY-4.0 | caniuse-lite data |
| ISC | picocolors, semver |
| BSD-3-Clause | source-map-js |
| 0BSD | tslib |

Development-only entries are @types/node, @types/react, @types/react-dom, csstype, and undici-types (MIT), plus TypeScript and all locked `@typescript/typescript-*` platform packages (Apache-2.0).

## Distribution note

The runtime includes copyleft dependencies brought in by spotDL, notably Mutagen, Unidecode, pykakasi, and spotapi. Anyone redistributing built container images must preserve the license files included in those distributions and comply with their corresponding source/notice obligations. The project does not copy those packages into its own source tree or relicense them under the repository's MIT license.
