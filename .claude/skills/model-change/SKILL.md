---
name: model-change
description: Furnish or revise the monarch2 IFC house from plans, site measurements, screenshots and real products. Use for room layouts, furniture placement, materials, floors, roofs, doors and windows; includes clearance checks, actual-IFC visual review and safe Blender reload.
---

# Change the house model

Produce an editable, sourced IFC change that fits the room, works in use, and has
been inspected in a room view. Preserve the owner's selected layout and products.

## Start with the right evidence

Read `CLAUDE.md`, inspect git/worktrees, and locate the room and relevant source
plans. Use a separate worktree when other sessions may edit the checkout. Establish
compass orientation, storey datum and what the user actually changed before making
geometry. Treat incoming corrections as steering the active task, not a reset.

- For furnishing/product work, read [furnishing.md](references/furnishing.md).
- For dimensions, roofs, windows, finishes or source conflicts, read
  [sources-and-datums.md](references/sources-and-datums.md).
- Before building, rendering, merging or reloading, read
  [verification-and-delivery.md](references/verification-and-delivery.md).
- For an imported mesh, also use `../find-asset/SKILL.md`.
- For any IKEA product that is added, swapped or removed, also follow
  `../ikea-shopping-list/SKILL.md` before declaring the change done.

## Work from layout to verified model

1. Measure first: `./q at X,Y --storey S` gives the wall faces around a spot, and
   `./q near ID` the gaps. Record footprints, heights and access reserves in
   editable room layout data.
   Show walls, openings, door arcs and relevant roof contours in 2D. Reuse the
   existing room layout/checks for small edits.
2. Resolve circulation and usable storage before adding visual detail. A cabinet
   fitting under a roof is not proof that someone can stand at its front.
3. Generate from that layout, keeping one physical item as one IFC entity with
   differently styled `parts`. Update the saved active variant as well as the
   storey JSON; a generator/variant reload must not undo the change.
4. Validate every changed item against actual IFC geometry, not only the JSON:
   `./q show/at/near/clash/free` give exact numbers from the built model, and
   room constraints belong in `data/rules/<room>.json`. Then inspect a
   `src/snap_web.py` room view next to the user's reference and fix visible issues.
5. Land reviewed changes in main when authorized, then run
   `./open.sh --no-browser --wait` from main (hot reload plus checks). Report what
   actually finished and any material approximation. Do not call an unchecked
   build or an unviewed render complete.

## Project decisions that must not be lost

These apply to this house unless the user changes them:

- Covering power outlets with furniture is allowed and is never a reason to lift,
  shift, shrink or notch a piece (owner, 2026-09-19). The only fixture rule is:
  light switches stay uncovered and reachable. Keep doors and circulation usable.
  Window obstruction is decided case by case; identify the operable leaf and its
  sweep before moving anything in front of it.
- Catalogue furniture must retain actual catalogue sizes and configuration.
  Do not invent a narrow IKEA end module or protective socket niche and call it
  a real product. Distinguish custom, owned and proposed purchased furniture.
- Floors have a concrete slab and a finish build-up. Preserve the established
  finished floor and knee heights when furnishing; measure before and after if
  architecture/flooring is touched. Current values come from source data/checks.
- RAL 7021 applies to stair and landing metalwork only, not outdoor railings.
- DG foyer: no chairs beside the railings; user allows at most beanbags. A reading
  nook requested for the kids' room is a separate decision.
- Keep model information honest: measured, inferred, approximate geometry,
  proposed product and contracted finish are different kinds of evidence.

## Make failures reproducible

For a demonstrated geometry failure, extend an appropriate existing check to cover
all affected items. Tests should catch a meaningful failure, not mirror wording.
Global warnings are not success: inspect newly introduced door/access warnings.
Do not expand a local furnishing task into unrelated model cleanup.

Maintain this skill from demonstrated lessons. Keep changing task status in the
handoff, and project dimensions/catalogue lists in their source files or room docs.
