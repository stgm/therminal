# therminal

Provisioning and deployment for [Therminus](../therminus) on a Raspberry Pi
Zero 2 W.

The Pi runs two things: **ebusd**, talking to an eBUS Adapter Shield v5 over the
network, and **Therminus**, talking to ebusd on `127.0.0.1:8888`.

Everything is driven from your computer. Nothing in this repo is installed on the Pi
except the files it writes there, and nothing deployment-related lives in the
Therminus repo.

```
 Computer ──ansible/rsync──▶ Pi Zero 2 W ──────┐
                             ├─ therminus:6790 │ web UI + /roomtemp
                             └─ ebusd:8888 ────┴──WiFi──▶ Adapter Shield v5 ──▶ heat pump
```

---

## Requirements

- A Raspberry Pi Zero 2 W and a microSD card (8 GB is plenty)
- An eBUS Adapter Shield v5 already on your network and reachable by IP
- `uv` on the host computer
- An SSH key (`~/.ssh/id_ed25519.pub`). Create one with `ssh-keygen -t ed25519`
  if you don't have it.

No monitor, keyboard, or USB adapter is needed at any point.

---

## Setup

### 1. Configure

```bash
make hooks
make config
```

`make hooks` points git at `.githooks/` and only needs running once per clone —
see [Secrets](#secrets) for what it guards against.

That copies `group_vars/all.example.yml` to `group_vars/all.yml`. Open it and
fill in at least your WiFi details and the adapter's IP address. The file is
gitignored because it holds your WiFi password.

Give the adapter a DHCP reservation in your router while you're there. If its
address moves, ebusd loses the bus.

### 2. Flash the card

In Raspberry Pi Imager:

1. **Choose OS → `Raspberry Pi OS (other)` → `Raspberry Pi OS Lite (64-bit)`.**
   Lite is not in the top-level list — the entry called "Raspberry Pi OS
   (64-bit)" is the full desktop image. Do not use that one: on a 512 MB Zero
   2 W it is slow, and if first-boot configuration fails it drops into a
   graphical setup wizard that waits for a keyboard you do not have.
2. Pick the entry named **`Raspberry Pi OS Lite (Legacy, 64-bit)`**. Imager
   currently labels every Bookworm image "Legacy" simply because Trixie is the
   current stable Debian — it is not deprecated or unsupported, and it still
   gets security updates. The non-legacy Lite entry is Trixie. See
   [Notes on choices](#notes-on-choices) for why Bookworm here.
3. **Skip the customisation dialog** — click "No" when it offers to apply OS
   customisation settings.

This matters. Imager's customisation is unreliable on the Zero 2 W: it writes a
malformed WiFi country code into the kernel command line and the Pi boots with
no network ([rpi-imager#1543](https://github.com/raspberrypi/rpi-imager/issues/1543)).
On a headless device that is unrecoverable — you would have to pull the card and
start again. This repo writes the first-boot configuration itself instead.

Leave the card mounted and run:

```bash
make card
```

(`make card-check` prints what it would write, without needing a card. It also
refuses to run while any example values are still in place — a card written with
`your-network` as the SSID would boot with no network, which is exactly the
failure this avoids.)

This writes `custom.toml` to the card's boot partition with your hostname, user,
SSH public key, WiFi credentials, and timezone. It also generates a random
console password and saves it to `.card-password` (gitignored) — SSH is key-only
so you will never need it over the network, but it is the way back in if you
ever attach a keyboard.

Eject the card, put it in the Pi, power it up. First boot takes two to three
minutes and reboots once.

### 3. Check it came up

```bash
ping therminus.local
ssh youruser@therminus.local
```

Substitute your own hostname and user from `group_vars/all.yml`. The hostname is
set by `custom.toml` during first boot and Raspberry Pi OS runs avahi by
default, so `.local` works without anything from this repo having run yet.

If it does not resolve after five minutes, the Pi may still be perfectly fine —
mDNS is the fragile part. Check your router's DHCP list for the device, and if
it is there, continue with `make setup PI_ADDRESS=<its ip>`. See
[Troubleshooting](#troubleshooting).

### 4. Provision

```bash
make setup
```

Ten to twenty minutes on a Zero 2 W, mostly the initial `apt` upgrade. It
installs and configures ebusd, deploys Therminus, and sets up the watchdog and
health check. At the end it prints the web interface address.

### 5. Repoint your room temperature source

Therminus needs a room temperature feed to function, so configure a POST request:

```
http://therminus.local:6790/roomtemp
```

Until a room temp is first posted, the heat pump is left
alone, which is the safe failure mode, but nothing will happen.

---

## Day to day

```bash
make deploy       # push app changes from ~/dev/therminus and restart
make status       # service status and current controller state
make logs         # pull logs into logs/<timestamp>/
make backup       # pull history.json and effective config into backups/
make ebus-config  # update the eBUS message definitions checkout
make ssh          # shell on the device
```

`make deploy` rsyncs your working copy, so you do not need to commit or push to
try a change. `make help` lists everything.

---

## What gets configured

### Base (`roles/base`)

Hostname, timezone, avahi so the device answers to `<hostname>.local`, and `uv`
in `/usr/local/bin`.

SSH is key-only. The playbook refuses to disable password authentication unless
it can first confirm an authorized key is present — on a headless device that
mistake is unrecoverable.

Journals are persistent but capped at 64 MB with a one-month retention, and
synced every five minutes rather than per message. Persistent because `make
logs` needs history after a crash; capped because a failure loop would otherwise
wear out the card. Swappiness drops to 10 for the same reason.

Unattended security upgrades are on, with automatic reboot explicitly **off** —
a reboot chosen by apt at 06:00 would restart the controller without a rest
period.

### ebusd (`roles/ebusd`)

Installs the `.deb` from the [ebusd releases](https://github.com/john30/ebusd/releases)
matching the CPU architecture and OS release. Run as:

```
EBUSD_OPTS="-d ens:192.168.1.50:9999 -p 8888 -c /opt/ebus-config/en \
  --scanconfig --latency=20 --httpport=8889 --enablehex --accesslevel=*"
```

`ens:` is the enhanced protocol over TCP, which is what the v5 adapter speaks in
WiFi mode.

`-f`, `-l` and `--pidfile` command line are deliberately
absent. The packaged unit is `Type=forking` with `PIDFile=/var/run/ebusd.pid`,
and ebusd's default log destination is `/var/log/ebusd.log`, which the package
already rotates at 1 MB. `make logs` collects that file alongside the journals.

The packaged unit also already has `Restart=always`. The only thing it lacks is
`Wants=network-online.target` — it orders itself *after* that target without
ever pulling it in, which on a WiFi-only device means it can start before the
network is up. A drop-in adds it.

**Message definitions** come from a clone of
[eBUS/ebus.github.io](https://github.com/eBUS/ebus.github.io) (or your fork of it) at
`/opt/ebus-config`, passed as `-c /opt/ebus-config/en`, rather than being
fetched from `cfg.ebusd.eu`. The clone is **not** updated by `make setup` — a
silent `git pull` could change how the pump is addressed without anyone
deciding to. Update it deliberately:

```bash
make ebus-config
```

### Therminus (`roles/therminus`)

```
/opt/therminus/app     the app, rsynced
/opt/therminus/venv    virtualenv, built with uv against system Python 3.11
/var/lib/therminus     working directory — history.json lives here
```

Runs as a `therminus` system user under a sandboxed systemd unit
(`ProtectSystem=strict`, `ReadWritePaths=/var/lib/therminus`, a system call
filter, no home access).

Two details worth knowing:

- `RestartSec=30`, not the default 100 ms. A crash loop that restarted instantly
  would reinitialise the controller over and over.
- The unit passes `--initial-state RESTING`, so the controller waits out a full
  rest period after every boot. A power cut followed by a reboot loop therefore
  cannot short-cycle the compressor. Set `therminus_initial_state: IDLE` in
  `group_vars/all.yml` if you would rather it be able to heat on the first tick
  after boot.

### Health and recovery (`roles/health`)

The systemd hardware watchdog is armed at 14 seconds, so a kernel that stops
scheduling resets the board.

Above that, a timer runs `/usr/local/bin/therminus-health` every five minutes.
It opens `/api/stream` and reads the snapshot the app sends on connect — that
gives liveness, the controller state, and the app's own view of bus health from
one request, without needing a health endpoint added to Therminus. Separately it
runs `ebusctl info`.

Failures escalate per subsystem: three consecutive failures restart the relevant
service, twelve (about an hour) reboot the device, and reboots are rate-limited
to one per hour so a fault the reboot cannot fix does not become a boot loop.
The app's own `bus.ok` flag is only believed once it reports at least three
missed reads, because a freshly started process reports an unhealthy bus before
the first room temperature ever arrives.

---

## Secrets

Nothing secret is committed. `group_vars/all.yml` (WiFi password, adapter
address), `.card-password` (console password), and anything pulled off the
device into `logs/` or `backups/` are all gitignored. SSH keys never enter the
repo at all — `ssh_public_key_file` is a path, and the key is read at
card-write time and written to the SD card.

`backups/` is the part worth guarding most: `history.json` is a room
temperature log, which is fairly direct occupancy data.

`.gitignore` alone is thin protection, because the failure it does not cover
cannot be undone. A WiFi password that reaches git history survives deleting
the file, and survives making the repository private — it takes a history
rewrite and a new WiFi password. So `make hooks` installs a pre-commit hook
that catches the cases `.gitignore` misses:

- `git add -f group_vars/all.yml`, or a `.gitignore` that got edited
- the same config staged under a different name (`group_vars/pi-backup.yml`)
- any staged file containing a private key

The example file passes, because its placeholder values start with `your-`.
Bypass a false positive with `git commit --no-verify`.

Hooks are not copied by `git clone`, so `make hooks` needs running once in each
clone. That is why the hook lives in `.githooks/` — it is version-controlled,
rather than sitting in `.git/hooks/` where it would be lost.

---

## Troubleshooting

**The Pi never appears on the network.** Put the card back in your computer and check
whether `custom.toml` is still on the boot partition — the OS deletes it once
applied, so if it is still there, first boot did not get that far. Confirm the
SSID is 2.4 GHz; the Zero 2 W has no 5 GHz radio and will not see a 5 GHz-only
network. Then `make card` again and retry.

**`therminus.local` does not resolve but the Pi is online.** mDNS needs
multicast on a flat network; guest VLANs and some routers block it. Find the
address in your router's DHCP list and pass it as `PI_ADDRESS` — every target
accepts it:

```bash
make setup PI_ADDRESS=192.168.1.x
```

That changes only how your computer reaches the Pi. `pi_hostname` is the name set on
the device and must stay a hostname. To make it permanent, uncomment
`pi_address` in `group_vars/all.yml` and give the Pi a DHCP reservation.

If it resolves from the computer but your room temperature source cannot reach it,
use the IP there too — mDNS support varies a lot between clients.

**ebusd starts but `ebusctl info` fails.** Almost always the adapter address.
Check `ebusd_adapter_host`, confirm the adapter responds on port 9999, and read
`journalctl -u ebusd`. `make setup` prints a warning rather than failing here,
so the rest of the deployment still completes.

**The web UI loads but nothing happens.** Nothing is POSTing room temperature.
Confirm with `curl -X POST http://therminus.local:6790/roomtemp -d "current=21.0"`
— the state should change within a minute.

**Ansible asks for a sudo password.** The first-boot user gets passwordless
sudo. If yours does not, add `--ask-become-pass`:
`uv run ansible-playbook site.yml --ask-become-pass`.

---

## Notes on choices

**Bookworm rather than Trixie — currently required, not a preference.**
The Trixie images have moved first-boot configuration to **cloud-init**. Their
boot partition carries `user-data`, `meta-data` and `network-config`, and
`cmdline.txt` no longer runs `raspberrypi-sys-mods/firstboot` — the script that
reads `custom.toml`. On such an image `custom.toml` is not "unsupported", it is
simply never looked at: the file sits there and the Pi boots unconfigured with
no network.

`make card` now detects this and refuses, rather than reporting success and
leaving you with a Pi that never appears. Trixie also has its own batch of
Zero 2 W first-boot WiFi reports on top of this.

Supporting Trixie means emitting cloud-init `user-data` and `network-config`
instead. That is a contained change to `bootstrap/provision_card.py` — the
playbooks themselves are release-agnostic, and only `ebusd_distro` would need
flipping to `trixie` to pick the matching `.deb`.

**System Python rather than a uv-managed one.** Bookworm ships Python 3.11,
which satisfies Therminus. Downloading a standalone build would add time and a
moving part for no benefit. Override with `therminus_python` if that changes.

**ansible-core with no Galaxy collections.** Every task uses builtin modules, so
`uv sync` is the whole setup and there is no `ansible-galaxy install` step to
forget.

**Therminus configuration still lives in the app.** `SETPOINT`, `KP`, `BAND`,
latitude and longitude are constants at the top of `controller.py` and
`weather.py`, so they are not managed here. `EBUSD_HOST`/`EBUSD_PORT` already
point at `127.0.0.1:8888`, which is correct on the Pi. If you later want tuning
values set per-deployment, they would need to become environment variables in
the app first, and this repo would then set them in the unit file.
