#!/usr/bin/env bash
# Pull logs off the device into logs/<timestamp>/ for inspection here.
#
#   make logs            last 2 days
#   make logs SINCE=1h   last hour
set -euo pipefail

cd "$(dirname "$0")/.."
# Preserve an environment override across the eval, which would otherwise
# replace it with the value from group_vars.
_env_address="${PI_ADDRESS:-}"
eval "$(uv run python scripts/config.py)"
PI_ADDRESS="${_env_address:-$PI_ADDRESS}"

SINCE="${SINCE:-2 days ago}"
TARGET="logs/$(date +%Y%m%d-%H%M%S)"
# PI_ADDRESS from the environment wins, for when mDNS is unavailable.
HOST="${PI_USER}@${PI_ADDRESS}"

mkdir -p "$TARGET"
echo "Fetching from $HOST since '$SINCE' into $TARGET/"

fetch() {
    local name="$1" unit="$2"
    echo "  $name"
    ssh "$HOST" "sudo journalctl -u '$unit' --since '$SINCE' --no-pager" \
        > "$TARGET/$name.log" 2>"$TARGET/$name.err" || {
            echo "    failed, see $TARGET/$name.err" >&2
        }
    [[ -s "$TARGET/$name.err" ]] || rm -f "$TARGET/$name.err"
}

fetch therminus therminus.service
fetch ebusd ebusd.service
fetch health therminus-health.service

echo "  system"
ssh "$HOST" "sudo journalctl --since '$SINCE' --no-pager -p warning" \
    > "$TARGET/system-warnings.log"

# ebusd writes its own log outside journald. The package rotates it at 1 MB.
echo "  ebusd.log"
ssh "$HOST" "sudo cat /var/log/ebusd.log" > "$TARGET/ebusd-daemon.log" 2>/dev/null || {
    echo "    not present" >&2
    rm -f "$TARGET/ebusd-daemon.log"
}

echo "  status"
{
    echo "=== uptime ==="
    ssh "$HOST" "uptime"
    echo
    echo "=== services ==="
    ssh "$HOST" "systemctl status therminus ebusd --no-pager -l" || true
    echo
    echo "=== timer ==="
    ssh "$HOST" "systemctl list-timers therminus-health --no-pager" || true
    echo
    echo "=== disk ==="
    ssh "$HOST" "df -h /"
    echo
    echo "=== memory ==="
    ssh "$HOST" "free -h"
    echo
    echo "=== bus ==="
    ssh "$HOST" "ebusctl -p ${EBUSD_PORT} info" || true
    echo
    echo "=== reboots ==="
    ssh "$HOST" "last -x reboot | head -20" || true
} > "$TARGET/status.txt" 2>&1

echo
echo "Written to $TARGET/"
ls -la "$TARGET"
