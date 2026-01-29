#!/bin/bash
# Heatmap Dashboard Launcher - Server Side (runs on Pluto directly)

# Resolve absolute path to current directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Just run the dashboard python script directly
# Check for venv
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

# Use AppleScript to open a Terminal window if running from LaunchAgent (headless launch need UI)
osascript <<EOF
tell application "Terminal"
    activate
    do script "cd \"$DIR\" && echo '🔥 Starting Local Dashboard...' && source .venv/bin/activate && python3 hm/support/dashboard.py"
    tell front window to set bounded to {0, 0, 1500, 1000}
    tell front window to set custom title to "Heatmap Dashboard (Local)"
end tell
EOF
