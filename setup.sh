#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if command -v python3 >/dev/null 2>&1; then
    exec python3 tools/setup_deploy.py "$@"
elif command -v uv >/dev/null 2>&1; then
    exec uv run --frozen python tools/setup_deploy.py "$@"
else
    echo 'Install Python 3.9+ or uv, then run bash setup.sh again.' >&2
    exit 1
fi
