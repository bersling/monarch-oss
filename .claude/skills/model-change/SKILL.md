---
name: model-change
description: Change the Monarch house model (this repository's data/*.json -> IFC). Use whenever the user wants to change their house model, add or move furniture, place a product, move or add a wall, window, door, stair or roof, change a floor or finish, or asks "put X in room Y". Covers measuring from the built model, 2D sketch first, editing the JSON, build, numeric verification with ./q, rules as data, product metadata and the screenshot review.
---

# Change the house model

Deliver an editable, sourced change to `data/` that builds with zero check failures, is
verified against the built IFC geometry, and has been looked at in a render. Never edit
`generated/`; never describe an unbuilt or unviewed change as done.

## 1. Find the measurement first

- `./q storeys` lists storey codes and floor elevations (from `data/house.json`).
- `./q at X,Y --storey GF` tells you what is at a point and how far the wall faces,
  furniture and slab are in +x -x +y -y up down. `./q near ID` gives gaps to neighbours,
  `./q show ID` the bounds, size, properties and the `data/` file + JSON path that
  defines an element. `./q room` lists room cards (files, prefixes, variant group).
- Real products: read the manufacturer's datasheet or dimension drawing the user gave
  you (open images with the Read tool) and write the numbers down before modelling.
  Look at the product photo and write one sentence describing its shape.
- Coordinates: metres, x east, y north, z up. Furniture z is above the finished storey
  floor; roof planes, profile walls and `windows_abs` use absolute z.

## 2. Think in 2D before 3D

For a room layout, sketch first: `./room sketch <room>` (or
`.venv/bin/python src/plan2d.py GF x0,y0,x1,y1 out.png concept.json`) draws walls,
openings, door swings, outlets and existing furniture; a concept file adds labelled
rectangles. Move rectangles until walkways, door swings and access zones work, then
write the 3D entries. Resolve circulation and usable storage before visual detail.

## 3. Edit the JSON

- Storey files are named in `data/house.json`; sections and keys are documented in
  `docs/data-format.md` and in `src/build_model.py` comments.
- One physical thing is one entry with styled `parts` (boxes, cylinders, spheres,
  prisms, `mesh`, `texture`), never several half-objects.
- Catalogue products: define once in `data/products/<id>.json`, place with
  `./place add <product> CAT_<ID> --storey GF --at x,y,z --rotation 90 --status Proposed`.
- Alternatives for a room: variants (`src/variant.py list|load|save`), owned by a group
  in `data/variants.json`. Update the saved variant too, or a reload undoes your change.
- Give an `access` zone (`{"dir": "-y", "depth": 0.75}`) to anything you stand in front of.
- Metadata on every product entry: `Product`, `Supplier`, `SourceURL` (or `Plan`),
  `Dimensions` ("W x D x H cm", checked against the geometry), `License`; for meshes
  also `MeshSource`, `MeshLicense`, `MeshURL`, `MeshAuthor`. Mark estimates as estimates.
- Need a mesh? Follow `../find-asset/SKILL.md`.

## 4. Build, then verify with numbers

- `./build.sh` builds IFC, cache, glTF and runs every check (about 2 s). Read the
  output: FAIL lines block, warn lines are design conflicts to resolve or justify.
- Verify the actual geometry, not the JSON: `./q show ID` (size), `./q clash ID`
  (interpenetrations), `./q fits ID --at X,Y` (does it fit there),
  `./q free x0,y0,z0,x1,y1,z1` (is a zone empty).
- Constraints that must keep holding become rules in `data/rules/<room>.json`
  (`clear` walkway zones, `bbox` sizes, `gap`, `no_penetration`, `inside`, `ray`).
  A rule in data beats a paragraph in a skill and a new check script.
- An intentional overlap (flush-mounted cabinet) gets a reason in the rule's `known`.

## 5. Look at it, then report

- `.venv/bin/python src/snap_web.py room=PREFIX plan=GF ext=-9,-12,9,4,3,2` renders
  PNGs with the browser renderer; open them with the Read tool. A single-colour image
  means the camera is inside a wall: move it. Compare against the user's reference.
- `./open.sh --no-browser --wait` keeps the user's viewer current and waits for checks.
- Report what changed, the measured numbers, what is approximate, and anything the
  user still has to decide. Work in a worktree when other sessions may be editing.

## Make failures reproducible

When a mistake slipped through, add the check that would have caught it (a rule in
`data/rules/`, or an invariant in `src/check_model.py` if it is generic) and the
measurement to the room's card in `data/rooms.json`.
