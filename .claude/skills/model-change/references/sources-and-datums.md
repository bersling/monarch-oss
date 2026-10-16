# Source interpretation, materials and vertical geometry

## Find the right source

Architecture and electrical plans live under `plans/`; the user also supplies
`floorplans_2026-03-05/` and local photographs/screenshots. Offers/contracts may be
outside this repository. For this project, Movanorm contracts are in
`../felsenhofstrasse/werkvertraege/` relative to the MAIN checkout, not a temporary
worktree. Resolve sibling paths from the real main project directory. User's
spoken/typed path may contain a typo; inspect nearby directory names.

Useful contract pairs (TXT searchable, PDF authoritative for layout):

- `Werkvertrag_2580_Movanorm_Haus Nord`: kitchen, Hana4366SPMBL-FOR fronts.
- `Werkvertrag_2731_Movanorm_Haus Nord`: entrance joinery, same Hana finish.

A kitchen quote from another supplier/earlier design is not evidence for the
selected kitchen. Check house, commission and drawing revision. Signed product
specifications can supersede an architectural colour note; site measurements can
supersede plan dimensions. Record the discrepancy instead of erasing provenance.

Before declaring supplier profiles or handedness unavailable, inspect the
relevant `Werkpläne Unternehmer` folder as well as the architect details and
contract. Image-only PDFs return no searchable text. The approved 25 February
von Euw sheets, for example, resolve RAL 7016 and exact window dimensions after
the older contract's RAL 9007; `./q plans window-supplier` locates them. Check
the visible approval/date and position, not just the filename or OCR result.

## Read dimensions like a surveyor

Identify both reference edges of every dimension. Distinguish clear opening,
casing outer edge, leaf, finished wall, structure and nominal unit size. Write out
chains to detect missing jambs/gaps. The inside finish line and outside roof
surface are not interchangeable references.

Use vector extraction with PyMuPDF `get_drawings()` and confirm the printed scale
against a known dimension. The AF_GRU_50 A0 originals are1:50, approximately
56.69PDFpt/m. That is NOT a universal scale for screenshots/resized PDFs. Do not
fit a coordinate system from eyeballed pixels when vector coordinates exist.

For a complex PDF, locate labels with `page.search_for()` / text extraction and
render only the relevant rectangle with `get_pixmap(clip=...)`. The interior
material concept stalls full-page rasterization in both MuPDF and Poppler, while
the window-finish label crop renders immediately. Avoid repeatedly launching
whole-page previews of the same problematic document.

Owner measurements that disagree with the model trigger source reinspection.
Keep raw measurements, explicit interpretations and resulting geometry separate.
An inferred window width is not an independently measured width. If switch/socket
coordinates need provisional coordination after a measured door moves, preserve
the electrical-plan point and label the adjustment; do not call it as-built.

## Coordinates and current roof/floor facts

x=east, y=north. Site z is relative to462.00m above sea level. Storey furniture z
is relative to that storey's finished floor; DG is absolute+5.700m. Roof/profile
wall profiles can be absolute z: inspect the schema before adding the datum.
`at` is the imported mesh origin, not automatically its centre: inspect the mesh
bounds/origin convention and apply rotation/scale before testing placement.

Do not copy a knee height from an old skill, render or summary. Read:

- `data/dg_site_measurements.json`, `src/dg_site_geometry.py` for current survey.
- `data/dg_section_geometry.json` and `docs/dg-section-heights.md` for sections.
- `src/check_dg_site_measurements.py`, `src/check_dg_floor_build_up.py` for actual
  IFC checks and retained datums.

As clarified19September2026: the measured77cm knee is SOUTHEAST ONLY;78cm was a
typo. Northeast remains unmeasured, section-derived (~73.086cm knee). The former
63cm northeast window sill was an old model value, not an owner measurement;
the opening audit now uses the Type 1 detail's inside finished levels. Read the
current window data and distinguish raw, interior and exterior boundaries.
Do not apply one room's survey to every attic room. The38.7cm window gap is on the southeast room's north
side. Preserve supplied approximate values as approximate.

Adding floor thickness means placing the concrete slab, build-up and hardwood
below the established finished floor, not raising the room floor and losing knee
height. Check before/after finished floor, structural slab underside, knee,
window sill and door. Do not conceal architecture errors with floating furniture.
Roof apertures must follow the actual partition coordinates, not an old centreline.
Site grass/terrain must not occupy the house footprint/cellar volume.

## Materials with evidence

The contracted kitchen/wardrobe finish is Formex Hana4366SPM, matched by Formex to
FENIX0792Blu Shaba. The manufacturer's NCS cross-reference S7010-B10G is approximate,
not an exact colourimetric equivalence. `src/joinery_finishes.py` stores the shared
finish and digital swatch source. Reuse one definition, retaining specified oak
niches and countertops. Do not darken arbitrary RGB values by eye when a swatch is
available. A sampled screen swatch is still only a display approximation.

Entrance dusty rose is an owner colour direction unless an exact RAL/NCS source
is provided. Stair/landing RAL7021 does not extend to outdoor metalwork.
Bathroom privacy glazing applies to actual bathroom windows, not every north
window. Determine which room each opening serves; signed wall directions matter
for `offset_along_wall`. Frosted glazing must remain obscuring in the live viewer
and renders. A renderer that blindly converts every window material to clear
transmission can undo the source change. Distinguish visual approximation from a
verified glass assembly, light transmission or privacy specification.

## Openings in the storey JSON

`offset_along_wall` of windows and doors counts from the wall's `start` point, not
from the model origin (W_WEST starts at y 1.61, so WIN_OG_W_01 at offset 2.351 is at
y 3.961..5.251). Take opening positions from `plan2d.py` output or the door_swings
entries, never by reading the offset as an absolute coordinate; a wrong window
position produced a false "shelf in front of the window" conflict on 2026-09-19.
