#!/data/data/com.termux/files/usr/bin/bash

set -u

PROJECT_DIR="${HOME}/projects/Storage"
LOG_DIR="${HOME}/logs"
TASK_LOG="${LOG_DIR}/native-localtunnel.log"

mkdir -p "$LOG_DIR"
exec >>"$TASK_LOG" 2>&1

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$*"
}

log "Native Termux LocalTunnel supervisor started"

while true; do
    log "Launching LocalTunnel"
    bash "${PROJECT_DIR}/localtunnel.sh"
    exit_code=$?
    log "LocalTunnel exited with code ${exit_code}; restarting in 10 seconds"
    sleep 10
done
