# Contributing to Monarch

Thanks for helping. This file is short on purpose; the conventions the agent follows are in
`CLAUDE.md` and `AGENTS.md`, the schema in `docs/data-format.md`.

## Setup

```sh
uv sync
./build.sh                                        # must end with "4/4 checks passed"
.venv/bin/python -m unittest discover -s tests    # 62 tests
```

Optional tools: Blender for `src/convert_asset.py`, Google Chrome for `src/snap_web.py` and
`node tests/browser_viewer.mjs`. Their paths are constants at the top of those files.

## Work in a worktree

The viewer (`./open.sh`) watches the checkout and rebuilds on every save, and other sessions
(human or agent) may have the same checkout open. Do your work in a git worktree
(`git worktree add ../monarch-<topic> -b <topic>`); each worktree gets its own viewer port
and its own build lease, so parallel work never collides.

## What a pull request needs

- `./build.sh` passes with zero failures. Warnings are design conflicts to read, not noise;
  resolve them or explain them in the PR.
- Unit tests pass. New engine behaviour comes with a test in `tests/`.
- If you changed the house, include a render: `.venv/bin/python src/snap_web.py room=PREFIX`
  and attach the PNG. Reviewers look at pictures, not coordinates.
- Nothing under `generated/` is committed.

## Rules as data, not scripts

A constraint that must keep holding (a clear walkway, a minimum gap, an element that must
stay inside a zone) goes into `data/rules/<room>.json` as a `clear`, `bbox`, `inside`,
`gap`, `ray` or `no_penetration` rule with a `why`. Rules run on the built IFC geometry on
every build. Add a Python check only for a new *kind* of invariant that the rule engine
cannot express, and put it in `src/check_model.py` or `src/rules.py` rather than a new
script.

## Metadata convention

Every product-like element carries `props` with `Product`, `Supplier`, `SourceURL` (or
`Plan`), `Dimensions` (`W x D x H cm`, checked against the geometry within 2 cm), `License`
and `Status`. Meshes add `MeshSource`, `MeshLicense`, `MeshURL`, `MeshAuthor`, and
`Attribution` when the licence requires it. Casa Monarch is fictional, so suppliers are
`"fictional"` or `"none (authored)"` and URLs point to `example.com` or into this
repository; never add a real company or person to the demo data.

## Assets and licences

Only CC0, CC-BY (with attribution) or MIT material may enter `assets/`. For each file:

1. Record source, author, licence and size in the provenance table of `assets/README.md`.
2. Ship a sidecar `<name>.json` with `size_m`, `origin`, `rgb` and the `props` above.
3. Copy the `props` into every element that uses the mesh.

Nothing from 3D Warehouse (its terms forbid redistribution) and nothing of unknown origin.
Keep meshes small: decimate on conversion, round coordinates to 0.1 mm, drop unused
vertices; the whole `assets/` folder is about 2 MB today.

## Style

Code and docs are English, terse and concrete. Say why in `_comment` fields, not what.
One physical thing is one IFC element with styled `parts`. Element ids are stable upper-case
keys prefixed by room (`GAL_`, `KIT_`). Storeys, elevations and the IFC path come from
`data/house.json`; nothing in `src/` may hard-code them.

## Licence

By contributing you agree that your contribution is licensed under the MIT licence in
`LICENSE`.
