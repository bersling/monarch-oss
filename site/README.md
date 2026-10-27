# Hosted IFC viewer (`site/`)

A static page that opens any `.ifc` in the browser: drop a file (or pick one), it is parsed
client-side with [web-ifc](https://github.com/ThatOpen/engine_web-ifc) (WebAssembly) and drawn
with three.js. Nothing is uploaded; there is no server. The page is deployed to GitHub Pages
together with the built example house, so visitors can click "Load the example house".

## Run locally

```sh
python3 -m http.server 8080 -d site
# then open http://127.0.0.1:8080/ in a browser
```

Any static file server works; `file://` does not (ES modules and wasm need HTTP). To test the
example button locally, copy a built IFC next to the page: `cp generated/casa_monarch.ifc site/`
(do not commit it; `.gitignore` covers `generated/`, and the deploy workflow copies it for you).

## Features

- Drag & drop or file picker; a STEP header check and an error toast for broken files. A failed
  load keeps the current model.
- All geometry via `StreamAllMeshes`, merged per element and colour, IFC colours with
  transparency (glazing). `IfcSpace` volumes are loaded separately and hidden by default (Spaces).
- Click to select: highlight, bounding box, and a panel with Name, type, GlobalId, storey, size and
  every property set (`getPropertySets(recursive)` plus the type object's psets), quantities included.
- Storey chips from `IfcBuildingStorey` containment, walking up `IfcRelContainedInSpatialStructure`
  and `IfcRelAggregates` so parts of assemblies and furniture inside spaces land in their storey.
  Click toggles, Alt-click solos.
- Search by name, type, GlobalId or express id; Enter focuses the hit.
- Image textures (`IfcImageTexture` with an `IfcIndexedTriangleTextureMap`, planar maps) for the
  example house: the files are resolved next to the IFC URL (`textures/…`). A dropped file has no
  folder to resolve them from, so its textured faces keep the style colour.
- Look mode by default: left-drag turns the camera in place, move with `W A S D`, the arrow keys or
  the mouse wheel (towards the cursor), `Q E` down/up, `Shift` faster. A one-card tutorial says so
  after the first load (remembered in `localStorage`). `O` switches to Orbit (left-drag orbits,
  right-drag pans, scroll zooms). `H` home, `F` focus, `Esc` clear. Keys match the local viewer
  (`docs/viewer.md`).
- Cut: a storey and everything below it, clipped at floor + height (default 1.20 m), cut walls
  in a slate section fill (opaque back faces while cutting). Storey floors are
  `IfcBuildingStorey.Elevation` (times the `IfcSIUnit` length prefix), measured in the same frame
  as the geometry; a storey placed with an extra offset would cut at the wrong height.
- Plan (`P` or the button): top-down orthographic view of the cut storey (Ground floor, i.e.
  elevation 0, else the lowest, when no cut is set), implying a cut at floor + 1.20 m. North up:
  IFC +y is taken as north, `IfcGeometricRepresentationContext.TrueNorth` is ignored. Drag pans,
  scroll zooms, `W A S D` pan the map, the Cut menu switches the storey, `P` goes back to 3D
  (the cut stays until Cut is set to off).
- Measure (`M`): click two points. In Measure mode a left-click only places points; hold
  `Space` and drag to look around. The cursor snaps to corners (square), else edges (diamond),
  else the face (ring); `Alt` turns snapping off, `Shift` locks the dominant axis. The result is a
  line with a distance label in the view, dashed x / y / z legs and a box with Δx / Δy / Δz in
  IFC axes to the millimetre. `Esc` clears, a second `Esc` leaves Measure.
- Field of view: Narrow 30°, Normal 45° (default), Wide 60°, Extra wide 75°; disabled in Plan.
- Remove (`X` or the panel button) hides the selected element for this session only (nothing is
  saved; a reload restores it). `Z` undoes the last removal, `Shift+Z` restores all; the hint line
  and the Elements stat show how many are removed.
- Night (`N` or the button): the sun and the day fill go out, a faint cool sky keeps unlit rooms
  readable, and the lamps light the house. Every element with a `Pset_Light` (the example writes it on
  its `IfcLightFixture`s: `LuminousFlux_lm`, `ColourTemperature_K`, `Distribution` point/spot,
  `BeamAngle_deg`, `Direction`, `SourceX/Y/Z`, `EmissiveRGB`) becomes a three.js point or spot
  light, as in the local viewer: candela = lumen / solid angle, colour from Kelvin, 100 lux shown
  as full brightness, plus a soft fill (Bounce, default 35 % of the lumen, ending at 5 m) standing
  in for wall bounce. Parts in the `EmissiveRGB` colours glow. Budget: the lights exist only at
  night, the first 6 spots cast shadows (shadow maps redrawn only on changes), points never do.
  A lamp list (bottom left, foldable) switches each lamp; a selected lamp's panel has Light on/off
  (`L`) and a Light section (lumen, Kelvin, beam) that previews live. The hosted page has no
  server, so these edits last for the session; the local viewer's editor saves them
  (`data/lamp_overrides.json`). Plans always use day light.
- Stats line: elements, triangles, parse time. Works at phone width (the toolbar wraps).

## Deploy

`.github/workflows/pages.yml` runs on every push to `main`: `uv sync`, `./build.sh` (falls back to
`src/build_model.py`; the deploy is never blocked by model checks), copies `site/` to `_site/` and
the IFC named by `ifc_file` in `data/house.json` to `_site/casa_monarch.ifc`, then publishes with
`actions/deploy-pages`. Enable it once under Settings > Pages > Source: GitHub Actions.

The example must be served from `./casa_monarch.ifc` relative to `index.html` (`EXAMPLE_URL` in
`app.js`).

## Vendored libraries (no npm, no bundler)

- `vendor/web-ifc/`: web-ifc **0.0.78**, MPL-2.0. ESM `web-ifc-api.js` + single-thread
  `web-ifc.wasm` (GitHub Pages is not cross-origin isolated, so the multi-threaded build is not
  shipped). Upgrade: `curl -O https://registry.npmjs.org/web-ifc/-/web-ifc-<version>.tgz`,
  `tar -xzf`, copy `web-ifc-api.js`, `web-ifc.wasm`, `LICENSE.md`, update `VERSION.txt`.
- `vendor/three/`: three.js r170 (MIT), the same files as `web/vendor/three`:
  `three.module.js`, `OrbitControls.js`, `BufferGeometryUtils.js`.

The import map in `index.html` maps `three`, `three/addons/` and `web-ifc` to these paths.

## Testing

Headless only: serve `site/` (with a test IFC placed as `casa_monarch.ifc`) and run
`node site/test_site.mjs http://127.0.0.1:PORT/ [shot.png]` (usage at the top of the file). It
drives headless Chrome over the DevTools protocol and covers loading, storeys, selection, search,
remove / undo, FOV, Plan, Measure and the 390 px layout; with a screenshot path it also writes
`shot-plan-gf.png`, `shot-measure.png`, `shot-cut-3d.png` and `shot-phone-390(-plan).png`. The page
exposes `window.__viewerReady` and `window.__viewer` (`state`, `camera`, `loadBuffer`,
`setSelection`, `removeSelected`, `undoRemove`, `setFov`, `setCut`, `enterPlan`, `exitPlan`,
`setMeasure`, `measureClick`, `snapAt`, `drawCalls`, ...) for such scripts.
