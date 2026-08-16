#!/usr/bin/env bash
# Copy the device's mutable state and effective configuration into
# backups/<date>/.
#
# Everything the playbooks manage can be rebuilt from this repo, so the only
# thing here that cannot be regenerated is history.json.
set -euo pipefail

cd "$(dirname "$0")/.."
# Preserve an environment override across the eval, which would otherwise
# replace it with the value from group_vars.
_env_address="${PI_ADDRESS:-}"
eval "$(uv run python scripts/config.py)"
PI_ADDRESS="${_env_address:-$PI_ADDRESS}"

TARGET="backups/$(date +%Y%m%d-%H%M%S)"
# PI_ADDRESS from the environment wins, for when mDNS is unavailable.
HOST="${PI_USER}@${PI_ADDRESS}"

mkdir -p "$TARGET"
echo "Backing up $HOST into $TARGET/"

echo "  history.json"
scp -q "$HOST:/var/lib/therminus/history.json" "$TARGET/history.json" || {
    echo "    no history.json yet" >&2
}

echo "  effective configuration"
{
    echo "=== /etc/default/ebusd ==="
    ssh "$HOST" "cat /etc/default/ebusd"
    echo
    echo "=== therminus.service ==="
    ssh "$HOST" "systemctl cat therminus" || true
    echo
    echo "=== ebusd drop-ins ==="
    ssh "$HOST" "systemctl cat ebusd" || true
    echo
    echo "=== installed packages (manual) ==="
    ssh "$HOST" "apt-mark showmanual"
    echo
    echo "=== os ==="
    ssh "$HOST" "cat /etc/os-release; uname -a"
} > "$TARGET/config.txt" 2>&1

echo "  app tree checksum"
ssh "$HOST" "find /opt/therminus/app -type f -not -path '*/__pycache__/*' -exec md5sum {} + | sort -k2" \
    > "$TARGET/app-checksums.txt" 2>/dev/null || true

echo
echo "Written to $TARGET/"
ls -la "$TARGET"
