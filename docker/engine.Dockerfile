FROM python:3.12.10-slim-bookworm AS deno
ARG DENO_VERSION=2.6.3
ARG DENO_AMD64_SHA256=b3c24dc6f3982607896bd795fd6bcbdc53f3d11e8d8190b2a07fd1881eb1148a
ARG DENO_ARM64_SHA256=92c9496e8c71e6b18abf1f728d6223bb682749e4946f24589a7ef8972fec423e
ARG TARGETARCH=amd64
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && case "$TARGETARCH" in \
         amd64) deno_target="x86_64-unknown-linux-gnu"; deno_sha="$DENO_AMD64_SHA256" ;; \
         arm64) deno_target="aarch64-unknown-linux-gnu"; deno_sha="$DENO_ARM64_SHA256" ;; \
         *) echo "Unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
       esac \
    && curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${deno_target}.zip" -o /tmp/deno.zip \
    && echo "$deno_sha  /tmp/deno.zip" | sha256sum -c - \
    && unzip /tmp/deno.zip -d /usr/local/bin \
    && chmod 0755 /usr/local/bin/deno

FROM python:3.12.10-slim-bookworm AS engine-base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data \
    DOWNLOADS_DIR=/downloads \
    XDG_CACHE_HOME=/app/runtime/cache
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app/data /app/runtime/cache /downloads
COPY --from=deno /usr/local/bin/deno /usr/local/bin/deno
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY app ./app
COPY scripts ./scripts

FROM engine-base AS test
COPY requirements-test.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements-test.lock
COPY tests ./tests
RUN pytest

FROM engine-base AS runtime
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
