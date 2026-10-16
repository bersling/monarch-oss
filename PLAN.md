# Monarch open-source edition: build plan (shared brief for all agents)

This directory is the open-source rewrite of a private project ("monarch", at
/Users/bersling/IT-Projects/monarch, read-only reference). The private project is an
AI-editable IFC model of one family's real house. The engine proved itself over 587
commits; a practising architect said he knows no software as good as this. We now
publish the ENGINE plus a fictional MODEL HOME, with every personal part removed.

## Vision (one paragraph)

Monarch = "your house as JSON, your agent as the architect". Plain JSON files in `data/`
describe walls, openings, slabs, stairs, roofs, furniture and products in metres. One
command builds a real IFC file, tessellates it once (incremental cache), exports glTF and
opens a hot-reloading browser viewer. `./q` answers geometric questions from the BUILT
geometry (what is at x,y; how much gap; does this box fit; clashes) so an AI agent never
has to guess. Declarative rules in `data/rules/*.json` guard walkways and clearances on
every build. Variants let a room hold several layouts. Products are reusable catalogue
definitions placed with `./place`. The result is a sourced, diffable, verifiable model that
any BIM tool can open, and that Claude Code (or any coding agent) can change precisely.

## Hard rules for everything in this repo

- NO personal data. Never mention: the private street/town/project number, the owner's
  name or employer, the architects, "Haus Nord"/"Haus Süd", contractors, IKEA order
  state, or any file under the private `plans/` tree. The fictional house is called
  **"Casa Monarch"**. The private repo is a reference for CODE, never for content.
- Language: English everywhere (code, docs, JSON comments, labels). Real UTF-8 is fine.
- Licence: MIT (LICENSE in root). Every third-party mesh/image must be CC0 or CC-BY with
  attribution recorded in `assets/README.md` AND in the object's IFC props
  (`MeshSource`, `MeshLicense`, `MeshURL`, `MeshAuthor`). Do not ship anything under
  3D Warehouse terms or unknown licences.
- No new Python dependencies beyond pyproject (ifcopenshell, pymupdf, shapely, numpy via
  ifcopenshell). Text-to-image for plaques uses PyMuPDF (already a dependency).
- Do not install anything on the host. Blender for mesh conversion is at
  `/Applications/Blender.app/Contents/MacOS/Blender`. Headless Chrome for screenshots
  is `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` (never open the
  user's browser; headless only).
- Python: `uv sync` then `.venv/bin/python`. Tests: `.venv/bin/python -m unittest discover -s tests`.
- Keep the proven architecture (docs/architecture.md). Simplify, do not re-invent.
- Write quality: terse, concrete, no marketing fluff inside code/docs (the README is the
  only place allowed to be enthusiastic, and it must still be true).

## Repository layout (target)

```
README.md               the killer readme (agent R)
LICENSE                 MIT
CLAUDE.md / AGENTS.md   instructions for the USER'S coding agent working on THEIR house
PLAN.md                 this file (delete before release)
data/
  house.json            project + storey index (contract below)
  ground_floor.json, upper_floor.json   per-storey plan JSON (same schema as private repo)
  products/*.json       reusable catalogue products (./place)
  placements.json       independent product instances
  rooms.json            room cards (./q room, ./room)
  variants.json + variants/<group>/<variant>.json
  rules/*.json          declarative checks
  sources.json          provenance rules (generic)
assets/                 OBJ meshes + README.md provenance table + plaque textures
src/                    engine only. No per-room scripts.
web/                    local hot-reload viewer (three.js), served by src/serve.py
site/                   HOSTED drop-an-IFC viewer (static, GitHub Pages), agent C
docs/                   architecture.md, viewer.md, data-format.md, gallery.md, images/
tests/
.claude/skills/         model-change, find-asset (generalised)
.github/workflows/      tests + pages deploy
```

## data/house.json contract (build_model.py must read this; nothing hard-coded)

```json
{
  "name": "Casa Monarch",
  "description": "Fictional two-storey family home that doubles as the Monarch tutorial.",
  "ifc_file": "generated/casa_monarch.ifc",
  "units": "meters",
  "origin_note": "Local frame: x east, y north, z up. Origin = south-west outer corner of the ground-floor envelope at finished floor level.",
  "storeys": [
    {"code": "GF", "name": "Ground floor", "elevation": 0.0, "file": "ground_floor.json"},
    {"code": "UF", "name": "Upper floor", "elevation": 2.9, "file": "upper_floor.json"}
  ],
  "site": "site.json"
}
```

`code` is the short storey key used by `./q --storey`, element keys (`@GF`), rules and
the viewer storey buttons. Storey order = list order. Everything in the engine that
today maps Untergeschoss/Erdgeschoss/... to UG/EG/OG/DG must use this list instead.
`site` is optional.

## The model home (agent B)

Casa Monarch, about 11 x 8.5 m, two storeys, pitched roof optional (gable along x) if
roof planes work generically; otherwise flat slab roof. Ground floor: entrance hall,
living/dining/kitchen (open), WC, stair, and THE GALLERY (a 5 x 4 m room). Upper floor:
two bedrooms, bathroom, landing. Real-ish dimensions (walls 0.3 exterior / 0.12
interior, doors 0.9 x 2.1, windows sill 0.9). Furniture in every room so it looks lived in.

### The Gallery = the tutorial ("the house is the tutorial")

A room with plinths. Each exhibit = one IFC object + one wall/plinth plaque. The plaque
is a thin textured box whose PNG texture (rendered with PyMuPDF from text in
`data/gallery.json`) shows a title, the provenance line, and the prompt a user can give
their agent. The same text goes into IFC properties (`Pset_Gallery`: `Title`,
`Provenance`, `License`, `HowTo`, `Prompt`), so every IFC viewer shows it on click and the
hosted viewer can draw labels. Exhibits (one per technique):

1. **Boxes in JSON** — a bookshelf authored directly as `parts[].boxes` in the storey JSON.
   Plaque: "Modelled by hand as JSON boxes. Prompt: 'add a 0.8 x 0.4 x 2.0 m bookshelf
   with five shelves on the north wall'".
2. **Catalogue product + ./place** — a product in `data/products/` placed via
   `data/placements.json`. Plaque names the CLI.
3. **Procedural mesh (Python)** — generated by `src/gen_assets.py` (e.g. a pendant lamp
   or a round table), plaque: "Generated with Python, src/gen_assets.py".
4. **Imported, Poly Haven (CC0)** — real downloaded mesh converted with
   `src/convert_asset.py` (agent D supplies it in assets/). Plaque: "Imported from
   Poly Haven, CC0. Prompt: 'find an asset for a ... and place it ...'".
5. **Imported, Sketchfab (CC-BY)** — same, with author credit on the plaque.
6. **Image on a surface** — a framed picture using `texture` on a box (image_textures).
   Use a CC0 image (agent D supplies).
7. **Variant** — the gallery's centre piece has two variants (e.g. "bench" / "sculpture")
   via data/variants, plaque explains `uv run python src/variant.py list|load`.
8. **Rule** — a walkway zone marked on the floor (thin coloured floor patch) guarded by
   `data/rules/gallery.json` (`clear`), plaque explains that the build fails if furniture
   enters it.
9. **Empty plinth: "Your turn"** — plaque: "Ask your agent: 'put a plant from Poly Haven
   on the empty plinth in the gallery'".

Every exhibit has full metadata props (Product, Source/URL, Dimensions, License,
Status) per the project convention.

## Hosted viewer (agent C) — `site/`

Static page deployable to GitHub Pages: drag & drop (or file picker) an `.ifc`, parsed in
the browser with web-ifc (wasm, vendored into `site/vendor/` from the npm tarball,
version pinned), rendered with three.js (reuse `web/vendor/three`). Same visual language
as `web/` (dark glass chrome, Outfit + DM Mono). Features: orbit + WASD walk, storey
visibility toggles, click to select with a property panel (all psets), element search,
floating labels for elements with `Pset_Gallery.Title`, section plane (optional), a
"load the example house" button that fetches `casa_monarch.ifc` next to the page. No
build step, no npm on the host: plain ES modules + import map. Must work from `file://`
fallback is NOT required; GitHub Pages is the target. Workflow `.github/workflows/pages.yml`
copies `site/` + the built example IFC into the artifact.

## Agents and file ownership (parallel work, disjoint files)

- **A: engine generalisation** owns `src/`, `tests/`, `web/`, root scripts, pyproject,
  docs/architecture.md + docs/viewer.md, `.claude/skills/*`, CLAUDE.md, AGENTS.md.
  Also writes a temporary stub house in `data/` to prove the pipeline (B will replace it).
- **C: hosted viewer** owns `site/`, `.github/workflows/`.
- **D: assets** owns `assets/` (meshes, textures, README.md provenance) and
  `docs/assets-provenance.md` if needed.
- **B: model home + gallery** (runs after A) owns `data/`, `docs/gallery.md`,
  `docs/data-format.md`, plus `src/gen_assets.py` additions and `src/gen_plaques.py`.
- **R: README + images** (runs last) owns README.md, docs/images/.

Do not edit files owned by another agent; leave a note in your final report instead.

## Definition of done (whole project)

`git clone && uv sync && ./build.sh` builds Casa Monarch with zero check failures;
`./open.sh` shows it; `./q at 5,4 --storey GF` answers; unit tests pass; `site/` loads
the example IFC and any dropped IFC; README screenshots are real renders of the model;
`grep -ri` for the private identifiers finds nothing.
