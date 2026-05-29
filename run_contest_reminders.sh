#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
LOG_FILE="$SCRIPT_DIR/contest-reminders.log"
SYNC_FAILED=0

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log_message() {
  printf '[%s] %s\n' "$(timestamp)" "$1" | tee -a "$LOG_FILE"
}

log_output() {
  local output="$1"

  if [[ -z "$output" ]]; then
    log_message "No changes reported"
    return
  fi

  while IFS= read -r line; do
    log_message "$line"
  done <<< "$output"
}

load_env() {
  local env_file="$SCRIPT_DIR/.env"
  local line
  local key
  local value

  if [[ ! -f "$env_file" ]]; then
    return
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"

    if [[ -z "$line" || "${line:0:1}" == "#" || "$line" != *"="* ]]; then
      continue
    fi

    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"

    if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ && -z "${!key+x}" ]]; then
      export "$key=$value"
    fi
  done < "$env_file"
}

require_enabled_scripts() {
  if [[ -n "${CONTEST_REMINDER_SCRIPTS:-}" ]]; then
    return
  fi

  log_message "Run failed: CONTEST_REMINDER_SCRIPTS must be set in .env"
  exit 1
}

dry_run_is_enabled() {
  local value="${CONTEST_REMINDER_DRY_RUN:-}"
  value="${value,,}"

  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" || "$value" == "on" ]]
}

run_sync() {
  local output
  local status

  set +e
  output="$(python3 "$SCRIPT_DIR/main.py" 2>&1)"
  status=$?
  set -e

  log_output "$output"

  if (( status != 0 )); then
    log_message "Run warning: sync failed with exit status $status"
    SYNC_FAILED=1
  fi
}

log_summary() {
  if (( SYNC_FAILED != 0 )); then
    log_message "Run finished with warnings"
    return
  fi

  log_message "Run finished successfully"
}

load_env

log_message "Run started"
require_enabled_scripts
if dry_run_is_enabled; then
  log_message "Dry run enabled: Linear creates and updates will be previewed only"
fi
run_sync
log_summary
