# Instructions for coding agents

Monarch models a house as JSON (`data/`) and compiles it to IFC + a browser viewer. Agents
change the house by editing the JSON and verifying against the built geometry. Full
conventions: `CLAUDE.md`; architecture: `docs/architecture.md`; data schema:
`docs/data-format.md`.

## Five steps, every time

| Step | Command |
|---|---|
| 1. Measure before you model | `./q at X,Y --storey GF`, `./q show ID`, `./q near ID`, `./q storeys` |
| 2. Edit the JSON | `data/<storey>.json`, `./place add PRODUCT CAT_ID --storey GF --at x,y,z`, `src/variant.py load GROUP VARIANT` |
| 3. Build and check | `./build.sh` (zero failures) |
| 4. Verify numbers | `./q show ID`, `./q clash ID`, `./q fits ID --at X,Y`; add `data/rules/*.json` for lasting constraints |
| 5. Look, then report | `.venv/bin/python src/snap_web.py room=PREFIX plan=GF`, open the PNGs |

## Rules of the repository

- Metadata on every product: `Product`, `Supplier`, `SourceURL`/`Plan`, `Dimensions`,
  `License` (+ `MeshSource`, `MeshLicense`, `MeshURL`, `MeshAuthor` for meshes). Record
  licences; only CC0, CC-BY (with attribution) or MIT assets.
- Rules are data (`data/rules/`), checked on the actual IFC geometry on every build.
- Storeys, elevations and the IFC path come from `data/house.json`; nothing in `src/` may
  hard-code them.
- Never edit `generated/`; it is rebuilt. Never open the user's browser from a script;
  `./open.sh --no-browser --wait` and `src/snap_web.py` use headless Chrome.
- Use a git worktree when parallel sessions may touch the checkout.
- Dependencies: `uv sync`; run Python with `.venv/bin/python`. No new packages without
  updating `pyproject.toml`.
