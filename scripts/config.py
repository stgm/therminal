#!/usr/bin/env python3
"""
Print settings from group_vars/all.yml as shell assignments.

Used by the shell scripts so connection details live in one place:

    eval "$(uv run python scripts/config.py)"
"""

import shlex
import sys
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "group_vars" / "all.yml"

EXPORTED = [
    "pi_hostname",
    "pi_user",
    "therminus_port",
    "ebusd_port",
    "therminus_src",
]


def address(config: dict) -> str:
    """
    How to reach the Pi from here.

    Mirrors the default in inventory.yml. Kept separate from pi_hostname so an
    IP address can be used when mDNS is unavailable, without renaming the
    device.
    """
    return config.get("pi_address") or f"{config['pi_hostname']}.local"


def main() -> None:
    if not CONFIG.exists():
        print(
            "error: group_vars/all.yml not found. "
            "Copy group_vars/all.example.yml first (make config).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    config = yaml.safe_load(CONFIG.read_text()) or {}

    # `--host` prints just the SSH target, for use in the Makefile.
    if "--host" in sys.argv:
        print(f"{config['pi_user']}@{address(config)}")
        return

    for key in EXPORTED:
        value = config.get(key)
        if value is not None:
            print(f"{key.upper()}={shlex.quote(str(value))}")
    print(f"PI_ADDRESS={shlex.quote(address(config))}")


if __name__ == "__main__":
    main()
