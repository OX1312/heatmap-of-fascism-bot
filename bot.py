#!/usr/bin/env python3
# Heatmap of Fascism — Entry Point
#
# This file is now a thin wrapper around the hm/ modular architecture.
# See docs/ARCHITECTURE.md for details.

import sys
import os
from pathlib import Path

# Ensure we can import from local directory
import sys
print(f"DEBUG: sys.executable={sys.executable}")
print(f"DEBUG: sys.version={sys.version}")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from hm.core.main_loop import run_loop
from hm.utils.files import load_json

def main():
    # Load config (minimized logic here)
    cfg_path = ROOT / "config.json"
    cfg = load_json(cfg_path, {})
    
    # Load secrets
    secrets_path = ROOT / "secrets" / "secrets.json"
    secrets = load_json(secrets_path, {})
    cfg.update(secrets)
    
    # Delegate to core
    run_loop(cfg)

if __name__ == "__main__":
    main()
