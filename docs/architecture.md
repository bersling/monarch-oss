# How the model is built, checked, queried and shown

Goals: AI edits must be fast and precise, the viewer must never crash, hot reload must
just work, and every answer must come from the built geometry rather than from a guess.

```
data/house.json                      project name, IFC path, storey list (code, name, elevation, file), site
data/*.json, assets/*.obj            source of truth: metres, local frame, sourced props
  │  src/build_model.py              JSON -> IFC. Only engine code lives in src/; rooms are data
  ▼                                  (JSON + rules), not scripts
generated/<house>.ifc                the deliverable for architects and BIM tools
  │  src/geometry_cache.py           tessellated ONCE per build; products whose geometry key is
  ▼                                  unchanged are reused
generated/geometry.npz + elements.json  meshes + bounds + property sets + house summary per element
  ├─► src/export_glb.py  -> generated/model.glb -> web/ (three.js) browser viewer
  ├─► src/q.py (./q)     answers questions about the actual geometry
  ├─► src/rules.py       declarative data/rules/*.json on the actual geometry
  └─► src/run_checks.py  all checks in one process, create_shape served from the cache
```

## Commands

| What | Command |
|---|---|
| Build + verify everything | `./build.sh` |
| Checks only / matching ones | `./check.sh` / `./check.sh rules` |
| Viewer with hot reload | `./open.sh` (details in docs/viewer.md) |
| Wait for the viewer's build + checks | `./open.sh --no-browser --wait` |
| Screenshots | `.venv/bin/python src/snap_web.py room=KEY plan=GF ...` |
| Ask the model | `./q help` |
| Storeys of this house | `./q storeys` |
| Room cards | `./q room [name]`, `./room sketch|check|snap <room>` |
| Reusable product placements | `./place list|show|add|move|remove|check` |
| Variants | `.venv/bin/python src/variant.py list|show|load|save|check` |
| Source documents | `./q sources`, viewer button **Sources** |
| Plan overlays (needs registered PDFs) | `./q compare GF` |
| Unit tests | `.venv/bin/python -m unittest discover -s tests` |
| Browser regression | `node tests/browser_viewer.mjs <viewer URL>` |

The launcher and screenshot tool request `/api/refresh`: make the model and checks
current and reuse successful results for identical inputs. Build and check freshness are
verified from content fingerprints of `data/`, `assets/` and the builder modules, not from
the server's displayed status. Editing a checker alone reuses the IFC.

## Ask instead of guessing: `./q`

```
./q find shelf                      keys, class, storey, size, product name
./q show GAL_SHELF                  bounds, size, height above the storey floor, properties,
                                    and the data/ file + JSON path that defines it
./q at 2.0,3.0 --storey GF          what is at a point + distance to the nearest surface
                                    in +x -x +y -y down up (wall faces, furniture, slabs)
./q near GAL_SHELF                  neighbours with clear gaps per axis
./q free 2.9,1.2,0,3.94,2.3,2.1     is this box free? (exact triangle/box test)
./q fits GAL_CENTRE_TABLE --at 1,1  would it fit with its min corner moved there?
./q clash GAL_PLANT                 actual-mesh interpenetrations > 5 mm
```

Geometry answers come from the built IFC; `room`, `storeys` and `sources` read the source
indexes. If the cache is behind the IFC, `./q` refreshes it first. If `data/` changed since
the last build, it warns.

## The house index

`data/house.json` names the project, the output IFC and the storeys:
`{"code": "GF", "name": "Ground floor", "elevation": 0.0, "file": "ground_floor.json"}`.
The code is the storey key everywhere: `./q --storey GF`, element keys (`W_WEST@GF` when a
name repeats on several storeys), rule selectors, the viewer's storey buttons and the hash
(`storeys=GF,UF`, `cut=GF@1.2`). Storey order is list order. Everything contained in the
IfcSite (terrain, parcel lines, site slabs, trees) has the pseudo code `SITE`. An optional
`"site": "site.json"` adds the site.

## Plan JSON sections (per storey file)

`building_dimensions.storey_height`; `exterior_walls`/`interior_walls` (start, end,
thickness, optional height, rgb, props); `profile_walls` (absolute (y,z) polygon extruded
over x0..x1: gables, knee walls); `windows` and `doors` referenced to a wall by
`offset_along_wall`; `windows_abs` (absolute `box`, optional stepped `opening_boxes` and
styled `parts`, `kind: door` for door openings); `free_doors` (leaf without an opening);
`slabs` (rects, thickness, `recess_finishes` so floorings replace slab material);
`floor_build_up` (extra layers); `floorings`; `wall_coverings`; `stairs` (straight sawtooth
flights with railing sides) and `railings` (void guards); `roof_planes` (absolute (y,z)
profiles, `interior_finish` from `roof_finishes`, `ifc_class: IfcWindow` + `cuts` for a
roof window); `spaces` (IfcSpace rooms); `furniture`/`sanitary`/`door_hardware` (boxes,
cylinders, spheres, prisms, `mesh`, `parts` with `texture`, `access`, `props`, optional
`gallery` -> `Pset_Gallery`); `door_swings`; `outlets`; `switches`. Products in
`data/products/` are merged in through `data/placements.json`. Full key reference:
`docs/data-format.md`.

## Rules are data

`data/rules/house.json` holds house-wide rules; a room gets its own
`data/rules/<room>.json`. Kinds (see `src/rules.py`): `no_penetration`, `clear` (a zone
must stay free, e.g. a door swing or a walkway), `bbox` (exact faces and size), `inside`,
`gap`, and `ray` (nearest surface distance, or no hit for a void). A pre-existing overlap
can be listed as `"A|B": "UNREVIEWED ..."`: it stays visible as a warning, while any new
one fails the build. An intentional overlap gets a real reason (e.g. a flush-mounted
cabinet). Prefer a few rule lines over a new `check_*.py`.

`src/check_model.py` keeps the generic invariants over the JSON itself: unique ids,
resolving references, fixtures on real wall faces, door swings, roof headroom, access
zones, dimensions versus the datasheet claim, metadata presence.

## Why not IFC in the browser, a game engine, or a DCC tool

- **IFC** stays as the export format, but nobody reads it at runtime. Tessellating it is
  the slow part, so it happens once, incrementally.
- **Game engines** use heavy binary scenes that AI agents edit poorly. JSON sources plus
  glTF output are diffable and light.
- **Desktop BIM/DCC viewers** reload whole files and cannot be driven from a script
  reliably; the browser viewer swaps the model in place and is screenshot-able headless.

## Invariants the tools rely on

- An element key is its IFC Name (= the JSON `id`), plus `@<storey code>` if the name
  repeats across storeys. Storey codes come from `data/house.json`; the IfcBuildingStorey
  Name is the storey name and its LongName the code.
- The geometry key hashes representation, placement, openings, styles and materials, with
  STEP ids and GUIDs normalised away. Bump `CACHE_VERSION` in `geometry_cache.py` when
  the meshing or the cache metadata changes.
- The mesher is ifcopenshell's `hybrid-cgal-simple-opencascade`; bounds agree with
  OpenCASCADE within 1 mm. Representations that mix solid kinds are meshed per element,
  because the iterator drops parts of them.
- glTF is y-up: IFC (x, y, z) maps to glTF (x, z, -y). Colours are authored as sRGB and
  written to glTF as linear.
- Image surfaces use standard `IfcImageTexture` and `IfcIndexedTriangleTextureMap`
  records. `image_textures.py` reads the explicit IFC UVs back through tessellation and
  cache reuse; GLB embeds the images; OBJ/MTL export them too. Ship `generated/textures/`
  beside the IFC or OBJ. The authoring primitive is a planar projection on a box;
  unsupported UV layouts fail instead of silently changing the artwork.
- Variant round trips compare copies in memory; checks never temporarily switch the
  active layout. Active variant drift fails validation.
- `pipeline.py` holds one OS lease across IFC generation, tessellation, export and
  checks; child steps inherit it. Placement edits and variant loads use the same lease.
  Separate worktrees have independent leases. Edits made during a build invalidate its
  source fingerprint and require another build.
- Geometry arrays, metadata and GLB carry the IFC content digest. Readers reject mixed
  exports; the browser keeps its previous model and retries. Validation fingerprints
  include every Python checker and helper.
