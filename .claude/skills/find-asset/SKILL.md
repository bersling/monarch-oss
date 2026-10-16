---
name: find-asset
description: Find, inspect and convert a licensed 3D product mesh into a correctly scaled monarch2 OBJ asset, or author a faithful simplified substitute. Use when furniture or equipment needs a mesh; use model-change for room layout and placement.
---

# Finding a mesh asset and getting it into the model

Use this workflow when a product needs a mesh; simple cabinetry can use editable IFC solids. Apply the relevant steps without prolonged asset searching once a faithful authored representation is sufficient. Coordinate placement and room verification with `../model-change/SKILL.md`.

## 0. LOOK at the product before you build anything

Download at least one product photo and, if it exists, the manufacturer's
dimension drawing, and open them with the Read tool. Text descriptions lie
by omission: "12 lights on golden rods" described a Ø 72 sphere of 260 rods
and was modelled as a flat chandelier; "flat roof with an arched lip" was a
bike shelter whose whole roof is one arc; "L517 x B592" included a swing
that the chosen variant does not have. Every one of those cost a round trip.

After looking, write ONE sentence describing the shape (what is round, what
is flat, which side is open, what sits where) and put it in `MeshNote`. If
you cannot write that sentence, you have not looked enough. When a user
screenshot or SketchUp study exists, read the numbers off it and reconcile
them with the datasheet before modelling.

Procedural builds (`src/gen_assets.py`) follow the same rule: photo first,
then geometry, then a render compared side by side with the photo.

## 1. Know the real dimensions BEFORE you search

Never scale a mesh to whatever looks right. Get the numbers from, in order:

1. the exact chosen variant's manufacturer dimension/assembly drawing or datasheet the user pasted (screenshots of the manufacturer's dimension
   drawing are the best case — read every number off it),
2. the plan in `./plans`,
3. the manufacturer's product page.

Watch for products where the **footprint differs from the nominal size**. An
INTEX Prism Frame Pool "400 x 200 x 100" needs 465 x 259 cm of ground, because
the support legs splay outwards. Its stand area, not its nominal size, is what
has to fit on the site.

## 2. Search

```
uv run python src/find_asset.py "intex rectangular pool" --count 10
```

This hits 3D Warehouse, Sketchfab (needs `$SKETCHFAB`) and Poly Haven in one go
and prints licence, preview URL and a direct download URL for each hit.

- **3D Warehouse wins for real building products and branded goods.** Its
  descriptions frequently state the real dimensions.
- Search in English, and search the *product category* if the brand name gives
  nothing ("rectangular frame pool", not "Intex 26790").
- Per-platform caveats live in `assets/README.md`.

## 3. Look at the preview before downloading

`curl -sL -o /tmp/p.jpg "<preview url>"` and actually read the image. It costs
one tool call and rules out the models that are a different product, a logo, or
a cartoon.

## 4. Download and probe

```
curl -sL -A Mozilla/5.0 -o /tmp/x.glb "<download url>"
blender -b --python src/convert_asset.py -- /tmp/x.glb /tmp/probe.obj origin=center
```

Read the printed `imported bbox`. If it is far bigger than the product, the file
is a **whole scene** — Warehouse GLBs routinely ship the prop plus ground,
trees, a backdrop plane and SketchUp's "Susan" scale figure. The converter also
prints an inventory of every imported object with its size; pick the strays out
of that and drop them with `exclude=Mesh_1,Mesh_18` on the next run.

Also drop meshes that stick out past the product's real silhouette (a stray
pump, hose or ladder), or they will inflate the footprint you are fitting.

## 5. Convert, scaled to the datasheet

```
blender -b --python src/convert_asset.py -- /tmp/x.glb assets/<name>.obj \
    exclude=Mesh_1,Mesh_18 fit_x=4.00 fit_z=1.00 origin=center decimate=20000
```

- one `fit_*` scales uniformly; several scale each named axis to its target
  (use this when the mesh has the right plan proportions but the wrong height),
- `origin=center` for free-standing objects, the default `wall` for fixtures,
- check the printed `final bbox` against the datasheet and **write down every
  place it disagrees** — that goes in the metadata in step 6.

Name the file after the product, not the platform: `pool_intex_prism_frame_400.obj`.

## 6. Wire it in with honest metadata

Add the entry to the right JSON in `data/`, then fill in the `props` that
`CLAUDE.md` requires: `ProductName`, `Lieferant`, `Contract`, `Plan`,
`Dimensions`, plus product-specific fields. On top of those, always record:

- `MeshSource`, `MeshLicense`, `MeshURL` — licences must travel with the model,
- `MeshNote` — the mesh's native size, what you scaled it to, and **every way
  the mesh still differs from the datasheet**.

`Dimensions` must state the *product's* real dimensions from the datasheet, not
the mesh's. For owned pieces with no measured dimensions, explicitly label a photo-based estimate instead. If product and mesh differ, the difference belongs in `MeshNote`. This file is
meant to become the project's source of truth, so a plausible-looking wrong
number is worse than an admitted approximation.

## 7. Verify visually, BEFORE reporting

```
./open.sh --no-browser --wait        # the viewer server rebuilds on save; this waits for it
.venv/bin/python src/snap_web.py close=<cam x,y,z>,<target x,y,z> sel=<ID>   # or: room=<ID>
./q show <ID>                        # actual IFC bounds and size, to compare with the catalogue
```

Look at the image, next to the product photo. Three checks that each caught
a real mistake in this project:

1. Did the asset actually change? Print the OBJ bbox and vertex count after
   regenerating; if they match the previous file, your edit did not run.
2. Is the camera in open space? A camera inside a wall gives a flat grey
   image. Put it in a room or outside, aim it at the object, and if the
   render is a single colour move the camera instead of shipping it.
3. Does the render look like the photo? Same silhouette, same open side,
   same proportions. If not, fix it now; do not describe it as done. Then confirm the numbers rather than trusting your eyes —
read the object's bounding box back out of `generated/haus_nord_essentials.obj`,
and check clearances against the parcel boundary with shapely.
