#!/usr/bin/env bash
# Build a standalone sc-telemetry binary for the current platform.
# Runtime needs NOTHING (stdlib only); this just bundles a Python interpreter.
set -euo pipefail
cd "$(dirname "$0")"
command -v pyinstaller >/dev/null 2>&1 || python3 -m pip install --user pyinstaller
pyinstaller --onefile --clean --name sc-telemetry \
  --hidden-import scpaths --hidden-import settings \
  --hidden-import gamelog  --hidden-import mango \
  --hidden-import linuxenv \
  sc_telemetry.py
echo "✓ built dist/sc-telemetry"
