# Data format

Everything under `data/` is plain JSON in metres. `src/build_model.py` compiles it to IFC; the
checks in `src/check_model.py`, the rules in `src/rules.py` and the tools (`./q`, `./place`,
`./room`, `src/variant.py`) read the same files. Keys not listed here are ignored, so
`_comment`/`_source` notes are welcome (say why, not what).

## Coordinates

- Frame: x east, y north, z up; origin = south-west outer corner of the ground floor at
  finished floor level (`house.json: origin_note`).
- **Storey-local z** (0 = that storey's finished floor): `furniture`, `sanitary`, `door_hardware`,
  `slabs`, `floorings`, `floor_build_up`, `wall_coverings`, `stairs`, `railings`, `spaces`,
  `door_swings`, `outlets`, `switches`, window/door `sill_height`.
- **Absolute z**: `windows_abs` (`box`, `opening_boxes`, `parts`, `floor_recess_boxes`),
  `profile_walls` and `roof_planes` profiles. Rules (`data/rules`), `./q` and the viewer use
  absolute coordinates throughout.
- Boxes are `[x0, y0, z0, x1, y1, z1]`, cylinders `[cx, cy, z0, r, z1]`, spheres `[cx, cy, cz, r]`,
  prisms `{"x0", "x1", "profile": [[y, z], ...]}` (a (y, z) polygon extruded along x).
- Rotations are degrees counter-clockwise about z; `./place` accepts only multiples of 90.

## house.json

```json
{"name": "Casa Monarch", "ifc_file": "generated/casa_monarch.ifc", "units": "meters",
 "storeys": [{"code": "GF", "name": "Ground floor", "elevation": 0.0, "file": "ground_floor.json"},
             {"code": "UF", "name": "Upper floor", "elevation": 2.9, "file": "upper_floor.json"}],
 "site": "site.json"}
```

`code` is the storey key everywhere (`./q --storey GF`, `NAME@GF`, rules, viewer buttons,
`./place --storey`). Storey order is list order; `site` is optional.

## Storey JSON sections

Every entry has a unique `id` (stable key, upper case, prefixed by room: `GAL_`, `KIT_`). Ids
with a `props` block get `Pset_Product`; `data/sources.json` adds `Pset_Source` by prefix.

### building_dimensions

`{"width_x": 11.0, "depth_y": 8.5, "storey_height": 2.9}`. `storey_height` is the default wall
height.

### exterior_walls, interior_walls

```json
{"id": "W_S", "start": [0.0, 0.15], "end": [11.0, 0.15], "thickness": 0.3,
 "height": 2.65, "rgb": [0.92, 0.9, 0.86], "props": {...}}
```

Centre line from `start` to `end`; `height` optional. Windows and doors reference walls by
id; `offset_along_wall` is measured from `start`. Butt walls against each other instead of
overlapping them at corners (two solids sharing a face flicker in every viewer).

### profile_walls

`{"id": "GABLE_W", "x0": 0.0, "x1": 0.3, "profile": [[y, z_abs], ...], "rgb", "props"}`: a (y, z)
polygon extruded over x0..x1, absolute z. Gables and the infill between a 2.7 m wall and a
sloping roof. Doors cannot be cut into a profile wall; `windows_abs` can (`host`).

### windows, doors

```json
{"id": "WIN_X", "wall_id": "W_N", "offset_along_wall": 1.5, "width": 1.2, "sill_height": 0.9,
 "height": 1.3, "glazing": "frosted", "props": {...}}
{"id": "DOOR_ENTRY", "wall_id": "W_N", "offset_along_wall": 6.5, "width": 1.0, "sill_height": 0.0,
 "height": 2.2, "rgb": [0.2, 0.21, 0.22], "props": {...}}
```

A window is an 8 cm glass pane, a door a 5 cm leaf in the wall colour (`rgb`) or wood. A window
entry that also has `parts` is built as an absolute assembly (see `windows_abs`).

### windows_abs

Openings with explicit geometry, absolute z. Casa Monarch models every window this way to get
anthracite frames:

```json
{"id": "WIN_LIV_S", "host": "W_S", "cut_axis": "y",
 "box": [0.7, 0.0, 0.9, 2.9, 0.3, 2.2], "nominal_width": 2.2, "nominal_height": 1.3,
 "kind": "door",                                   // optional: IfcDoor instead of IfcWindow
 "opening_boxes": [[...]],                          // optional stepped reveals; default: box +-5 cm along cut_axis
 "parts": [{"name": "frame", "rgb": [0.2, 0.21, 0.22], "boxes": [[...]]},
           {"name": "glass", "glazing": "clear", "boxes": [[...]]}],
 "props": {...}}
```

`box` is the aperture, `cut_axis` the wall's normal direction (`y` for a wall running along x).
Without `parts` a plain pane/leaf fills the box; `frame_width` (doors, `cut_axis: x`) draws a
casing. `floor_recess_boxes` cut pockets into slabs for floor-to-ceiling glazing.

### free_doors

`{"id", "center": [x, y], "axis": "x"|"y", "width", "height", "thickness", "sill_height", "rgb"}`:
a leaf without an opening (closet doors, sliding panels).

### slabs

```json
{"id": "SLAB_UF", "z_top": 0.0, "thickness": 0.25, "recess_finishes": true,
 "rects": [[0.3, 0.3, 5.02, 8.2], [6.08, 0.3, 10.7, 8.2]], "rgb": [...], "props": {...}}
```

`z_top` storey-local. Leave the stair void out of `rects`. With `recess_finishes` the slab top
drops by each flooring's thickness under it, so finishes sit flush at `z_top`.
`underside_zones` (`{"rects", "z_bottom"}`) model local downstands.

### floorings, floor_build_up

```json
{"id": "FLOOR_GF_OAK", "z_top": 0.0, "thickness": 0.015, "rgb": [0.7, 0.55, 0.36],
 "rects": [[0.3, 0.3, 7.9, 4.08]], "props": {...}}
```

Top at `z_top`, thickness downwards. Keep rects disjoint and aligned to wall faces. A thin patch
above the floor (`z_top: 0.002`) draws a marking without recessing the slab (the gallery
walkway). `floor_build_up` entries (`z_bottom`, `z_top`, `rects`, `rgb`) are extra layers.

### wall_coverings

`{"id", "rgb", "boxes": [...], "props"}`: tiles and panels as thin boxes on the room side of a
wall face (face at y = 8.2 -> box y 8.19..8.2). Split them around openings.

### stairs

```json
{"id": "STAIR_GF_UF", "x_range": [5.02, 6.02], "y_start": 4.5, "y_end": 8.19, "rise_total": 2.9,
 "risers": 15, "riser_h": 0.19333, "tread": 0.246, "waist_vertical": 0.19,
 "railings": [{"side": "high_x", "mount": "flight"}], "opening_x_range": [5.02, 6.08]}
```

A straight sawtooth flight along y, climbing from `y_start` towards `y_end` (`risers x tread`
long), with a handrail 0.9 m above the nosings. `data/stair_finishes.json` adds timber treads
and railing colours. Cut the void in the slab above from the step where headroom drops under
2.0 m.

### railings

`{"id", "from": [x, y], "to": [x, y], "height": 1.0, "rgb", "props"}`: void guards and balcony
railings, rods every 11.5 cm under a flat top rail.

### roof_finishes, roof_planes

```json
"roof_finishes": {"plaster": {"name": "Plasterboard lining", "thickness_m": 0.03, "rgb": [...], "props": {...}}},
"roof_planes": [
  {"id": "ROOF_S", "x0": -0.4, "x1": 11.4, "interior_finish": "plaster", "rgb": [0.3, 0.3, 0.32],
   "profile": [[-0.4, 5.42], [4.25, 7.5], [4.25, 7.8], [-0.4, 5.72]], "props": {...}},
  {"id": "RW_BEDROOM", "ifc_class": "IfcWindow", "cuts": "ROOF_S", "x0": 2.0, "x1": 2.78, "style": "glass",
   "profile": [[1.2, 6.2], [2.25, 6.67], [2.25, 6.77], [1.2, 6.3]], "props": {...}}]
```

A roof plane is a (y, z) quadrilateral (underside first, increasing y, then the top edge back)
extruded over x0..x1, absolute z. `interior_finish` splits off a lining of that thickness. A
plane with `ifc_class: IfcWindow` and `cuts: <host>` is a roof window that bores a real hole
through the host. The headroom check uses the lowest plane above each piece of furniture.

### spaces

`{"id", "name": "GALLERY", "long_name": "Gallery", "polygon": [[x, y], ...], "height": 2.65,
"props": {"RoomNumber": "GF.06"}}` becomes an IfcSpace; `./q at` reports the room a point is in.

### furniture, sanitary, door_hardware

One physical thing = one entry = one IFC element, styled by parts:

```json
{"id": "GAL_SHELF",
 "parts": [{"name": "carcass", "rgb": [0.2, 0.21, 0.22], "boxes": [[0.3, 4.8, 0.0, 0.7, 4.82, 2.0]]},
           {"name": "shelves", "rgb": [0.7, 0.55, 0.36], "boxes": [[...]]},
           {"name": "print", "texture": {"image": "assets/picture_great_wave.jpg", "u_axis": "-y", "v_axis": "+z"}, "boxes": [[...]]}],
 "access": {"dir": "+x", "depth": 0.75, "ignore": "STUDY_CHAIR"},
 "props": {"Product": "...", "Supplier": "...", "SourceURL": "...", "Dimensions": "40 x 80 x 200 cm",
           "License": "MIT (project data)", "Status": "Proposed"},
 "gallery": {"Title": "...", "Provenance": "...", "License": "...", "HowTo": "...", "Prompt": "..."}}
```

- Geometry keys on the entry or on each part: `boxes`, `cylinders`, `spheres`, `prisms`,
  `mesh` (+ `at`, `rotation_z`, `scale`), `texture` (image projected on the part's boxes;
  `u_axis`/`v_axis` are `+x`, `-y`, `+z`, ...; images must live under `assets/`), `rgb`,
  `transparency`, `name`.
- Meshes: OBJ in metres, z up. Give the entry `mesh_bounds` (its world bbox) so the checks and
  2D sketches know its footprint; copy the sidecar's `props` (MeshSource, MeshLicense, MeshURL,
  MeshAuthor) into `props`.
- `ifc_class`/`predefined_type` (`IfcSanitaryTerminal` + `TOILETPAN`, `IfcElectricAppliance` +
  `WASHINGMACHINE`); default `IfcFurnishingElement`.
- `access`: the floor you stand on to use it; other furniture there is a warning.
- `surface: true`: rugs and paint, blocks nothing.
- `gallery`: written as `Pset_Gallery`; the viewers label such elements.
- `light`: the photometry of a lamp, written as `Pset_Light` and used by the viewers' Night look
  (`N`). Give lamps `"ifc_class": "IfcLightFixture"` (`predefined_type` `POINTSOURCE` or
  `DIRECTIONSOURCE`):
  `{"lumen": 800, "kelvin": 2700, "kind": "point"|"spot", "angle_deg": 110 (spot: full beam),
  "direction": [0, 0, -1], "at": [x, y, z] (light source, same frame as the entry),
  "emissive": [[r, g, b]] (part colours that glow), "source": "E27 LED 800 lm, 2700 K",
  "switch": "a" (optional wall-switch group)}`. Values edited in the local viewer land in
  `data/lamp_overrides.json` and win over the block. Use warm white (2700-3000 K) inside.
- `props.Dimensions` must read `W x D x H cm` and match the geometry (whole bbox or one part)
  within 2 cm, or the build fails (`DIMS`). `Product`, `Dimensions` and `SourceURL`/`Plan` are
  required (`META` warning otherwise).

### door_swings

`{"id", "door": "DOOR_WC", "kind": "door"|"balcony_door", "hinge": [x, y], "closed": [x, y],
"open": [x, y]}`: the swept quarter disc on the floor. Furniture in a swing is a warning;
under 60 degrees of opening it fails the build.

### outlets, switches

```json
{"id": "OUT_GAL_W", "wall": "W_W", "at": [0.3, 6.2], "normal": [1, 0], "gangs": 2, "sill": 0.3, "props": {...}}
{"id": "SW_GALLERY", "wall": "W_GAL_S", "at": [3.65, 4.2], "normal": [0, 1], "height": 1.05,
 "columns": 1, "controls": [{"circuit": "gallery track lights"}], "props": {...}}
```

`at` is the point on the wall face (centre line +- half thickness), `normal` points into the
room. The MOUNTED check rejects plates off the wall, in an opening or behind furniture.

## products/ and placements.json

A product is reusable geometry in its own local frame (metres, back at y = 0 for wall-standing
pieces); `props` give the metadata, `label` the product name:

```json
{"id": "demo-bookshelf", "label": "Demo bookshelf 80 x 40 x 120", "kind": "catalogue_product",
 "props": {"Supplier": "...", "SourceURL": "...", "Dimensions": "80 x 40 x 120 cm", "License": "..."},
 "geometry": {"access": {"dir": "+y", "depth": 0.75}, "parts": [...]}}
```

`./place add demo-bookshelf CAT_GALLERY_SHELF --storey GF --at 1.0,4.2,0 --rotation 0 --status Exhibit`
appends to `data/placements.json`:

```json
{"id": "CAT_GALLERY_SHELF", "product": "demo-bookshelf", "storey": "GF", "at": [1.0, 4.2, 0.0],
 "rotation_z": 0.0, "status": "Exhibit", "source": "docs/gallery.md"}
```

Ids start with `CAT_`, rotations are cardinal, `status` is required. Placements are merged into
the storey plan at build time, so they survive variant reloads and room regeneration.
`./place list | show | move | remove | check`.

## variants.json and variants/<group>/<variant>.json

```json
{"groups": {"gallery_centre": {"target": "ground_floor.json", "active": "bench",
  "id_prefixes": ["GAL_CENTRE_"],
  "variants": {"bench": {"description": "...", "file": "variants/gallery_centre/bench.json"},
               "sculpture": {"description": "...", "file": "variants/gallery_centre/sculpture.json"}}}}}
```

A variant file is `{section: [entries]}` in the storey schema. The group owns every id in any of
its variants plus the `id_prefixes`. `uv run python src/variant.py load gallery_centre sculpture`
removes the owned ids from the target, inserts the variant and rebuilds; `save` writes the
current entries back into the variant file; `check` (part of `./build.sh`) fails when the storey
JSON drifted from the saved active variant.

## rules/*.json

```json
{"rules": [
  {"id": "gallery-walkway-clear", "rule": "clear", "why": "...",
   "zone": [3.85, 4.25, 0.0, 4.65, 7.0, 2.1], "except": ["FLOOR_GF_WALKWAY_MARK"]},
  {"id": "gallery-shelf-size", "rule": "bbox", "element": "GAL_SHELF", "size": [0.4, 0.8, 2.0]},
  {"id": "kitchen-island-walkway", "rule": "gap", "a": "KIT_ISLAND", "b": "KIT_RUN", "axis": "x", "min": 1.0},
  {"id": "furniture-in-structure", "rule": "no_penetration", "what": {"class": ["IfcFurnishingElement"]},
   "into": {"class": ["IfcWall", "IfcSlab", "IfcStairFlight"]}, "tolerance": 0.005, "known": {}}]}
```

Kinds: `clear`, `bbox` (`expect` faces and/or `size`), `inside`, `gap`, `ray`, `no_penetration`.
Evaluated on the built IFC geometry (absolute metres) after every build; `severity: warn`
downgrades a rule. Element references are ids, `ID@UF` when a name repeats on several storeys.

## rooms.json

```json
{"id": "gallery", "name": "Gallery (GF.06)", "aliases": ["gal"], "storey": "GF", "target": "ground_floor.json",
 "prefixes": ["GAL_", "CAT_GALLERY_"], "bounds": [0.0, 4.0, 5.2, 8.5], "group": "gallery_centre",
 "rules": ["gallery"], "docs": ["docs/gallery.md"], "notes": "..."}
```

`./q room` lists the cards; `./room sketch gallery` draws `bounds`; `./room snap gallery` frames
every element whose id starts with one of `prefixes`.

## gallery.json

The exhibits of the tutorial room: `key`, `element` (id, list of ids, product id or `null`), `title`,
`provenance`, `license`, `howto`, `prompt`, `plaque: {at: [x, y], normal: [nx, ny]}`. See
`docs/gallery.md`; `uv run python src/gen_plaques.py` derives the PNGs, plaque boards and
`gallery` blocks from it.

## sources.json

Provenance by id prefix, written as `Pset_Source`:

```json
{"suppliers": {"authored": {"supplier": "none (authored in this repository)", "contract": "", "url": "docs/data-format.md"}},
 "rules": [{"prefix": "GAL_", "supplier": "authored", "plan": "docs/gallery.md", "notes": "..."},
           {"prefix": "DOOR_", "supplier": "fictional-joinery", "plan_by_storey": {"GF": "...", "UF": "..."}}]}
```

First matching prefix wins; `plan_by_storey` overrides `plan` per storey code.

## site.json

```json
{"terrain": {"id": "TERRAIN", "polygon": [[-7, -7], [18, -7], [18, 15.5], [-7, 15.5]], "z": -0.15, "base_z": -1.2,
             "holes": [[0.0, 0.0, 11.0, 8.5]], "rgb": [0.47, 0.62, 0.36], "props": {...}},
 "boundaries": [{"id": "PARCEL", "points": [...], "closed": true, "rgb": [0.85, 0.25, 0.2], "width": 0.08, "lift": 0.02}],
 "slabs": [{"id": "TERRACE", "rect": [0.4, -3.6, 6.0, 0.0], "z_top": -0.02, "thickness": 0.15, "rgb": [...], "props": {...}}],
 "objects": [ ...furniture schema, z above the terrain... ]}
```

Terrain is a flat surface at `z` with a skirt down to `base_z`; cut the house footprint out with
`holes`. Site elements belong to the pseudo storey `SITE`.

## stair_finishes.json

Optional. `timber_flights` lists the stairs that get oak treads (the concrete core is lowered by
the build-up); `metal_railings`, `handrail`, `painted_concrete` colour the rest. Delete the file
for bare concrete stairs.

## Metadata convention

Every product-like element carries `Product`, `Supplier` (or `"fictional"` / `"none (authored)"`),
`SourceURL` (or `Plan`), `Dimensions` (`W x D x H cm`), `License`, `Status`; meshes add
`MeshSource`, `MeshLicense`, `MeshURL`, `MeshAuthor` (and `Attribution` for CC-BY). Casa Monarch
is a model home, so its suppliers are fictional and its URLs point to `example.com` or into this
repository; the mesh and image provenance in `assets/README.md` is real.
