# Furnishing from a sketch or a real product

## Preserve the brief

Open the actual screenshot/photo before interpreting it. Resolve left/right into
north/south/east/west, including which direction a shelf opening faces. If a path
is missing, check the obvious extension and nearby filenames, then ask for the
missing reference; do not invent the requested layout. Convert HEIC with `sips`
to a temporary PNG and view it. Use available tools rather than assuming one
specific image viewer or library is installed.

A useful clarification asks only what affects the fit: an ambiguous measured
edge, chosen product size, or child's age/height for adjustable seating. Continue
independent work while awaiting the answer. Do not reconfirm dimensions already
provided. The linked 160cm MANDAL wins over an old 140cm sketch once confirmed.

A hand sketch is often not north-up: fix its orientation from the door, the
windows and any notch before mapping lengths onto walls. Ceiling services in the
cellar are real IFC products; compare tall furniture against their actual bounds.

## Choose products before designing around them

IKEA product pages render client-side, so a plain fetch of the search page is
empty. The site's search API works:
`https://sik.search.blue.cdtapps.com/ch/de/search-result-page?q=<name>&size=24`
returns name, article, measurement text, price and product URL as JSON; the
product URL itself then gives components and exact dimensions.

Read the manufacturer's exact regional product page and dimension/assembly
illustration. Record article/variant, finish, overall width/depth/height, proposed
setting for adjustable items, date checked and source URL. A product family name
alone is not a purchase specification. Inspect its photo as well as the text.

- Nominal mattress sizes exclude bed rails; drawer depths may exclude fronts;
  frames may exclude legs, handles, hinges or suspension rails. Fit the assembled
  envelope, and keep an explicit component list for modular IKEA combinations.
- Check assembly drawings when a listing is ambiguous. If their dimensions
  conflict, document both and the chosen planning envelope; never silently claim
  both are exact. Preserve enough room for the uncertainty.
- A seven-year-old needs adjustable child seating, not a scaled adult chair.
  Record the adjustment range; the pictured setting is provisional until fitted
  to the actual child. Check desk height against window sill/leaf movement too.
- “Existing” furniture is not a newly ordered product. If the owner's photograph
  lacks dimensions, use an explicitly labelled estimate and identify what should
  be measured. Do not ask for estimates already authorized by the owner.
- Stop asset searching once an appropriate licensed model is found or the benefit
  of more searching is low. A faithful authored model from product photos and
  dimensions is acceptable; label it as simplified, not manufacturer geometry.

## Design for use, then composition

Start with the owner's anchors: bed, desk/window relation, built-in fronts and door
routes. Use actual furniture footprints in `data/*_layout.json`; draw the concept
with `src/plan2d.py` or the room's existing planner. Clear reserves belong in the
same data, not only in prose.

Check full door/wardrobe sweeps, drawer travel, chair pullback, ladder landings,
standing headroom and access to the next piece. Do not reduce a full sweep check
to one open/closed leaf. Test the swept region against geometry at the relevant
height: floor rugs can catch low doors; an empty region in a shaped mesh's bounding
box may require triangle-level checking. Room checks already contain examples.

For an attractive arrangement, use coherent alignments and a small material
palette drawn from the chosen pieces. Relate a rug to a functional grouping rather
than leaving a random strip of fabric in circulation. Keep deliberate empty space.
Place a few representative books, artwork and useful objects so their scale and
orientation read naturally. A plant's pot fitting is insufficient: check the
canopy, window sweep and cupboard access. Do not add furniture just to fill space.

In an attic, inspect the real roof from a seated/standing interior camera as well
as a labelled roof-off overview. Never adjust architecture merely to make a
preferred furniture product fit. Reduce/rearrange the furniture within the brief.

## Editable, sourced objects

One real cabinet = one `IfcFurnishingElement`; use `parts` for carcass, fronts,
handles, shelves and different colours. Do not split one object into unrelated
entities solely by colour. Mark genuinely protruding handles as `protruding`;
include them in collision checks while comparing the body to catalogue dimensions.

Use required metadata fields `Product`, `Dimensions`, `Lieferant`, `Contract`,
`Plan`, plus article/URL, mesh source/license and approximation notes where useful.
“No order placed” is valid for a proposal. Do not invent a supplier or contract.
Source the construction and visual finish separately if they come from different
documents. Render-only decoration must not masquerade as actual IFC furniture.

- Authored meshes (`artpiece_geometry.write/soft/lathe/rods`) are files in `assets/`.
  Author them around the origin, encode size and direction in the file name
  (`kids_og.aname`), place them with `at`, and set `artpiece_geometry.PREFIX` per
  room. Two variants writing the same asset name with different geometry silently
  corrupt each other's IFC; the per-variant IFC-vs-JSON bounds check catches it.

- `check_model.allboxes()` ignores the `mesh_bounds` of authored mesh *parts*;
  room checks that compare IFC bounds, clear zones or interpenetration must use
  `check_model.bodyboxes()` (or add the part bounds) or every mesh part is invisible
  to them. A prism authored to rest on a flat must have a flat, not a vertex, at
  its bottom: `lathe`/`loft` rings starting at angle 0 give a flat for even sides.
- Check installed service hardware as well as pipes: mounting rails/clamps can be
  `IfcDiscreteAccessory`, and equipment hoses can be `IfcPipeSegment` outside the
  main pipe list. The gym guitar collided with a rail that distribution-only
  checks missed (24 September 2026). Include equipment parts and inspect the
  furnished room view; allow only the thin finish overlap needed for wall fixing.
- The cellar ceiling is 2.395 m inside the surveyed downstand polygon
  (`data/cellar_heating_survey.json`), 2.495 m elsewhere; the model slab is flat
  at 2.50. Mount ceiling items at the real height like the light battens and say so.
- For pull-up equipment, check the head rising above the grip as well as the
  hanging body and frame. Include ceiling lights, pipe insulation and the bar's
  own uprights; record the assumed body envelope and clearance in the layout.

Keep generators, room layout, saved active variant and active floor JSON in sync.
Regenerate the intended variant, load it, then inspect the resulting diff. Do not
restore deleted alternatives or stack multiple furnishing sets in one room.

- `check_model.check_dims_and_meta` reads the FIRST `a x b x c` triple in
  `Dimensions` and needs it to match a part or the whole body within 2 cm. Put the
  assembled envelope first (`153 x 39 x 146.5 cm (2 Regale je 76.5 x ...)`), not
  a per-module size or a range like `90-172`. Cylinders become polygons in the
  IFC, so bounds comparisons of castor/lift geometry need ~0.5 mm tolerance.
- Collision checks are box based: a single rod mesh spanning a lamp arm claims
  its whole diagonal box. Split arms at the elbow so each part's box is tight.
- Two product variants of one room: keep the owner's anchors in one layout file,
  derive footprints from catalogue sizes per variant and write them back next to
  the anchors (`data/office_dg_products_layout.json`, `src/gen_office_products.py`).

## Existing examples

- DG kids: `data/kids_dg_layout.json`, `src/plan_kids_dg.py`,
  `src/gen_kids_dg.py`, `src/kids_smastad_storage.py`, `src/check_kids_dg.py`.
- DG foyer: `data/dg_foyer_layout.json`, `src/gen_dg_foyer.py`,
  `src/check_dg_foyer.py` (includes actual mesh/window-sweep clipping).
- DG southeast: `docs/artpiece-dg-south-east.md` and its referenced generator.
- OG playroom variants: `docs/kids-og-variants.md`, `src/kids_og.py` (room constants,
  catalogue builders), `src/kids_og_store.py`, `src/gen_kids_og.py`, `src/check_kids_og.py`,
  `src/render_kids_og.py` (Cycles room views with per-variant `VIEWS`).
- Entrance joinery: `src/gen_entrance_wardrobe.py` and
  `src/check_entrance_wardrobe.py`.

Use these as patterns, not immutable layouts. The latest user correction wins.
