#!/data/data/com.termux/files/usr/bin/bash

# One-shot entry point for Android JobScheduler. It recreates the long-running
# native supervisor if Android has removed it.
set -u

TERMUX_HOME="/data/data/com.termux/files/home"
LOG_DIR="$TERMUX_HOME/logs"
SUPERVISOR="$TERMUX_HOME/projects/Storage/start-storage-native.sh"
PID_FILE="$LOG_DIR/storage-supervisor.pid"
ENSURE_LOG="$LOG_DIR/storage-ensure.log"

mkdir -p "$LOG_DIR"
pid="$(cat "$PID_FILE" 2>/dev/null || true)"

if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    exit 0
fi

printf '[%s] Supervisor missing; starting it\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" >>"$ENSURE_LOG"
nohup "$SUPERVISOR" >/dev/null 2>&1 &
