#!/usr/bin/env bash
# Build the model from the plan JSONs in data/, then verify it.
#   generated/<house>.ifc                the IFC named in data/house.json (source of truth for exports)
#   generated/geometry.npz + elements.json  tessellated once, incremental (src/geometry_cache.py)
#   generated/model.glb                  browser viewer model (src/export_glb.py)
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x .venv/bin/python ]]; then
  MONARCH_PYTHON=(.venv/bin/python)
else
  MONARCH_PYTHON=(uv run python)
fi

"${MONARCH_PYTHON[@]}" src/pipeline.py build
