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
