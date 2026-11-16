#!/bin/bash
# macOS: start Tidy Agent without a window. Dashboard: http://127.0.0.1:8765
cd "$(dirname "$0")" || exit 1
[ -x ".venv/bin/python" ] || python3 -m venv .venv
if ! cmp -s requirements.txt .venv/requirements.installed; then
  .venv/bin/python -m pip install --quiet -r requirements.txt && cp requirements.txt .venv/requirements.installed
fi
mkdir -p data
nohup .venv/bin/python app.py --background >> data/agent.log 2>&1 &
echo "Tidy Agent is running in the background: http://127.0.0.1:8765"
sleep 1 && open "http://127.0.0.1:8765"
