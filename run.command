#!/bin/bash
# macOS: double-click to start Tidy Agent with its dashboard window.
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "First run: setting up..."
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found. Install it from python.org or with: brew install python"
    read -r -p "Press Enter to close"; exit 1
  fi
  python3 -m venv .venv || { read -r -p "Could not create venv. Press Enter to close"; exit 1; }
  .venv/bin/python -m pip install --quiet --upgrade pip
fi
# (Re)install extras when requirements.txt is new or changed (e.g. after an update)
if ! cmp -s requirements.txt .venv/requirements.installed; then
  .venv/bin/python -m pip install --quiet -r requirements.txt && cp requirements.txt .venv/requirements.installed
fi
exec .venv/bin/python app.py
