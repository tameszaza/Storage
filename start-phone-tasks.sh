#!/usr/bin/env bash

# Runs inside Ubuntu/PRoot. The outer lifecycle is supervised by native Termux.
set -u

LOG_DIR="/root/logs"
MAIN_LOG="$LOG_DIR/startup.log"
PROJECTS_DIR="/root/projects"
STORAGE_DIR="$PROJECTS_DIR/Storage"
VENV_PYTHON="$PROJECTS_DIR/flask-test/.venv/bin/python"
SERVER_LOG="$STORAGE_DIR/server.log"
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"

mkdir -p "$LOG_DIR"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$*"
}

server_event() {
    printf '[%s] SUPERVISOR %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$*" >>"$SERVER_LOG"
}

describe_exit() {
    case "$1" in
        0) printf 'normal exit' ;;
        1) printf 'application/startup error' ;;
        134) printf 'aborted (SIGABRT)' ;;
        137) printf 'killed (SIGKILL; often Android or memory pressure)' ;;
        139) printf 'segmentation fault (SIGSEGV)' ;;
        143) printf 'terminated (SIGTERM)' ;;
        *)
            if (( "$1" > 128 )); then
                printf 'terminated by signal %s' "$(("$1" - 128))"
            else
                printf 'exit code %s' "$1"
            fi
            ;;
    esac
}

run_forever() {
    local session_name="$1"
    local command="$2"
    local task_log="$LOG_DIR/$session_name.log"
    local exit_code reason started_at

    exec >>"$task_log" 2>&1
    log "Task runner started session=$session_name"
    server_event "runner started session=$session_name"

    while true; do
        started_at="$(date '+%Y-%m-%d %H:%M:%S %z')"
        log "Launching task session=$session_name"
        server_event "launching session=$session_name"

        PYTHONFAULTHANDLER=1 PYTHONUNBUFFERED=1 bash -lc "$command"
        exit_code=$?
        reason="$(describe_exit "$exit_code")"

        log "Task stopped session=$session_name code=$exit_code reason=$reason started=$started_at"
        server_event "task stopped session=$session_name code=$exit_code reason=$reason; restarting in 10 seconds"
        sleep 10
    done
}

start_tmux_task() {
    local session_name="$1"
    local command="$2"
    local tmux_command

    if tmux has-session -t "$session_name" 2>/dev/null; then
        log "tmux session '$session_name' is already running"
        return 0
    fi

    printf -v tmux_command '%q __run_forever %q %q' \
        "$SCRIPT_PATH" "$session_name" "$command"

    log "Starting tmux session '$session_name'"
    tmux new-session -d -s "$session_name" "$tmux_command"
    sleep 1

    if tmux has-session -t "$session_name" 2>/dev/null; then
        log "tmux session '$session_name' started"
    else
        log "ERROR: tmux session '$session_name' failed to start"
        server_event "ERROR tmux session '$session_name' failed to start; inspect $LOG_DIR/$session_name.log"
        return 1
    fi
}

if [[ "${1:-}" == "__run_forever" ]]; then
    run_forever "$2" "$3"
    exit 0
fi

exec >>"$MAIN_LOG" 2>&1
log "Checking Ubuntu startup tasks"

if [[ ! -d "$STORAGE_DIR" ]]; then
    log "ERROR: Storage directory not found: $STORAGE_DIR"
    exit 1
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
    log "ERROR: Flask Python not found: $VENV_PYTHON"
    server_event "ERROR Flask Python not found: $VENV_PYTHON"
    exit 1
fi

export SPEED_GPS_COMMAND="${SPEED_GPS_COMMAND:-/data/data/com.termux/files/usr/bin/termux-location -p gps -r once}"
export SPEED_GPS_TIMEOUT_SECONDS="${SPEED_GPS_TIMEOUT_SECONDS:-8}"

start_tmux_task "myserver" \
    "cd $STORAGE_DIR && exec $VENV_PYTHON -X faulthandler -c 'from app import app; app.run(debug=False, host=\"0.0.0.0\", port=5000)'"

log "Ubuntu startup tasks checked"
