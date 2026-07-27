# ==============================================================================
#  Cadenza - multi-stage image (linux/amd64)
#  Target: Synology DS1821+ / DVA3221 (x86_64) running DSM 7.x
# ==============================================================================

# ---- Stage 1: build the web UI ----
FROM node:20-bookworm-slim AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


# ---- Stage 2: resolve Python dependencies ----
FROM python:3.12-slim-bookworm AS pydeps

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY backend/requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install -r requirements.txt


# ---- Stage 3: runtime ----
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="Cadenza" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.description="Music library curation for Synology DSM 7" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    CADENZA_MUSIC_ROOT=/music \
    CADENZA_QUARANTINE_ROOT=/quarantine \
    CADENZA_CONFIG_DIR=/config \
    CADENZA_HTTP_PORT=8760

# ffmpeg/ffprobe drive conversion and probing; libchromaprint-tools provides
# fpcalc for acoustic fingerprinting. Neither can be installed on DSM itself,
# which is the whole reason this package ships as a container.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libchromaprint-tools \
        nginx \
        tini \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

COPY --from=pydeps /opt/venv /opt/venv

WORKDIR /app
COPY backend/app /app/app
COPY --from=frontend /build/dist /app/www
COPY docker/nginx.conf /etc/nginx/conf.d/cadenza.conf
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Running as a non-root user matters here: the container writes into the user's
# own shared folder, and the files must stay owned by them, not by root.
ARG CADENZA_UID=1029
ARG CADENZA_GID=100
RUN groupadd -g ${CADENZA_GID} -o cadenza 2>/dev/null || true \
    && useradd -u ${CADENZA_UID} -g ${CADENZA_GID} -o -m -s /usr/sbin/nologin cadenza 2>/dev/null || true \
    && mkdir -p /config /music /quarantine /var/lib/nginx /var/log/nginx /run \
    && chown -R ${CADENZA_UID}:${CADENZA_GID} \
         /config /quarantine /app /var/lib/nginx /var/log/nginx /run

VOLUME ["/config", "/music", "/quarantine"]
EXPOSE 8760

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8760/health',timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
