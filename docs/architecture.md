# How the model is built, checked, queried and shown

Overhauled 26 Sep 2026. The goals: AI edits must be fast and precise, the owner's
view must never crash, and hot reload must just work.

```
data/*.json, assets/*.obj            source of truth: absolute IFC coordinates, sourced props
  │  src/build_model.py (4.4 s)      JSON -> IFC. Only compiled code lives in src/;
  ▼                                  rooms are data (JSON + rules), not new scripts
generated/haus_nord_essentials.ifc   the deliverable for architects/BIM tools
  │  src/geometry_cache.py (3 s)     tessellated ONCE per build; products whose geometry
  ▼                                  key is unchanged are reused (16 s for a full re-mesh)
generated/geometry.npz + elements.json  meshes + bounds + property sets per element
  ├─► src/export_glb.py (0.2 s)  -> generated/model.glb -> web/ (three.js) browser viewer
  ├─► src/q.py (./q)             answers questions about the actual geometry
  ├─► src/rules.py               declarative data/rules/*.json on the actual geometry
  └─► src/run_checks.py          all checks in one process, create_shape served from the cache
```

## Commands

| What | Command | Time |
|---|---|---|
| Build + verify everything | `./build.sh` | ~20 s (was ~95 s) |
| Checks only / matching ones | `./check.sh` / `./check.sh gym kids` | ~12 s / ~1-2 s |
| Viewer with hot reload | `./open.sh` (details in docs/viewer.md) | edit to new model in the tab ~9 s |
| Wait for the viewer's build + checks | `./open.sh --no-browser --wait` | |
| Screenshots | `.venv/bin/python src/snap_web.py room=KEY plan=EG ...` | ~9 s for 3 views |
| Ask the model | `./q help` | ~0.3 s |
| Find room inputs and current decisions | `./q room office` | no IFC load |
| Find dated source PDFs | `./q plans DG` or viewer **Plans** | |
| Draw / generate / check / render a room | `./room sketch office`, `generate`, `check`, `snap` | |
| Reusable product placements | `./place list`, `show`, `add`, `move`, `remove` | |
| Unit tests | `.venv/bin/python -m unittest discover tests` | <1 s |

The launcher and screenshot tool request `/api/refresh`: make the model/checks
current and reuse successful results for identical inputs. Requests queued while
a check is running coalesce; they do not force another build after it finishes.
The viewer's explicit `/api/rebuild` command still forces geometry generation.
Build and check freshness are verified from content fingerprints, not only the
server's displayed status. Editing a checker alone reuses the IFC.

## Ask instead of guessing: `./q`

```
./q find kallax                     keys, class, storey, size, product name
./q show DG_SE_DIVIDER              bounds, size, height above the storey floor, properties,
                                    and the data/ file + JSON path that defines it
./q at 12.1,3.4 --storey DG         what is at a point + distance to the nearest surface
                                    in +x -x +y -y down up (wall faces, furniture, slabs)
./q near UG_GYM_BENCH               neighbours with clear gaps per axis
./q free 11.2,3.0,5.7,12.0,3.9,7.0  is this box free? (exact triangle/box test)
./q fits PAINTING_NORTH --at 7.0,6.31   would it fit with its min corner moved there?
./q clash KITCHEN_ISLAND_TOP        actual-mesh interpenetrations > 5 mm
```

Geometry answers come from the built IFC; `room` and `plans` read the source indexes. If the cache is behind the IFC,
`./q` refreshes it first. If `data/` changed since the last build, it warns.

## Shaped walls and mounted fixtures

`roof_planes` can reference a shared storey `roof_finishes` entry by
`interior_finish`. Its `thickness_m` is normal to the slope, with `rgb`, `name`
and sourced `props`. The first two roof-profile points are the finished soffit;
`roof_finishes.py` splits the lining behind it, preserving the full envelope.
Lining/core are styled solids in one IFC roof so its openings cut both layers.
This applies to quadrilateral slopes with vertical end cuts; arbitrary shaped
roof profiles need their own assembly interpretation.

Rooflight setting-out and raw section points live in `data/roof_windows.json`.
Run `.venv/bin/python src/gen_roof_windows.py` after editing them. It writes
styled absolute `parts[].prisms` and shaped `cut_parts` into the five existing
roof-window entries. A single IFC window contains its fixed frame, sash,
glazing and site lining; `OverallWidth/OverallHeight` retain the nominal frame
size, while the complete assembly includes wider reveals. `ProfileName`
identifies the real solids for frame-size and privacy-glazing checks. The
generator preserves room furniture and the finished roof/floor datums.

Reviewed facade assemblies live in `data/facade_windows.json`. Run
`.venv/bin/python src/gen_facade_windows.py` after changing a listed opening.
Type 1/2/3/4 templates generate 28 reviewed facade windows: all six DG windows
(three gables, south single dormer and north coupled pair), nine EG Type 1
and six OG Type 1 units, five full-height Type 2 units and two fixed Type 3
members. Type inheritance shares profile data;
`axis` selects the X/Y wall normal. Regular dormer records retain their IDs,
wall offsets and list membership while using the same absolute assembly path.
March rough pockets, inside/outside finished apertures and released supplier
sizes are separate references. The southeast inside aperture follows the owner
survey; its larger raw pocket accommodates the full selected frame.
The coupled pair has asymmetric 78/90 mm jamb faces and no centre fitting gap.
Glazing rebates derive from sash sight and glass dimensions.
`OverallWidth/OverallHeight` describe the frame, excluding joint fill; named IFC
profiles let checks measure each component independently. The generator preserves
room data. `./q plans window-supplier` finds the approved schedule/details.
UG assemblies, exterior threshold construction, individual handedness and omitted hardware remain to
review. Source conflicts are recorded in `docs/facade-window-review.md` and
`docs/attic-partition-review.md`.

Full-height templates support asymmetric `sash_overlaps`/`sash_face_widths` and
`bottom_extension` / `top_extension` with separate materials. A `shared_opening`
defines the continuous inner bay, installation pocket and separate exterior
apertures for vertically coupled members crossing storey boundaries. Each host
clips that shared void to its own geometry. Nominal IFC height includes the
extensions; `FrameHeight` reports the wood-frame height separately. Explicit
absolute `floor_recess_boxes` cut local installation pockets from intersecting
slabs and floor layers, through IFC opening relationships. They stop at the raw
sill, retain the floor datum and do not remove concrete below it. Changing a
frame must preserve the distinction between product, fitting joint and floor.

`profile_walls` extrude an absolute `[y,z]` polygon between `x0` and `x1`.
Fixture mounting defaults to the two X-normal sides. Set `mount_axis: "y"`
for an east-west wall whose vertical profile ends face north/south. The shared
`profile_wall_faces.py` exposes those faces in local free-coordinate/height
coordinates, so switch placement, outlet extraction and whole-plate validation
agree. A pointed profile end is not a mounting face. Absolute door openings are
checked along the corresponding free axis, including X for a Y-normal wall.

## Stepped window openings

An entry in `windows_abs` or a regular `windows` record may add `opening_boxes`
and `parts`. All their box
coordinates are **absolute**, like the existing `box`; furniture parts elsewhere
remain relative to the storey floor. `box` is the inside aperture envelope used
by layout/fixture tools. `opening_boxes` contains the connected solids removed
from the wall, including any required cutting depth beyond its exterior faces.
Each part has `name`, `boxes` and either opaque `rgb` or `glazing: "clear"` /
`"frosted"`. The frame, sash and glass remain one selectable IFC window with
individually styled solids. `props.Product` supplies its readable viewer label.

The northeast attic Type 1 window is the first audited example. Its properties
separate rough opening, inside and outside finished levels, contract dimensions,
and schematic frame/glass geometry. Do not resize a frame from a rough-opening
label. `data/rules/window-openings.json` checks actual jambs, sill/head faces,
through-bore, reveal shoulder, glass/sash planes and wall interference. Room
sketches show the stepped opening and frame section at floor +1.2 m; use
`./q compare` for an actual-IFC slice at another height.

## Rules are data

`data/rules/house.json` holds house-wide rules; a room gets its own
`data/rules/<room>.json`. Kinds (see `src/rules.py`): `no_penetration`, `clear`
(a zone must stay free, e.g. a door swing or a walkway), `bbox` (exact faces and
size), `inside`, `gap`, and `ray` (nearest surface distance, or no hit for a void).
For example, `data/rules/basement-ceiling.json` tests the actual stepped ceiling
without relying on one overall bounding box. A pre-existing overlap can be listed as
`"A|B": "UNREVIEWED ..."`: it stays visible as a warning, while any new one fails
the build. An intentional overlap gets a real reason (e.g. a flush-mounted mirror
cabinet). Prefer a few rule lines over a new `check_*.py`.

## Why not IFC in the browser, a game engine, or Blender

- **IFC** stays as the export format, but nobody reads it at runtime. Tessellating
  it is the slow part, so it happens once, incrementally.
- **Game engines** (Unity, Godot) use heavy binary scenes that AI agents edit poorly.
  JSON sources plus glTF output are diffable and light.
- **Blender/Bonsai** reloaded in place and crashed on stale ifcopenshell pointers
  (7 crash reports on 24-25 Sep 2026). It remains available as `./open.sh --blender`.

## Invariants the tools rely on

- An element key is its IFC Name (= the JSON `id`), plus `@UG/@EG/@OG/@DG` if the
  name repeats across storeys.
- The geometry key hashes representation, placement, openings, styles and
  materials, with STEP ids and GUIDs normalised away. Bump `CACHE_VERSION` in
  `geometry_cache.py` when the meshing changes.
- The mesher is ifcopenshell's `hybrid-cgal-simple-opencascade`. On all 1003
  products its bounds agree with OpenCASCADE within 1 mm. Representations that mix
  solid kinds are meshed per element, because the iterator drops parts of them.
- glTF is y-up: IFC (x, y, z) maps to glTF (x, z, -y). Colours are authored as sRGB
  and written to glTF as linear.
- Image surfaces use standard `IfcImageTexture` and `IfcIndexedTriangleTextureMap`
  records. `image_textures.py` reads the explicit IFC UVs back through tessellation
  and cache reuse; GLB embeds the images. OBJ/MTL also exports images and UVs from
  the shared cache. Send `generated/textures/` beside the IFC or OBJ when sharing
  those formats. The initial authoring primitive supports a planar projection on
  a box; unsupported UV layouts fail instead of silently changing the artwork.
- Variant round trips compare copies in memory; checks never temporarily switch
  the owner's active layout. Active variant drift fails validation.
- `pipeline.py` holds one OS lease across IFC generation, tessellation, export and
  checks. Child steps inherit it. Room generation and placement edits use the same
  lease. Separate worktrees have independent leases. Manual edits made during a
  build invalidate its source fingerprint and require another build.
- Geometry arrays, metadata and GLB carry the IFC content digest. Readers reject
  mixed exports; the browser keeps its previous model and retries. Validation
  fingerprints include every Python checker/helper, not a partial checker list.

## Next steps (state 26 Sep 2026, end of the overhaul session)

1. **Rooms as data.** Implemented 1 Oct: detailed KALLAX 2x2/4x2 definitions in
   `data/products/`, independent instances in `data/placements.json`, and `./place`.
   Further catalogue migration can cover BESTÅ frames/fronts and PAX with real
   sizes, parts and props. Migrate DG southeast first (today `gen_office_mixed.py` +
   inventory + variant files), then delete per-room generators as rooms move over.
   Measured motivation: a furniture change took an agent ~12 min, and under 1 min of
   that was build, checks and screenshots; the rest was reading code and docs.
2. **Room cards.** Implemented 1 Oct as `data/rooms.json` and `./q room`: current
   variant, inputs, generator, checks, docs and retained decisions. `./room` uses
   this index for sketches, generation, fresh-model checks and IFC screenshots.
3. **Structure overlap rule.** `data/rules/house.json` only checks furniture against
   structure. Add wall/slab/roof-vs-structure overlaps as warnings. Overlapping solids
   z-fight in the viewer (found: dormer cheeks through DORMER_S_FRONT, fixed) and are
   model errors anyway.
4. **Owner decisions pending.** The DG SE confirmations listed in
   `docs/office-mixed-products.md` (bench width, castor height, BESTÅ orientation,
   the white base in IMG_0310). The 15 formerly `UNREVIEWED` overlaps in
   `data/rules/house.json` were accepted by the owner on 26 Sep 2026 (not worth fixing).
5. **Small viewer leftovers.** Inverted faces on some slabs show section fill in cuts;
   contract paths under `felsenhofstrasse/...` became linkable on 1 Oct, including
   worktrees and unambiguous Unicode/umlaut filename aliases;
   the client diagnostics log (`POST /api/log`, `<state_dir>/web-client.log`) stays on
   as a debugging aid for problems only seen in the owner's real browser.
6. **Stale checks.** `check_artpiece_dg_south_east.py` and `check_artpiece_open_review.py`
   fail (variant not active / missing generated file) and are not in the suite;
   `check_viewer_reload.py` needs Blender. Fix or delete.
