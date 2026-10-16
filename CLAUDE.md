# Monarch: your house as JSON, your agent as the architect

This repository is an AI-editable house model. Plain JSON in `data/` (walls, openings,
slabs, stairs, roofs, furniture, products, in metres) builds a real IFC file, a glTF for the
browser viewer and a geometry cache that answers questions. You, the coding agent, change
the house by editing JSON, never by hand-editing the IFC.

## The loop for every change

1. **Find the measurement.** Read the plan, datasheet or photo the user points at; ask the
   built model for the rest: `./q at X,Y --storey GF` (what is around a point), `./q show ID`,
   `./q near ID`, `./q free x0,y0,z0,x1,y1,z1`. Never guess a dimension you can look up.
2. **Edit the JSON** in `data/` (storey files named in `data/house.json`, products in
   `data/products/`, placements with `./place`, variants with `src/variant.py`).
   Sketch first for room layouts: `./room sketch <room>` or `src/plan2d.py`.
3. **`./build.sh`**: IFC, geometry cache, glTF and every check in about 2 s. Zero failures
   is the bar; warnings are design conflicts to read, not noise.
4. **Verify the numbers** against the built geometry: `./q show ID`, `./q clash ID`,
   `./q fits ID --at X,Y`. Constraints that must hold forever go into `data/rules/*.json`.
5. **Look at it**: `.venv/bin/python src/snap_web.py room=PREFIX plan=GF` renders PNGs with
   the same renderer the user sees; open them with the Read tool. Then report what changed,
   what you measured and what is approximate.

## Conventions

- Metadata on every product: `props` with `Product`, `Supplier`, `SourceURL` (or `Plan`),
  `Dimensions` ("80 x 40 x 200 cm", checked against the geometry), `License`; meshes add
  `MeshSource`, `MeshLicense`, `MeshURL`, `MeshAuthor`. Only CC0/CC-BY/MIT assets.
- Rules are data: `data/rules/<room>.json` (`clear`, `bbox`, `gap`, `no_penetration`,
  `inside`, `ray`), evaluated on the actual IFC geometry on every build. Prefer a rule
  over a new check script.
- One physical thing is one IFC element with styled `parts`, never several half-objects.
- Element ids are stable keys (`GAL_SHELF`); `./q` and the viewer address them by id.
- Setup: `uv sync`, then `.venv/bin/python`. Tests: `.venv/bin/python -m unittest discover -s tests`.
- Work in a git worktree when other sessions may be editing this checkout; the viewer
  (`./open.sh`, hot reload) runs one server per checkout.
- Skills: `.claude/skills/model-change` (change the house), `.claude/skills/find-asset`
  (bring in a licensed mesh). Read more in `docs/architecture.md`, `docs/viewer.md`,
  `docs/data-format.md`.
