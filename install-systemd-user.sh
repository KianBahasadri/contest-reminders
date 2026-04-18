#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$USER_SYSTEMD_DIR"

ln -sf "$SCRIPT_DIR/contest-reminders.service" "$USER_SYSTEMD_DIR/contest-reminders.service"
ln -sf "$SCRIPT_DIR/contest-reminders.timer" "$USER_SYSTEMD_DIR/contest-reminders.timer"

systemctl --user daemon-reload
systemctl --user enable --now contest-reminders.timer

printf 'Installed and enabled contest-reminders.timer\n'
printf 'Check status with: systemctl --user status contest-reminders.timer\n'
