# therminal

Provisioning and deployment for [Therminus](../therminus) on a Raspberry Pi
Zero 2 W.

The Pi runs three things: **ebusd**, talking to an eBUS Adapter Shield v5 over
the network; **Therminus**, the controller; and **ebusmon**, a read-only
dashboard. Both apps talk to ebusd on `127.0.0.1:8888`. An nginx in front of
them serves each on port 80 under its own `.local` name, so neither needs a
port number in the browser.

Everything is driven from your computer. Nothing in this repo is installed on the Pi
except the files it writes there, and nothing deployment-related lives in the
Therminus repo.

```
 Computer ──ansible/rsync──▶ Pi Zero 2 W
                             │
                             ├─ nginx:80 ─┬─ therminus.local ─▶ therminus:6790
                             │            └─ ebusmon.local ───▶ ebusmon:6789
                             │
                             └─ ebusd:8888 ──WiFi──▶ Adapter Shield v5 ──▶ heat pump
```

| Address | What |
| --- | --- |
| `http://therminus.local/` | controller web UI and `/roomtemp` |
| `http://ebusmon.local/` | dashboard |

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
installs and configures ebusd, deploys Therminus and ebusmon, and sets up the
watchdog and health check. At the end it prints both web addresses.

Both apps need a checkout on this machine. `therminus_src` and `ebusmon_src` in
`group_vars/all.yml` say where they are; the playbook stops with a clear message
if either path has no `app.py`.

### 5. Repoint your room temperature source

Therminus needs a room temperature feed to function, so configure a POST request:

```
http://therminus.local/roomtemp
```

Until a room temp is first posted, the heat pump is left
alone, which is the safe failure mode, but nothing will happen.

---

## Day to day

```bash
make deploy       # push both apps from their local checkouts and restart
make therminus    # deploy the controller only
make ebusmon      # deploy the dashboard only
make status       # service status and current controller state
make logs         # pull logs into logs/<timestamp>/
make backup       # pull history.json, the dashboard's data and config
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

### ebusmon (`roles/ebusmon`)

```
/opt/ebusmon/app          the dashboard, rsynced
/opt/ebusmon/config.yaml  generated at deploy time
/opt/ebusmon/venv         virtualenv, built with uv against Python 3.14
/var/lib/ebusmon/data     the daily .jsonl files
```

The same shape as the Therminus role: its own system user, its own venv, the
same sandboxed unit. Three differences:

- **Python 3.14.** ebusmon's `pyproject.toml` sets `requires-python = ">=3.14"`,
  so it cannot share the controller's 3.12 venv. uv downloads both interpreters
  into `/opt/uv-python`. Dependencies are read straight from `pyproject.toml`;
  there is no `requirements.txt`.
- **The config file is generated.** Charts and indicators are read from
  `config.yaml` in your ebusmon checkout, and only the `ebusd` and `server`
  sections are replaced — on the Pi, ebusd is on `127.0.0.1`. So charts are
  edited in the ebusmon repo, not duplicated here. Key order is preserved,
  because where two fields share a chart the first one is the primary series.
- **It is allowed to lose.** `Nice=10` and `MemoryMax=200M`, so on a 512 MB
  board the dashboard is the process that suffers, not the controller. Nothing
  about it escalates to a reboot; a dead dashboard is a dead dashboard.

The dashboard also exposes a `/roomtemp` endpoint of its own. It is separate
from the controller's — posting to one does not feed the other. If you want the
reading in both, post it to both.

### Web addresses (`roles/proxy`)

nginx listens on port 80 with one vhost per name, each proxying to an app on
localhost:

```
therminus.local  →  127.0.0.1:6790   (also the default vhost)
ebusmon.local    →  127.0.0.1:6789
```

The apps keep listening on their own ports and those keep working, so old
bookmarks and the health check are unaffected. `therminus.local` is the default
vhost, so reaching the Pi by IP address lands on the controller rather than the
dashboard.

Access logging is off — every request would otherwise be a write to the SD
card. Errors still go to `/var/log/nginx/`. `worker_processes` is 1; four
workers on a 512 MB board is memory spent on concurrency this never sees.
Responses are unbuffered with a 300s read timeout, so a streaming endpoint
reaches the browser as it is produced.

**The second name.** avahi answers for the device's own hostname only, so
`ebusmon.local` is published by `mdns-alias.service`, a small script that adds
a CNAME record pointing at `therminus.local` over avahi's D-Bus API. A CNAME
rather than an address record, because avahi then resolves the target itself at
query time and a new DHCP lease cannot leave a stale address behind. The record
lives as long as the process holds its D-Bus connection, and the unit is
`PartOf=avahi-daemon.service` so an avahi restart republishes it.

Add another name by adding an entry to `proxy_sites` in `roles/proxy/vars/`;
anything ending in `.local` that is not the device's own hostname is published
automatically.

CNAMEs over mDNS are resolved by macOS and Linux. Support elsewhere is uneven —
if a client cannot resolve `ebusmon.local`, use `therminus.local:6789` from it.

### Health and recovery (`roles/health`)

The systemd hardware watchdog is armed at 14 seconds, so a kernel that stops
scheduling resets the board.

Above that, a timer runs `/usr/local/bin/therminus-health` every five minutes.
ebusmon is deliberately outside it: restarting the heat pump controller, or
rebooting the board, because a monitoring dashboard fell over would be the wrong
trade. systemd restarts ebusmon on its own with `Restart=always`.

**The connectivity probes are a dependency chain**, evaluated in order:

```
wlan0 exists → gateway reachable → adapter reachable → ebusd has bus signal
```

The first failure is the diagnosis and the rest are skipped, because a
downstream failure is explained by the upstream one — without this, one lost
network connection escalates as several independent faults at once.

The controller check is separate, because it is served from `127.0.0.1` and
stays reachable while the network is down. It opens `/api/stream` and reads the
snapshot the app sends on connect, which gives liveness and the controller state
from one request without needing a health endpoint added to Therminus.

Each diagnosis gets the remedies that can actually repair it, and two get none:

| Diagnosis | Remedies |
|---|---|
| `wifi-local` | driver reload → full chip reset → reboot |
| `network-upstream` | none — logged only |
| `adapter` | none — logged only |
| `bus` | restart ebusd → reboot |
| `app` | restart therminus → reboot |

`network-upstream` and `adapter` are deliberately inert. Rebooting this board
does not bring back a router or a heat pump adapter, and it would restart the
controller into a fresh rest period for nothing.

**The bus check reads `signal:` from `ebusctl info`, not its exit status.**
`ebusctl` exits zero whenever the local daemon answers, which says nothing about
whether that daemon can reach the adapter — the check could not fail while
ebusd was running.

**WiFi recovery runs as its own transient unit** (`therminus-recover`), never
inline, because the sequence must not be terminated between unbinding the SDIO
host and binding it again. Two steps:

- `reload` — unload and reload `brcmfmac`. The chip has no flash, so its
  firmware is uploaded into its RAM at every driver load; this re-attempts
  exactly the operation that fails when the board comes up with no WiFi. ~3s.
- `full-reset` — unload the driver, unbind the SDIO host from `mmc-bcm2835`,
  drop `WL_ON` (GPIO 41), restore it, rebind, reload. ~12s. A real power cycle
  of the chip plus re-enumeration of the card, and the strongest remedy short of
  disconnecting the board. Both halves are required: the slot is declared
  `non-removable`, so cutting power without unbinding leaves a stale device and
  the next probe fails with `-110`. The script refuses to unbind a controller
  that carries a mounted filesystem.

`therminus-wifi-boot.service` runs the same check once at start-up. The timer
does not fire until `OnBootSec=5min`, and a chip that fails to initialise does
so during boot.

**Escalation** is three consecutive failures to restart a service, twelve (about
an hour) to reboot, rate-limited to one reboot per hour. `health_max_reboots`
caps the total for one unbroken run of failures: without it, a fault a reboot
cannot fix reboots the device every hour indefinitely, and since the controller
starts each time in `RESTING`, the heat pump would never run. The counter resets
on the first fully clean check. The board has no RTC, so a clock that stepped
backwards is treated as an expired cooldown rather than a future timestamp, and
the reboot cap is the real bound.


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

**`ebusmon.local` does not resolve but `therminus.local` does.** Check
`systemctl status mdns-alias` on the device. If the service is running, the
client is probably one that does not follow mDNS CNAMEs; use
`http://therminus.local:6789/` there instead.

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
Confirm with `curl -X POST http://therminus.local/roomtemp -d "current=21.0"`
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
