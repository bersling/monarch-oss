#!/usr/bin/env bash
# All model checks in one process (src/run_checks.py lists them): invariants over data/,
# declarative rules in data/rules/, and the per-room checks on the actual IFC geometry.
#   ./check.sh            everything          ./check.sh gym kids   only matching checks
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x .venv/bin/python ]]; then
  MONARCH_PYTHON=(.venv/bin/python)
else
  MONARCH_PYTHON=(uv run python)
fi

"${MONARCH_PYTHON[@]}" src/pipeline.py check "$@"
