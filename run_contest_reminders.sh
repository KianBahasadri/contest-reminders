#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
LOG_FILE="$SCRIPT_DIR/contest-reminders.log"
FAILED_SCRIPTS=()
DEFAULT_ENABLED_SCRIPTS="ctf,codeforces"

declare -A SCRIPT_PATHS=(
  [ctf]="$SCRIPT_DIR/ctf.py"
  [codeforces]="$SCRIPT_DIR/codeforces.py"
  [clist_dmoj]="$SCRIPT_DIR/clist_dmoj.py"
  [dmoj]="$SCRIPT_DIR/dmoj.py"
)

SCRIPT_ORDER=(ctf codeforces clist_dmoj dmoj)

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

script_is_enabled() {
  local script_name="$1"
  local enabled_scripts="${CONTEST_REMINDER_SCRIPTS:-$DEFAULT_ENABLED_SCRIPTS}"
  local normalized=" ${enabled_scripts//,/ } "

  [[ "$normalized" == *" all "* || "$normalized" == *" $script_name "* ]]
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
    log_message "Run warning: $script_name failed with exit status $status"
    FAILED_SCRIPTS+=("$script_name")
  fi
}

log_summary() {
  if (( ${#FAILED_SCRIPTS[@]} == 0 )); then
    log_message "Run finished successfully"
    return
  fi

  log_message "Run finished with warnings; failed sources: ${FAILED_SCRIPTS[*]}"
}

load_env

log_message "Run started"
for script_name in "${SCRIPT_ORDER[@]}"; do
  if script_is_enabled "$script_name"; then
    run_script "$script_name" "${SCRIPT_PATHS[$script_name]}"
  else
    log_message "[$script_name] skipped"
  fi
done
log_summary
