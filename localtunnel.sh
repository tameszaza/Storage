#!/usr/bin/env bash

set -u

PORT="${LOCALTUNNEL_PORT:-5000}"
SUBDOMAIN="${LOCALTUNNEL_SUBDOMAIN:-tames-storage}"
PUBLIC_URL="https://${SUBDOMAIN}.loca.lt"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
STARTUP_GRACE_SECONDS="${LOCALTUNNEL_STARTUP_GRACE_SECONDS:-30}"
HEALTHCHECK_INTERVAL_SECONDS="${LOCALTUNNEL_HEALTHCHECK_INTERVAL_SECONDS:-20}"
MAX_HEALTHCHECK_FAILURES="${LOCALTUNNEL_MAX_HEALTHCHECK_FAILURES:-3}"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$*"
}

stop_tunnel() {
    if kill -0 "$tunnel_pid" 2>/dev/null; then
        kill "$tunnel_pid" 2>/dev/null || true
    fi
    wait "$tunnel_pid" 2>/dev/null || true
}

log "Starting LocalTunnel for ${PUBLIC_URL} -> http://127.0.0.1:${PORT}"
NODE_PATH="${NODE_PATH:-$(npm root --global)}" \
    node "$SCRIPT_DIR/localtunnel-client.js" "$PORT" "$SUBDOMAIN" &
tunnel_pid=$!

trap 'stop_tunnel; exit 130' INT
trap 'stop_tunnel; exit 143' TERM

if ! command -v curl >/dev/null 2>&1; then
    log "curl is unavailable; tunnel health monitoring is disabled"
    wait "$tunnel_pid"
    exit $?
fi

sleep "$STARTUP_GRACE_SECONDS"
healthcheck_failures=0

while kill -0 "$tunnel_pid" 2>/dev/null; do
    response_headers=""
    curl_exit=0
    response_headers="$(
        curl --silent --show-error \
            --max-time 10 \
            --dump-header - \
            --output /dev/null \
            "$PUBLIC_URL" 2>&1
    )" || curl_exit=$?

    if (( curl_exit != 0 )) ||
        grep -qi '^x-localtunnel-status:.*tunnel unavailable' <<<"$response_headers"; then
        ((healthcheck_failures += 1))
        log "Tunnel health check failed (${healthcheck_failures}/${MAX_HEALTHCHECK_FAILURES})"
    else
        if (( healthcheck_failures > 0 )); then
            log "Tunnel health check recovered"
        fi
        healthcheck_failures=0
    fi

    if (( healthcheck_failures >= MAX_HEALTHCHECK_FAILURES )); then
        log "Tunnel is unavailable while the client is still running; restarting it"
        stop_tunnel
        exit 75
    fi

    sleep "$HEALTHCHECK_INTERVAL_SECONDS"
done

wait "$tunnel_pid"
exit $?
