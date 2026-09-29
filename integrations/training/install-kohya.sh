#!/usr/bin/env bash
set -euo pipefail

REPO="${HYPERSPACE_REPO:-/Users/opo/hyperspace-agi-1.04}"
KOHYA_DIR="${KOHYA_DIR:-$REPO/data/training/tools/sd-scripts}"
KOHYA_REF="${KOHYA_REF:-v0.12.0}"

if [[ ! -d "$KOHYA_DIR/.git" ]]; then
  mkdir -p "$(dirname "$KOHYA_DIR")"
  git clone --branch "$KOHYA_REF" --depth 1 https://github.com/kohya-ss/sd-scripts.git "$KOHYA_DIR"
fi
[[ -x "$KOHYA_DIR/.venv/bin/python" ]] || python3 -m venv "$KOHYA_DIR/.venv"
"$KOHYA_DIR/.venv/bin/python" -m pip install --upgrade pip
(cd "$KOHYA_DIR" && .venv/bin/python -m pip install -r requirements.txt)
# Il requirements v0.12.0 non porta torchvision su macOS ARM, ma le utility
# dataset lo importano sempre. Pinnato a 0.29.0 per combaciare con torch 2.14.0
# (coppia torch 2.14 <-> torchvision 0.29): un torchvision non vincolato puo'
# tirare su un torch diverso e rompere la build MPS.
if [[ "$(uname -s)" == "Darwin" ]]; then
  "$KOHYA_DIR/.venv/bin/python" -m pip install "torchvision==0.29.0"
fi
echo "kohya installato in $KOHYA_DIR"
