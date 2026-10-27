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
- Orbit (left-drag), pan (right-drag), zoom (scroll), fly with `W A S D` + `Q E` (`Shift` faster).
  `O` switches Look mode (left-drag turns the camera in place). `H` home, `F` focus, `Esc` clear.
- Stats line: elements, triangles, parse time. Works at phone width.

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

Headless only: serve `site/` (with a test IFC placed as `casa_monarch.ifc`), launch Chrome with
`--headless=new --remote-debugging-port=0` and drive it over the DevTools protocol from a Node
script (see `tests/browser_viewer.mjs` for the pattern). The page exposes `window.__viewerReady`
and `window.__viewer` (`state`, `loadBuffer`, `setSelection`, `focusOn`, `orbit`, `drawCalls`) for
such scripts.
