#!/bin/bash
# Heatmap Dashboard Launcher - Remote on Pluto

# Resolve absolute path to current directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# 1. Create/Update the helper script that runs on Pluto
# This file is written to the mounted volume, so it exists on Pluto too.
cat > _remote_launch_helper.sh <<'REMOTE_SCRIPT'
#!/bin/bash
echo "🔍 Searching for bot..."
# Find bot.py to locate the root directory
TARGET=$(find ~ -maxdepth 5 -name bot.py -print -quit 2>/dev/null)

if [ -z "$TARGET" ]; then
    echo "❌ Could not find bot.py in ~ (maxdepth 5)"
    # Fallback to standard location
    if [ -d "$HOME/heatmap-of-fascism-bot" ]; then
        TARGET="$HOME/heatmap-of-fascism-bot/bot.py"
    else
        read -p "Press Enter to exit..."
        exit 1
    fi
fi

ROOT_DIR=$(dirname "$TARGET")
echo "✅ Found bot at: $ROOT_DIR"
cd "$ROOT_DIR"

# Activate venv if present
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

# Run dashboard
python3 hm/support/dashboard.py
REMOTE_SCRIPT

chmod +x _remote_launch_helper.sh

# 2. Launch Terminal with SSH command
# We use a simple find command to locate the helper script we just created.
# This avoids needing to know the absolute path on the remote.

# Explanation for the user (in the AppleScript command)
CMD="echo '*****************************************************'; echo '* Connecting to Pluto Dashboard...                  *'; echo '*                                                   *'; echo '* Please enter the password for user: oscar_berngruber *'; echo '* (Login password for the Pluto server)             *'; echo '*****************************************************'; ssh -t oscar_berngruber@pluto 'find ~ -name _remote_launch_helper.sh -print -quit | xargs bash'"

osascript <<EOF
tell application "Terminal"
    activate
    do script "$CMD"
    tell front window to set bounded to {0, 0, 1500, 1000}
    tell front window to set custom title to "Heatmap Dashboard (Pluto)"
end tell
EOF
