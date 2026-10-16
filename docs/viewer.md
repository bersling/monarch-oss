# Browser viewer

```sh
./open.sh                         # start (or reuse) this checkout's viewer and open a tab
./open.sh --no-browser --wait     # agents: block until the model includes every edit so far
                                  # and its checks ran; exit 1 if the build or a check failed
./open.sh --no-browser --wait-build   # return once built; checks keep running in the background
./open.sh --stop                  # stop this checkout's server
./open.sh --blender ...           # legacy Blender/Bonsai viewer (docs/viewer-blender.md)
```

The server (`src/serve.py`) watches `data/`, `assets/` and the builder modules and
rebuilds after every save. The open tab swaps the model in place, keeping camera,
selection, storeys and cut. From saving an edit to the new model in the tab takes
about 9 s (IFC 4.4 s, incremental meshing 3 s). The checks then run in the
background, about 12 s. Each checkout (main or a worktree) gets its own port, so
parallel sessions never share a viewer. A failed build turns the status pill red
(click it for the log) and the last good model stays visible. Checks never run
against a half-written variant: builds, room generation, variant loading and
checks share the `generated/.model.lock` OS lease. `.checks.lock` is the running
checks indicator. Geometry and properties must carry the same IFC digest before
the viewer swaps them; incomplete exports are retried. `--wait` requires successful
validation of the current model and check code, including when reusing a server
started with checks disabled.

The launcher also checks which server code is loaded, rather than trusting a live
port alone. After a code upgrade it replaces the verified checkout-owned server
on the same port. A missing temporary state file is recovered from that port;
concurrent launchers share a startup lock. `./open.sh` enables watching and checks
when reusing a server originally started just for screenshots. Screenshots use
the same discovery/upgrade logic and wait for current geometry before capture.

## Using it

- **Navigate:** left-drag looks around (SketchUp's eye tool: the camera turns in place,
  pitch limited to ±85°). Middle-drag orbits, right-drag pans. `O` (or the Look / Orbit
  toggle) makes left-drag orbit instead; the choice is remembered in this browser. Scroll
  flies towards the cursor at constant speed (no braking near objects; Shift x3). `W/A/S/D`
  or the arrow keys fly in the view direction (A/D strafe level), `Q/E` go down/up, Shift x3. Double-click a surface: in Look
  mode the camera turns to face it, in Orbit mode it becomes the orbit centre. `H` returns
  to the home view. Plan view: every drag pans, W/arrows pan too (W = north).
- **Look:** the default look has sun shadows, soft image-based light, a dark sky and ground
  and a faint glow on the selection. It is calibrated so lit surfaces keep their authored
  colour (tone mapping is PBR Neutral without its toe; an unshaded floor renders within about
  3 ΔE of its authored sRGB, hue within 1°; faces in shadow are darker, never tinted).
  `T` (True colour) switches to flat, even light: no shadows, no bloom, no tone mapping,
  every face within about 1 to 3 ΔE of its authored colour. Use it to judge finishes; the
  panel also lists each element's authored colours as swatches. Plans never draw sun
  shadows. `look=true` in the hash starts in True colour.
- **Storeys:** the UG / EG / OG / DG / SITE buttons toggle storeys; Alt-click shows
  only that storey. Cut shows a storey and everything below it, with a horizontal
  section at the height you set (default floor + 1.20 m). `P` or Plan gives a
  top-down plan, north up.
- **Select:** click an element. The panel shows the product name first, then key,
  class, storey, size in cm, bounds and every property set. Paths to `plans/`,
  `docs/`, `data/`, `assets/`, `renders/` and supported sibling
  `felsenhofstrasse/` source folders open the file. Worktrees resolve sibling paths
  from the main checkout. `F` frames the selection, `Esc`
  clears it.
- **Plans:** the toolbar button lists the pinned architectural and electrical
  PDFs with printed revision dates, scope and verification evidence. The same
  catalogue is available through `./q plans [UG|EG|OG|DG]`. A replaced PDF fails
  source verification until its revision and model implications are reviewed.
- **Search:** press `/` and type part of a key, product name or class.
- **Measure:** press `M` (or the Measure button), then click two points. In Measure mode a
  left-click only places points: it may slide up to 10 px and last up to 400 ms (trackpad
  clicks), and it never turns the camera. To look around while measuring, hold **Space and
  drag** (in plan view: pan). Middle-drag orbit, right-drag pan, scroll-fly and WASD keep
  working. The cursor snaps to corners within about 10 px (square marker), else to edges
  within 7 px (a highlighted piece of the edge and a diamond), else stays on the face under it
  (ring). Only feature edges count, never the diagonals of flat faces; in a cut view, edges
  end at the section plane. **Alt** turns snapping off, **Shift** locks the dominant axis on top
  of the snap. While you pick the second point, a rubber-band line with a live distance
  follows the cursor. The result is an amber line with end ticks and a pill label (`0.86 m`),
  faint dashed x / y / z legs (IFC: x east, y north, z up) labelled where they fit, and the
  part hidden behind geometry drawn faint and dashed. The box at the bottom left gives the
  distance and Δx / Δy / Δz to the millimetre, plus both points. A new click starts a new
  measurement, `Esc` clears it (a second `Esc` leaves Measure), `M` toggles. The cursor's IFC
  coordinate always shows while hovering (while measuring: the snapped point and snap kind).
- **Share a view:** the URL hash holds the view. Copy the URL, or use "Copy link"
  in the panel.

## URL hash (IFC coordinates: x east, y north, z up, metres)

```
#cam=x,y,z&target=x,y,z&storeys=UG,EG&hide=SITE&cut=EG@1.2&plan=EG&span=18&sel=KEY&frame=KEY_or_prefix&edges=0&spaces=1&look=true
```

`frame=UG_GYM_` frames every element whose key starts with that prefix. Parsing is
lenient; the full format is documented at the top of `web/viewer.js`.

## Screenshots for verification (the same renderer the owner sees)

```sh
.venv/bin/python src/snap_web.py ext=-9,-16,12,7,4,1 hide=SITE  eg plan=EG  gym room=UG_GYM_ \
    [--size 1600x1000] [--out DIR] [--urls]
```

A view is `NAME=cam_x,cam_y,cam_z,target_x,target_y,target_z` or a bare `NAME`,
followed by any hash options (`storeys hide cut plan span target sel room edges
spaces`). `plan=EG` and `room=KEY` alone create auto-named views. One headless
Chrome renders all the views, typically 3 views in about 9 s, and the command
prints the PNG paths. Always look at the PNGs.

Snapshots warn when a perspective camera is inside visible opaque geometry,
including the element ID and camera coordinates. The check uses actual mesh
surfaces, respects hidden storeys and section cuts, and ignores clear glazing.
It does not replace inspecting the image: a camera outside solids can still
look at an unhelpful or obstructed target. Fix the position or use an intentional
cut when a warning appears; the requested image is still saved for inspection.

Field of view: the toolbar FOV menu offers Narrow 30°, Normal 45° (default),
Wide 60° and Extra wide 75°. It changes the lens without moving the camera.
The setting stays in copied view links and across reloads (`fov=30|45|60|75`),
and is disabled in the orthographic Plan view. `src/snap_web.py` also accepts `fov=`.
