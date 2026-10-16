---
name: find-asset
description: Find, inspect, convert and license a 3D mesh (OBJ) for the Monarch house model, or author a faithful procedural substitute with src/gen_assets.py. Use when furniture, a plant, a fixture or equipment needs a real shape that boxes cannot express ("find a mesh for a ...", "put a plant from Poly Haven ..."). Use model-change for the room layout and placement.
---

# Bring a mesh into the model

Simple cabinetry stays editable IFC solids (boxes, cylinders, prisms). Use a mesh when the
shape matters (a chair, a plant, a lamp, a bathtub). Placement and room verification
follow `../model-change/SKILL.md`.

## 0. Look at the product before you build anything

Open the product photo and, if it exists, the manufacturer's dimension drawing with the
Read tool. Write ONE sentence describing the shape (what is round, what is flat, which
side is open, what sits where) and keep it as `MeshNote`. Text descriptions lie by
omission; a photo does not. Procedural builds follow the same rule: photo first,
then geometry, then a render compared side by side with the photo.

## 1. Know the real dimensions before you search

In order of authority: the chosen variant's manufacturer drawing or datasheet, the plan
or survey the user gave you, the product page. Watch for footprints that differ from the
nominal size (splayed legs, handles, overhangs): the stand area is what has to fit.

## 2. Search

```
uv run python src/find_asset.py "office chair" --count 10 [--platform polyhaven|sketchfab]
```

Prints title, stated dimensions, licence, preview and download URL per hit. Only CC0
(Poly Haven) or CC-BY with attribution (Sketchfab) are acceptable; record author and URL.
Search in English and by category when the brand gives nothing.

## 3. Look at the preview before downloading

`curl -sL -o /tmp/p.jpg "<preview url>"` and read the image. One tool call rules out the
wrong product, a logo or a cartoon.

## 4. Download and probe

```
curl -sL -A Mozilla/5.0 -o /tmp/x.glb "<download url>"
/Applications/Blender.app/Contents/MacOS/Blender -b --python src/convert_asset.py -- /tmp/x.glb /tmp/probe.obj origin=center
```

Read the printed `imported bbox` and object inventory. A bbox far bigger than the product
means a whole scene (ground, backdrop, scale figure): drop strays with
`exclude=Mesh_1,Mesh_18` on the next run. Collada files go through
`src/dae_to_obj.py` first.

## 5. Convert, scaled to the datasheet

```
/Applications/Blender.app/Contents/MacOS/Blender -b --python src/convert_asset.py -- /tmp/x.glb assets/<name>.obj \
    exclude=... fit_x=0.60 fit_z=0.85 origin=center decimate=20000
```

One `fit_*` scales uniformly; several scale each named axis. `origin=center` for
free-standing objects, the default `wall` for fixtures (x centred, y_min 0 at the wall
face, z_min 0). Check the printed `final bbox` against the datasheet and write down every
place they disagree. Name the file after the product, not the platform. Procedural
shapes: add a function to `src/gen_assets.py` (helpers: `loft`, `cap`, `box`, `bar`).

## 6. Wire it in with honest metadata

Reference the mesh from a furniture entry (`"mesh": "assets/<name>.obj", "at": [x, y, z],
"rotation_z": 0, "scale": 1`) or as one `parts[]` entry of a hybrid product, and give
`mesh_bounds` so the 2D sketch and the access check know its footprint. Required props:
`Product`, `Supplier`, `SourceURL`, `Dimensions` (the product's real dimensions),
`License`, plus `MeshSource`, `MeshLicense`, `MeshURL`, `MeshAuthor`, `MeshNote` (native
size, what you scaled it to, every remaining difference). Add the same attribution line to
`assets/README.md`. `"SurfaceShading": "smooth"` in props renders sculpted meshes smooth.

## 7. Verify visually before reporting

```
./open.sh --no-browser --wait
.venv/bin/python src/snap_web.py close=<cam x,y,z>,<target x,y,z> sel=<ID>
./q show <ID>
```

Three checks that each catch real mistakes: did the asset actually change (vertex count
and bbox of the OBJ), is the camera in open space (a flat grey image means it is inside a
wall), and does the render look like the photo (silhouette, open side, proportions)?
Then confirm the numbers: `./q show` bounds against the datasheet, `./q clash` for
penetrations.
