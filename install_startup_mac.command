#!/bin/bash
# macOS: start Tidy Agent (background mode) automatically at login via launchd.
cd "$(dirname "$0")" || exit 1
APP_DIR="$(pwd)"
if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv && .venv/bin/python -m pip install --quiet -r requirements.txt
fi
mkdir -p data "$HOME/Library/LaunchAgents"
PLIST="$HOME/Library/LaunchAgents/com.tidyagent.plist"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.tidyagent</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP_DIR/.venv/bin/python</string>
    <string>$APP_DIR/app.py</string>
    <string>--background</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>StandardOutPath</key><string>$APP_DIR/data/agent.log</string>
  <key>StandardErrorPath</key><string>$APP_DIR/data/agent.log</string>
</dict>
</plist>
EOF
launchctl unload "$PLIST" 2>/dev/null
launchctl load "$PLIST"
echo "Tidy Agent will now start at login."
echo "To remove: launchctl unload \"$PLIST\" && rm \"$PLIST\""
read -r -p "Press Enter to close"
