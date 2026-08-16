#!/usr/bin/env python3
"""
Write first-boot configuration to a freshly flashed SD card.

Run this instead of using Raspberry Pi Imager's "Edit Settings" dialog. The
Imager's customisation is unreliable on the Zero 2 W (it writes a malformed
WiFi country code into the kernel command line, and the device comes up with no
network — unrecoverable without a monitor). This writes the documented
custom.toml format directly, which the OS applies on first boot.

Flash the plain image with Imager, skip customisation entirely, leave the card
mounted, then run `make card`.
"""

import os
import secrets
import string
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "group_vars" / "all.yml"
PASSWORD_FILE = REPO / ".card-password"

# Raspberry Pi OS names the boot partition "bootfs"; older images used "boot".
CANDIDATE_VOLUMES = ["/Volumes/bootfs", "/Volumes/boot"]


def fail(message: str) -> "None":
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_config() -> dict:
    if not CONFIG.exists():
        fail(
            f"{CONFIG.relative_to(REPO)} not found.\n"
            "  Copy the example first: cp group_vars/all.example.yml group_vars/all.yml"
        )
    config = yaml.safe_load(CONFIG.read_text()) or {}

    required = [
        "pi_hostname",
        "pi_user",
        "pi_timezone",
        "wifi_ssid",
        "wifi_password",
        "wifi_country",
        "ssh_public_key_file",
    ]
    missing = [key for key in required if not config.get(key)]
    if missing:
        fail("missing settings in group_vars/all.yml: " + ", ".join(missing))

    # Values straight out of the example file would produce a card that boots
    # with no network, which is the one failure this script exists to prevent.
    placeholders = {
        key: value
        for key, value in config.items()
        if isinstance(value, str) and value.startswith("your-")
    }
    if placeholders:
        fail(
            "group_vars/all.yml still has example values: "
            + ", ".join(sorted(placeholders))
            + "\n  Fill these in before writing the card."
        )
    return config


def find_boot_volume(timeout: int = 180) -> Path:
    """
    Wait for the card's boot partition to appear.

    Raspberry Pi Imager ejects the card when it finishes writing, so it is
    normally not mounted at the moment this runs. Rather than failing and
    making you start over, wait for you to reinsert it.
    """
    deadline = time.monotonic() + timeout
    waiting = False

    while True:
        found = [Path(v) for v in CANDIDATE_VOLUMES if Path(v).is_dir()]

        if len(found) > 1:
            fail(
                "more than one boot partition mounted: "
                + ", ".join(str(f) for f in found)
            )

        if found:
            volume = found[0]
            # macOS reports the mount point slightly before the contents are
            # readable. cmdline.txt is on every Raspberry Pi OS image.
            for _ in range(20):
                if (volume / "cmdline.txt").exists():
                    if waiting:
                        print(f"Found {volume}.")
                    return volume
                time.sleep(0.5)
            fail(
                f"{volume} is mounted but has no cmdline.txt.\n"
                "  That does not look like a Raspberry Pi OS boot partition."
            )

        if not waiting:
            # flush: stdout is block-buffered when this is piped or logged, and
            # a prompt nobody sees is worse than no prompt.
            print(
                "Waiting for the SD card.\n"
                "  Raspberry Pi Imager ejects the card when it finishes, so unplug\n"
                "  the reader and plug it back in. Ctrl-C to cancel.",
                flush=True,
            )
            waiting = True

        if time.monotonic() > deadline:
            fail(
                "no boot partition appeared after "
                f"{timeout}s. Expected one of: " + ", ".join(CANDIDATE_VOLUMES)
            )

        time.sleep(2)


def check_supports_custom_toml(volume: Path) -> None:
    """
    Refuse to write custom.toml to an image that will not read it.

    Raspberry Pi OS moved first-boot configuration to cloud-init in the Trixie
    images: the boot partition carries user-data/meta-data/network-config and
    cmdline.txt no longer runs raspberrypi-sys-mods/firstboot, which is the
    script that consumes custom.toml. Writing the file to such an image
    succeeds and then does nothing, which on a headless device looks like a
    dead Pi.
    """
    cmdline = volume / "cmdline.txt"
    text = cmdline.read_text() if cmdline.exists() else ""

    if "raspberrypi-sys-mods/firstboot" in text:
        return

    cloud_init = (volume / "user-data").exists() and (volume / "meta-data").exists()
    if cloud_init:
        fail(
            "this image uses cloud-init, not custom.toml.\n"
            "  Its boot partition has user-data/meta-data and cmdline.txt does not\n"
            "  run raspberrypi-sys-mods/firstboot, so custom.toml would be ignored\n"
            "  and the Pi would boot with no network and no way in.\n"
            "\n"
            "  Reflash with a Bookworm image, which this repo supports:\n"
            "    Imager -> Choose OS -> Raspberry Pi OS (other)\n"
            "             -> Raspberry Pi OS Lite (Legacy, 64-bit)\n"
            "  Skip the customisation dialog, then run `make card` again."
        )

    fail(
        "cannot tell how this image handles first-boot configuration.\n"
        "  cmdline.txt does not run raspberrypi-sys-mods/firstboot, and there is\n"
        "  no cloud-init user-data either. Writing custom.toml would probably do\n"
        "  nothing. Check you flashed Raspberry Pi OS and did not already boot\n"
        "  this card once."
    )


def warn_if_desktop_image(volume: Path) -> None:
    """
    Point out a desktop image without refusing it.

    pi-gen builds stage2 as Lite and stage4/stage5 as the desktop variants, and
    records the stage in issue.txt. A desktop image works, but on a 512 MB Zero
    2 W it spends most of the RAM on a GUI nothing will ever display.
    """
    issue = volume / "issue.txt"
    if not issue.exists():
        return

    text = issue.read_text()
    if "stage2" in text:
        return
    if not any(stage in text for stage in ("stage3", "stage4", "stage5")):
        return

    print(
        "warning: this looks like a desktop image, not Lite.\n"
        "  It will work, but on a 512 MB Zero 2 W the desktop takes RAM and SD\n"
        "  writes from a machine that has no display attached. Lite is at:\n"
        "    Imager -> Choose OS -> Raspberry Pi OS (other)\n"
        "             -> Raspberry Pi OS Lite (Legacy, 64-bit)\n",
        file=sys.stderr,
    )


def read_public_key(path_spec: str) -> str:
    path = Path(os.path.expanduser(path_spec))
    if not path.exists():
        fail(
            f"SSH public key not found at {path}.\n"
            "  Generate one with: ssh-keygen -t ed25519"
        )
    key = path.read_text().strip()
    if not key.startswith(("ssh-", "ecdsa-", "sk-")):
        fail(f"{path} does not look like a public key. Point at the .pub file.")
    if "PRIVATE KEY" in key:
        fail(f"{path} is a private key. Point at the .pub file instead.")
    return key


def console_password() -> str:
    """
    A random password for the local console account.

    SSH is key-only, so this is never used over the network. It exists because
    the first-boot config requires an account password, and because it is the
    only way back in if you ever attach a keyboard and monitor. It is written
    to .card-password (gitignored) so it is not lost.
    """
    if PASSWORD_FILE.exists():
        existing = PASSWORD_FILE.read_text().strip()
        if existing:
            return existing
    alphabet = string.ascii_letters + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(24))
    PASSWORD_FILE.write_text(password + "\n")
    PASSWORD_FILE.chmod(0o600)
    return password


def toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render(config: dict, public_key: str, password: str) -> str:
    return f"""# Written by therminal (bootstrap/provision_card.py). Do not edit by hand.
# Applied once on first boot, then removed by the OS.
config_version = 1

[system]
hostname = {toml_string(config["pi_hostname"])}

[user]
name = {toml_string(config["pi_user"])}
password = {toml_string(password)}
password_encrypted = false

[ssh]
enabled = true
password_authentication = false
authorized_keys = [ {toml_string(public_key)} ]

[wlan]
ssid = {toml_string(config["wifi_ssid"])}
password = {toml_string(config["wifi_password"])}
password_encrypted = false
hidden = {str(bool(config.get("wifi_hidden", False))).lower()}
country = {toml_string(config["wifi_country"])}

[locale]
keymap = {toml_string(config.get("pi_keymap", "us"))}
timezone = {toml_string(config["pi_timezone"])}
"""


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    config = load_config()
    public_key = read_public_key(config["ssh_public_key_file"])

    if dry_run:
        print(render(config, public_key, "<generated at write time>"))
        return

    volume = find_boot_volume()
    check_supports_custom_toml(volume)
    warn_if_desktop_image(volume)
    password = console_password()

    target = volume / "custom.toml"
    if target.exists():
        print(f"note: overwriting existing {target}")

    target.write_text(render(config, public_key, password))

    # Belt and braces: this also enables sshd on releases that still honour it,
    # and is harmless where custom.toml already did.
    (volume / "ssh").touch()

    os.sync()

    print(f"wrote {target}")
    print()
    print("Next:")
    print(f"  1. Eject the card:  diskutil eject {volume}")
    print("  2. Put it in the Pi and power it up.")
    print("  3. First boot takes 2-3 minutes and reboots once. Then:")
    print(f"       ssh {config['pi_user']}@{config['pi_hostname']}.local")
    print("  4. When that works:  make setup")
    print()
    print(f"Console password (not used for SSH) saved to {PASSWORD_FILE.name}")


if __name__ == "__main__":
    main()
