#!/data/data/com.termux/files/usr/bin/bash

# Native Termux supervisor. It survives an Ubuntu/PRoot task failure and can
# rebuild the complete PRoot keeper when Flask or tmux disappears.
set -u

PREFIX="/data/data/com.termux/files/usr"
TERMUX_HOME="/data/data/com.termux/files/home"
PROJECT_DIR="$TERMUX_HOME/projects/Storage"
LOG_DIR="$TERMUX_HOME/logs"
SUPERVISOR_LOG="$LOG_DIR/storage-supervisor.log"
PROOT_LOG="$LOG_DIR/storage-proot.log"
PID_FILE="$LOG_DIR/storage-supervisor.pid"
PROOT_PID_FILE="$LOG_DIR/storage-proot.pid"
CHECK_INTERVAL="${STORAGE_CHECK_INTERVAL_SECONDS:-30}"
FAILURES_BEFORE_REPAIR="${STORAGE_FAILURES_BEFORE_REPAIR:-3}"
FAILURES_BEFORE_RECYCLE="${STORAGE_FAILURES_BEFORE_RECYCLE:-8}"
HEALTH_URL="${STORAGE_HEALTH_URL:-http://127.0.0.1:5000/}"

export PREFIX
export HOME="$TERMUX_HOME"
export PATH="$PREFIX/bin:$PATH"

mkdir -p "$LOG_DIR"
exec >>"$SUPERVISOR_LOG" 2>&1

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$*"
}

pid_is_running() {
    [[ -n "${1:-}" ]] && kill -0 "$1" 2>/dev/null
}

proot_keeper_pid() {
    pgrep -f 'proot.*start-phone-tasks.sh.*sleep infinity' | head -n 1
}

storage_task_pids() {
    {
        pgrep -f '^/root/projects/flask-test/.venv/bin/python .*app.run' || true
        pgrep -f '^bash /root/projects/Storage/start-phone-tasks.sh __run_forever myserver ' || true
        pgrep -f '^tmux new-session -d -s myserver ' || true
    } | sort -u
}

start_proot_keeper() {
    local current_pid
    current_pid="$(proot_keeper_pid || true)"
    if pid_is_running "$current_pid"; then
        printf '%s\n' "$current_pid" >"$PROOT_PID_FILE"
        log "Ubuntu PRoot keeper already running pid=$current_pid"
        return 0
    fi

    log "Starting Ubuntu PRoot keeper"
    nohup "$PREFIX/bin/proot-distro" login ubuntu \
        --bind "$TERMUX_HOME/projects:/root/projects" \
        -- /bin/bash -lc '/root/projects/Storage/start-phone-tasks.sh; exec sleep infinity' \
        >>"$PROOT_LOG" 2>&1 &
    printf '%s\n' "$!" >"$PROOT_PID_FILE"
    log "Ubuntu PRoot keeper launched pid=$!"
}

repair_runner() {
    log "Requesting Ubuntu runner repair"
    "$PREFIX/bin/proot-distro" login ubuntu \
        --bind "$TERMUX_HOME/projects:/root/projects" \
        -- /bin/bash -lc '/root/projects/Storage/start-phone-tasks.sh' \
        >>"$PROOT_LOG" 2>&1 || log "Runner repair command failed code=$?"
}

recycle_proot() {
    local pid task_pids
    task_pids="$(storage_task_pids)"
    if [[ -n "$task_pids" ]]; then
        log "Stopping stale Ubuntu storage tasks pids=$(tr '\n' ',' <<<"$task_pids" | sed 's/,$//')"
        while read -r task_pid; do
            pid_is_running "$task_pid" && kill "$task_pid" 2>/dev/null || true
        done <<<"$task_pids"
    fi

    pid="$(proot_keeper_pid || true)"
    if pid_is_running "$pid"; then
        log "Recycling unresponsive Ubuntu PRoot keeper pid=$pid"
        kill "$pid" 2>/dev/null || true
    fi

    sleep 5
    while read -r task_pid; do
        pid_is_running "$task_pid" && kill -9 "$task_pid" 2>/dev/null || true
    done <<<"$task_pids"
    pid_is_running "$pid" && kill -9 "$pid" 2>/dev/null || true
    start_proot_keeper
}

health_ok() {
    curl --silent --show-error --fail --max-time 8 --output /dev/null "$HEALTH_URL"
}

cleanup() {
    log "Native storage supervisor stopping signal=${1:-EXIT}"
    rm -f "$PID_FILE"
}

existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if pid_is_running "$existing_pid" && [[ "$existing_pid" != "$$" ]]; then
    log "Supervisor already running pid=$existing_pid"
    exit 0
fi

printf '%s\n' "$$" >"$PID_FILE"
trap 'cleanup TERM; exit 143' TERM
trap 'cleanup INT; exit 130' INT
trap 'cleanup HUP; exit 129' HUP
trap 'cleanup EXIT' EXIT

termux-wake-lock || log "WARNING: termux-wake-lock failed"
log "Native storage supervisor started pid=$$"
start_proot_keeper

failures=0
while true; do
    if health_ok; then
        keeper_pid="$(proot_keeper_pid || true)"
        if ! pid_is_running "$keeper_pid"; then
            log "Flask is healthy but its PRoot keeper is missing; recreating keeper"
            start_proot_keeper
        fi
        if (( failures > 0 )); then
            log "Flask health check recovered after $failures failure(s)"
        fi
        failures=0
    else
        ((failures += 1))
        keeper_pid="$(proot_keeper_pid || true)"
        log "Flask health check failed count=$failures keeper_pid=${keeper_pid:-missing}"

        if ! pid_is_running "$keeper_pid"; then
            start_proot_keeper
        elif (( failures == FAILURES_BEFORE_REPAIR )); then
            repair_runner
        elif (( failures >= FAILURES_BEFORE_RECYCLE )); then
            recycle_proot
            failures=0
        fi
    fi
    sleep "$CHECK_INTERVAL"
done
