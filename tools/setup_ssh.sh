#!/bin/bash
# Setup Passwordless SSH Access to Pluto

echo "🔐 Setting up full access for Heatmap Bot..."

# 1. Check for existing SSH Keys (Standard locations)
KEY_FILE=""
if [ -f "$HOME/.ssh/id_ed25519" ]; then
    KEY_FILE="$HOME/.ssh/id_ed25519"
elif [ -f "$HOME/.ssh/id_rsa" ]; then
    KEY_FILE="$HOME/.ssh/id_rsa"
fi

# 2. Generate new key if none exists
if [ -z "$KEY_FILE" ]; then
    echo "🔑 No SSH key found. Generating new default key (Ed25519)..."
    ssh-keygen -t ed25519 -f "$HOME/.ssh/id_ed25519" -N ""
    KEY_FILE="$HOME/.ssh/id_ed25519"
else
    echo "✅ Found existing SSH key: $KEY_FILE"
fi

# 3. Add key to valid auth methods
# Ensure ssh-agent is running
eval "$(ssh-agent -s)" >/dev/null
ssh-add "$KEY_FILE" 2>/dev/null

# 4. Copy Key to Pluto
echo ""
echo "🚀 Transferring key to Pluto..."
echo "⚠️  BITTE PASSWORT EINGEBEN (für 'pluto'), wenn gefragt:"
echo ""

ssh-copy-id -i "$KEY_FILE" oscar_berngruber@pluto

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ SUCCESS! Key installed."
    echo "Testing connection..."
    ssh -o BatchMode=yes -o ConnectTimeout=5 oscar_berngruber@pluto echo "🎉 PASSWORDLESS ACCESS WORKING!"
else
    echo ""
    echo "❌ FAILED. Please try again or check password."
    exit 1
fi
