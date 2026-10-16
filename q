#!/usr/bin/env bash
# Query the built model: ./q help. Preserve errors and exit codes.
set -euo pipefail
cd "$(dirname "$0")"
if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python src/q.py "$@"
fi
exec uv run python src/q.py "$@"
