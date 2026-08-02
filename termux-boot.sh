#!/data/data/com.termux/files/usr/bin/bash

set -u

PREFIX="/data/data/com.termux/files/usr"
TERMUX_HOME="/data/data/com.termux/files/home"
LOG_DIR="$TERMUX_HOME/logs"
BOOT_LOG="$LOG_DIR/boot.log"
PROJECT_DIR="$TERMUX_HOME/projects/Storage"

export PREFIX
export HOME="$TERMUX_HOME"
export PATH="$PREFIX/bin:$PATH"

mkdir -p "$LOG_DIR"
exec >>"$BOOT_LOG" 2>&1

printf '\n[%s] Android boot sequence started\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
termux-wake-lock || printf '[%s] WARNING: wake lock failed\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"

if ! pgrep -x sshd >/dev/null 2>&1; then
    sshd
    printf '[%s] sshd started\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
fi

sleep 15
"$PROJECT_DIR/ensure-storage-native.sh"

if ! pgrep -f '[s]tart-localtunnel-native.sh' >/dev/null 2>&1; then
    nohup "$PREFIX/bin/bash" "$PROJECT_DIR/start-localtunnel-native.sh" >/dev/null 2>&1 &
    printf '%s\n' "$!" >"$LOG_DIR/native-localtunnel.pid"
    printf '[%s] Native LocalTunnel supervisor started pid=%s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$!"
fi

# Android JobScheduler periodically recreates the native supervisor if the OS
# removes only the Termux process while the phone stays powered on.
termux-job-scheduler \
    --job-id 5000 \
    --script "$PROJECT_DIR/ensure-storage-native.sh" \
    --period-ms 900000 \
    --persisted true >/dev/null 2>&1 || \
    printf '[%s] WARNING: could not register periodic recovery job\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"

printf '[%s] Android boot sequence completed\n' "$(date '+%Y-%m-%d %H:%M:%S %z')"
