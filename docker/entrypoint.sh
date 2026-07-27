#!/bin/sh
# Container entrypoint: nginx (static UI + reverse proxy) and uvicorn (API).
set -eu

log() { echo "[cadenza] $*"; }

: "${CADENZA_CONFIG_DIR:=/config}"
: "${CADENZA_MUSIC_ROOT:=/music}"
: "${CADENZA_QUARANTINE_ROOT:=/quarantine}"
: "${CADENZA_HTTP_PORT:=8760}"
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

log "starting nginx on :$CADENZA_HTTP_PORT and uvicorn on :8000"
sed -i "s/listen  *[0-9]\+;/listen ${CADENZA_HTTP_PORT};/" /etc/nginx/conf.d/cadenza.conf
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
