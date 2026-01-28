#!/bin/bash

# Get the directory where this script is located
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Change into that directory
cd "$REPO"

# Set window title
echo -ne "\033]0;🔥 Heatmap Bot Monitor\007"

# Run the ox monitor command
echo "========================================================"
echo "   🔥 HEATMAP BOT MONITORING ENABLED"
echo "   (This window tracks logs live)"
echo "========================================================"
echo ""

./ox monitor
