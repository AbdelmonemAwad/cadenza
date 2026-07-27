#!/bin/sh
# Container entrypoint: nginx (static UI + reverse proxy) and uvicorn (API).
set -eu

log() { echo "[cadenza] $*"; }

# Run an explicit command instead of the service stack when one is given, so
# `docker run <image> ffmpeg -version` and `docker run <image> sh` behave the
# way anyone would expect. Compose passes no command, so normal startup is
# unaffected.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

: "${CADENZA_CONFIG_DIR:=/config}"
: "${CADENZA_MUSIC_ROOT:=/music}"
: "${CADENZA_QUARANTINE_ROOT:=/quarantine}"
: "${CADENZA_API_WORKERS:=1}"

mkdir -p "$CADENZA_CONFIG_DIR/logs" "$CADENZA_CONFIG_DIR/cache/artwork" "$CADENZA_QUARANTINE_ROOT"

if [ ! -d "$CADENZA_MUSIC_ROOT" ]; then
    log "WARNING: music folder $CADENZA_MUSIC_ROOT is not mounted in the container"
fi
if [ ! -w "$CADENZA_CONFIG_DIR" ]; then
    log "ERROR: $CADENZA_CONFIG_DIR is not writable - check the folder permissions on the NAS"
    log "       (the container runs as UID:GID $(id -u):$(id -g))"
    exit 1
fi

# Running as root would write root-owned files into the user's music share,
# which they then cannot manage from File Station. Warn loudly rather than
# silently doing damage.
if [ "$(id -u)" = "0" ]; then
    log "WARNING: running as root. Set 'user:' in docker-compose.yml to the"
    log "         account that owns your music folder (id <dsm-user>)."
fi

command -v ffmpeg >/dev/null 2>&1 || log "WARNING: ffmpeg missing, conversion disabled"
command -v fpcalc >/dev/null 2>&1 || log "WARNING: fpcalc missing, acoustic fingerprinting disabled"

start_api() {
    /opt/venv/bin/uvicorn app.main:app \
        --host 127.0.0.1 --port 8000 \
        --workers "$CADENZA_API_WORKERS" \
        --proxy-headers --forwarded-allow-ips='*' \
        --no-server-header
}

# Split roles let a future deployment run the API and UI as separate services.
if [ "${CADENZA_ROLE:-all}" = "api" ]; then
    log "starting uvicorn only (role=api)"
    exec start_api
fi

# nginx always listens on 8760 inside the container. It used to be rewritten
# here with `sed -i`, but that file is owned root:root, so the rewrite failed
# for any non-root user -- meaning the container could only ever start as root,
# the exact opposite of the intended posture. Changing the published port is
# Docker's job: edit the `ports:` mapping in docker-compose.yml.
log "starting nginx on :8760 and uvicorn on :8000"
nginx -g 'daemon off;' &
NGINX_PID=$!

trap 'log "shutting down"; kill -TERM $NGINX_PID 2>/dev/null || true' TERM INT

start_api &
API_PID=$!

# If either half dies the container exits, and Docker's restart policy brings
# the whole thing back cleanly rather than leaving a half-running service.
wait -n $NGINX_PID $API_PID
EXIT=$?
log "a component exited with status $EXIT - stopping the container"
kill -TERM $NGINX_PID $API_PID 2>/dev/null || true
exit $EXIT
