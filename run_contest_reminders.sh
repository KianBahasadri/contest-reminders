#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

python3 "$SCRIPT_DIR/ctf.py"
python3 "$SCRIPT_DIR/codeforces.py"
python3 "$SCRIPT_DIR/dmoj.py"
