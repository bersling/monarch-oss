#!/usr/bin/env bash
# Browser viewer with hot reload: ./open.sh [--wait] [--no-browser] [--stop] [--blender ...]
# Details: src/open_viewer.py. The legacy Blender viewer is ./open.sh --blender.
set -euo pipefail
cd "$(dirname "$0")"
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python src/open_viewer.py "$@"
fi
exec uv run python src/open_viewer.py "$@"
