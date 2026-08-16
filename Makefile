.PHONY: help hooks config card card-check setup base ebusd ebus-config deploy health check ssh status logs backup restart reboot poweroff lint

SHELL := /usr/bin/env bash
# PI_ADDRESS=192.168.1.x on the command line overrides how the Pi is reached,
# for when mDNS does not work. It does not change the device's hostname.
ANSIBLE := uv run ansible-playbook $(if $(PI_ADDRESS),-e pi_address=$(PI_ADDRESS))
HOST = $(if $(PI_ADDRESS),$(shell uv run python scripts/config.py | sed -n "s/^PI_USER=//p" | tr -d "'")@$(PI_ADDRESS),$(shell uv run python scripts/config.py --host))

help:
	@echo "Setting up a new device"
	@echo "  make hooks      install the pre-commit secret guard (once per clone)"
	@echo "  make config     create group_vars/all.yml from the example"
	@echo "  make card-check show the first-boot config without writing it"
	@echo "  make card       write first-boot config to a mounted SD card"
	@echo "  make setup      provision everything on the Pi"
	@echo
	@echo "Day to day"
	@echo "  make deploy     push the app and restart it"
	@echo "  make status     service status and current controller state"
	@echo "  make logs       pull logs into logs/  (SINCE='2 days ago')"
	@echo "  make backup     pull history.json and effective config into backups/"
	@echo "  make ssh        open a shell on the device"
	@echo
	@echo "Parts of the setup"
	@echo "  make base       OS configuration only"
	@echo "  make ebusd      ebusd install and configuration only"
	@echo "  make ebus-config  update the eBUS message definitions checkout"
	@echo "  make health     health check only"
	@echo "  make check      dry run of the full playbook"
	@echo
	@echo "Recovery"
	@echo "  make restart    restart therminus and ebusd"
	@echo "  make reboot     reboot the device"
	@echo "  make poweroff   shut down cleanly before pulling the power"

# Hooks live in .githooks so they are version-controlled. git does not use them
# until it is pointed there, which is per-clone.
hooks:
	@chmod +x .githooks/*
	@git config core.hooksPath .githooks
	@echo "Hook installed. Commits touching group_vars/all.yml will be refused."

config:
	@if [ -f group_vars/all.yml ]; then \
		echo "group_vars/all.yml already exists — not overwriting."; \
	else \
		cp group_vars/all.example.yml group_vars/all.yml; \
		echo "Created group_vars/all.yml. Edit it, then run: make card"; \
	fi

card:
	uv run python bootstrap/provision_card.py

# Print the custom.toml that `make card` would write, without needing a card.
card-check:
	uv run python bootstrap/provision_card.py --dry-run

setup:
	$(ANSIBLE) site.yml

base:
	$(ANSIBLE) site.yml --tags base

ebusd:
	$(ANSIBLE) site.yml --tags ebusd

# Deliberate, because pulling new message definitions can change how the pump
# is addressed. A plain `make setup` leaves the checkout where it is.
ebus-config:
	$(ANSIBLE) site.yml --tags ebusd -e ebus_config_update=true

deploy:
	$(ANSIBLE) site.yml --tags deploy

health:
	$(ANSIBLE) site.yml --tags health

check:
	$(ANSIBLE) site.yml --check --diff

lint:
	uv run ansible-playbook site.yml --syntax-check

ssh:
	ssh $(HOST)

status:
	@ssh $(HOST) "systemctl --no-pager --lines=0 status therminus ebusd; \
		echo; systemctl list-timers therminus-health --no-pager; \
		echo; sudo /usr/local/bin/therminus-health"

logs:
	SINCE="$(SINCE)" PI_ADDRESS="$(PI_ADDRESS)" ./scripts/fetch-logs.sh

backup:
	PI_ADDRESS="$(PI_ADDRESS)" ./scripts/backup.sh

restart:
	ssh $(HOST) "sudo systemctl restart ebusd therminus"

reboot:
	ssh $(HOST) "sudo systemctl reboot" || true
	@echo "Rebooting. Back in about a minute."

# Flushes pending writes before you pull the power. Cutting power to a running
# Pi risks corrupting the SD card; the app itself tolerates it, the card is the
# fragile part.
poweroff:
	ssh $(HOST) "sudo systemctl poweroff" || true
	@echo "Shutting down. Wait for the green LED to stop, then pull the power."
