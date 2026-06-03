#!/usr/bin/env bash
# Build a standalone sc-telemetry binary for the current platform.
# Runtime needs NOTHING (stdlib only); this just bundles a Python interpreter.
set -euo pipefail
cd "$(dirname "$0")"

# Find pyinstaller; if absent, install it into a local .venv (works on distros
# with PEP-668 "externally managed" Python, e.g. Arch, Debian 12+).
if command -v pyinstaller >/dev/null 2>&1; then
  PYI=pyinstaller
else
  echo "pyinstaller not found — setting up .venv"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip pyinstaller
  PYI=./.venv/bin/pyinstaller
fi

"$PYI" --onefile --clean --name sc-telemetry \
  --add-data "chart.min.js:." \
  --hidden-import scpaths --hidden-import settings \
  --hidden-import gamelog  --hidden-import mango \
  --hidden-import linuxenv \
  sc_telemetry.py
echo "✓ built dist/sc-telemetry"
