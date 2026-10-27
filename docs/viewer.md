# Browser viewer

```sh
./open.sh                         # start (or reuse) this checkout's viewer and open a tab
./open.sh --no-browser --wait     # agents: block until the model includes every edit so far
                                  # and its checks ran; exit 1 if the build or a check failed
./open.sh --no-browser --wait-build   # return once built; checks keep running in the background
./open.sh --stop                  # stop this checkout's server
```

The server (`src/serve.py`) watches `data/`, `assets/` and the builder modules and
rebuilds after every save. The open tab swaps the model in place, keeping camera,
selection, storeys and cut. The checks then run in the background. Each checkout (main or
a worktree) gets its own port, so parallel sessions never share a viewer. A failed build
turns the status pill red (click it for the log) and the last good model stays visible.
Builds, placement edits, variant loading and checks share the `generated/.model.lock` OS
lease; `.checks.lock` is the running-checks indicator. Geometry and properties must carry
the same IFC digest before the viewer swaps them. `--wait` requires successful validation
of the current model and check code.

The launcher checks which server code is loaded rather than trusting a live port alone;
after a code upgrade it replaces the checkout-owned server on the same port. Screenshots
use the same discovery and wait for current geometry before capture.

## Using it

- **Navigate:** left-drag looks around (the camera turns in place, pitch limited to
  ±85°). Middle-drag orbits, right-drag pans. `O` (or the Look / Orbit toggle) makes
  left-drag orbit instead; the choice is remembered in this browser. Scroll flies towards
  the cursor at constant speed (Shift x3). `W/A/S/D` or the arrow keys fly in the view
  direction, `Q/E` go down/up. Double-click a surface: in Look mode the camera turns to
  face it, in Orbit mode it becomes the orbit centre. `H` returns to the home view. Plan
  view: every drag pans.
- **Look:** the default look has sun shadows, soft image-based light and a faint glow on
  the selection, calibrated so lit surfaces keep their authored colour. `T` (True colour)
  switches to flat, even light with no shadows or tone mapping, for judging finishes; the
  panel lists each element's authored colours as swatches. `look=true` in the hash starts
  in True colour.
- **Night:** `N` turns the sun off and lights the house with the modelled lamps: every element
  with a `Pset_Light` (a `light` block on the furniture item, see `docs/data-format.md`) becomes a
  point or spot light with its candela derived from the lumen value, its colour from the Kelvin
  value (white-balanced to 3400 K). Exposure: `lux=100` in the hash is the illuminance shown as
  full brightness, so a 400 lm bedside lamp reads bright at 1 m and faint at 2 m. Parts in the
  lamp's `emissive` colours glow. Spots cast shadows (the first 8), points do not. Rooms without
  lamps stay dark; `look=night` in the hash starts at night.
  **Switching lamps:** at night a lamp list (bottom left) shows every lamp on the shown storeys
  with a switch; a switch-group letter (`light.switch`) shared by several lamps flips them together.
  The selected lamp's panel has a Light on/off button (`L`). Off lamps go into the hash as
  `off=KEY,KEY`.
  **Editing a lamp:** the selected lamp's panel has a Light section with lumen, Kelvin and beam
  (all round or spot + angle) that previews live. *Save to model* posts the values to the server,
  which writes `data/lamp_overrides.json`; the watcher rebuilds and the IFC `Pset_Light` carries
  the new values (`Edited: viewer <date>`). *Reset* drops the override. **Bounce:** the lamp list
  has a Bounce slider (`bounce=0.35` in the hash): each lamp also spreads that share of its lumen
  as a soft fill that ends at 5 m, standing in for the light the walls bounce back (the viewer has
  no global illumination). The hosted viewer (`site/`) has the same Night look, switches and a
  session-only editor.
- **Storeys:** one button per storey from `data/house.json` plus SITE; click toggles,
  Alt-click shows only that storey. Cut shows a storey and everything below it, with a
  horizontal section at the height you set (default floor + 1.20 m). `P` or Plan gives a
  top-down plan, north up.
- **Select:** click an element. The panel shows the product name first, then key, class,
  storey, size in cm, bounds and every property set. Paths under `plans/`, `docs/`,
  `data/` and `assets/` open the file (read-only, served from the checkout; worktrees fall
  back to the main checkout's `plans/`). `F` frames the selection, `Esc` clears it.
- **Plaques:** floating labels on every element that has a `Pset_Gallery` property set
  (its `Title`). On by default when the model has such elements, off otherwise; click a
  plaque to select the exhibit. `plaques=0|1` in the hash overrides.
- **Sources:** the toolbar button lists the documents pinned in
  `data/plan_documents.json` (title, printed date, scope, verification evidence) or says
  that none are registered. The same catalogue is `./q sources`. A replaced PDF fails
  source verification until its revision and model implications are reviewed.
- **Search:** press `/` and type part of a key, product name or class.
- **Measure:** press `M`, then click two points. In Measure mode a left-click only places
  points and never turns the camera; hold **Space and drag** to look around. The cursor
  snaps to corners (square marker), else to edges (diamond), else stays on the face (ring);
  **Alt** turns snapping off, **Shift** locks the dominant axis. The result is a line with
  a distance label, dashed x / y / z legs and a box with Δx / Δy / Δz to the millimetre.
  `Esc` clears, a second `Esc` leaves Measure.
- **Share a view:** the URL hash holds the view. Copy the URL, or use "Copy link" in the
  panel.

## URL hash (IFC coordinates: x east, y north, z up, metres)

```
#cam=x,y,z&target=x,y,z&storeys=GF,UF&hide=SITE&cut=GF@1.2&plan=GF&span=18&sel=KEY&frame=KEY_or_prefix&edges=0&spaces=1&plaques=0&look=true&fov=60&lux=100&bounce=0.35&off=KEY
```

`frame=GAL_` frames every element whose key starts with that prefix. Parsing is lenient;
the full format is documented at the top of `web/viewer.js`.

## Screenshots for verification (the same renderer the user sees)

```sh
.venv/bin/python src/snap_web.py ext=-9,-12,9,4,3,2 hide=SITE  gf plan=GF  gallery room=GAL_ \
    [--size 1600x1000] [--out DIR] [--urls]
```

A view is `NAME=cam_x,cam_y,cam_z,target_x,target_y,target_z` or a bare `NAME`, followed
by any hash options (`storeys hide cut plan span target sel room edges spaces fov`).
`plan=GF` and `room=KEY` alone create auto-named views. One headless Chrome renders all
the views and the command prints the PNG paths. Always look at the PNGs.

Snapshots warn when a perspective camera is inside visible opaque geometry, including the
element ID and camera coordinates. The check uses actual mesh surfaces, respects hidden
storeys and section cuts, and ignores clear glazing. It does not replace inspecting the
image: a camera outside solids can still look at an unhelpful target. Fix the position or
use an intentional cut when a warning appears.

Field of view: the toolbar FOV menu offers Narrow 30°, Normal 45° (default), Wide 60° and
Extra wide 75° (`fov=` in the hash and in `snap_web.py`); it is disabled in the
orthographic Plan view.

## Regression test

`node tests/browser_viewer.mjs <URL>` drives headless Chrome over the DevTools protocol
against a running viewer: selection and property panel, image textures, the Sources
dialog, zoom and Look behaviour, measuring, hot reload, depth correctness, camera
obstruction diagnostics, storey buttons from the house index and the plaques.
