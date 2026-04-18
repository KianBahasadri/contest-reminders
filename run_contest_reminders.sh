#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
LOG_FILE="$SCRIPT_DIR/contest-reminders.log"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log_message() {
  printf '[%s] %s\n' "$(timestamp)" "$1" | tee -a "$LOG_FILE"
}

log_script_output() {
  local script_name="$1"
  local output="$2"

  if [[ -z "$output" ]]; then
    log_message "[$script_name] no changes reported"
    return
  fi

  while IFS= read -r line; do
    log_message "[$script_name] $line"
  done <<< "$output"
}

run_script() {
  local script_name="$1"
  local script_path="$2"
  local output
  local status

  set +e
  output="$(python3 "$script_path" 2>&1)"
  status=$?
  set -e

  log_script_output "$script_name" "$output"

  if (( status != 0 )); then
    log_message "Run failed while executing $script_name"
    exit "$status"
  fi
}

log_message "Run started"
run_script "ctf" "$SCRIPT_DIR/ctf.py"
run_script "codeforces" "$SCRIPT_DIR/codeforces.py"
run_script "dmoj" "$SCRIPT_DIR/dmoj.py"
log_message "Run finished successfully"
