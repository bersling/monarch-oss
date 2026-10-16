# Verification, worktrees and live delivery

## Keep parallel work safe

Inspect `git status`, branch and `git worktree list` before editing. Create an
isolated branch/worktree; don't stash another session's changes. If delegation is
authorized, assign distinct ownership or worktrees and integrate serially. Room
variants still share `second_floor.json`, `variants.json`, assets and build output:
separate variant files alone do not prevent integration conflicts.

Use the project's environment (`uv run`, or a worktree symlink to main `.venv`).
`UV_CACHE_DIR=/private/tmp/monarch-uv-cache` avoids a restricted default cache here.
Remove a temporary `.venv` symlink before committing/cleaning a worktree. Git's
shared metadata can live outside the writable root; request tool escalation when
required rather than forcing unsafe filesystem workarounds.

## Build and prove the actual result

Pipeline and tools: `docs/architecture.md`. The fast loop from the worktree:

```sh
./open.sh                                # once: viewer server for this checkout (hot reload)
.venv/bin/python src/gen_kids_dg.py      # only if the room has a generator
# edit data/ ... the server rebuilds on save (~9 s to the new model)
./open.sh --no-browser --wait-build      # returns once built (~9 s); checks continue in the background
.venv/bin/python src/snap_web.py room=KIDS_DG_   # screenshots while the checks run
./open.sh --no-browser --wait            # before landing: blocks until checked; exit 1 on failure
./q show KIDS_DG_BED                     # actual IFC bounds/size; which data/ file defines it
./q at 11.8,3.2 --storey DG              # distance to the nearest wall/furniture per direction
./q clash KIDS_DG_BED                    # real interpenetrations
./check.sh kids                          # just the matching checks (~1-2 s)
./build.sh                               # full build + all checks before landing (~20 s)
```

Measure, don't eyeball: before placing something, `./q at` / `./q near` give the
wall faces and gaps to the millimetre. After placing, `./q clash` and
`./q free <zone>` prove access zones stay clear. Numbers first, pictures second.
`./check.sh` runs every check in one process (`src/run_checks.py --list`); a failed
check invalidates the delivery even if the IFC was generated.

Express room constraints as data in `data/rules/<room>.json` (door-swing and
walkway zones as `clear`, exact catalogue sizes and wall contact as `bbox`, aisles
as `gap`). Rules run in `./check.sh`. Write a new `check_*.py` only for logic the
rule kinds cannot express, and consider adding the rule kind instead.

Check all changed items, not a convenient subset:

- Correct count/IDs; active variant equals saved variant; no stacked alternatives.
- Actual IFC geometry bounds agree with catalogue/layout and stay in the room.
- Doors, drawers, switches, stairs, windows and access reserves work in use.
- Every relevant mesh vertex fits the actual roof, including cushions/canopies.
- New geometry/materials retain required metadata and fixed architectural datums.

Read global warnings. Existing unrelated warnings may remain; new door/access
warnings for your objects must be resolved or explicitly tied to an owner decision.
Avoid silently including unrelated regenerated reports in a furnishing commit.

## Visual review is part of implementation

Screenshot the ROOM from the actual built model with the browser viewer's renderer,
the same one the owner sees:

```sh
.venv/bin/python src/snap_web.py room=KIDS_DG_ plan=DG detail=11.2,2.6,7.1,12.3,3.8,6.2
```

Use an interior viewpoint and, where helpful, a cut or `plan=` view. An attractive
object close-up does not prove a usable room. Inspect the PNGs with an image tool
and compare them to the user's reference before reporting success. The viewer
converts sRGB colours to linear, so colours are faithful; a washed-out image points
to a bug, not to the source colour.

If a change is invisible, run `./q show ID` to check it exists with the bounds you
expect, then check the active variant, before editing more geometry. Cycles
photoreal renders (`src/render_*.py`, Blender) remain for presentation; label any
presentation-only recolouring and never move furniture for a render.

Batch independent reads/checks; keep dependent regeneration/build/export sequential.
Render targeted views after a small change rather than the entire house repeatedly.
Once relevant checks pass, repeat only for changed inputs or unresolved concerns.

## Merge deliberately

`src/land.sh` currently assumes `.claude/worktrees/<branch>`, stages selected
folders, deletes `snaps`, and hardcodes an author trailer. Inspect it before use;
it is not the right tool for an arbitrary `/private/tmp` worktree or docs/renders
changes. A manual equivalent is appropriate: stage only intended paths, review
the staged diff, commit with accurate authorship, merge FROM THE MAIN CHECKOUT,
verify the branch tip is an ancestor, then remove the clean worktree/branch.
Never merge from inside the feature worktree and mistake that for integration.
Never erase unrelated main changes or untracked plans. Keep generated IFC files
out of commits unless repository policy specifically tracks them.

## Show the result to the owner

The browser viewer hot-reloads by itself: an open tab updates within seconds of a
save, keeping camera and selection. After landing on main, run
`./open.sh --no-browser --wait` FROM MAIN. It reuses main's viewer, or starts it,
and returns once main's model and checks are current. It opens a tab only if none
is connected. Never automate keyboard or mouse input on the owner's desktop.

The Blender/Bonsai viewer is legacy (`./open.sh --blender`, docs/viewer-blender.md);
use it only when explicitly asked.

## Handoff and concise completion

For an interrupted batch, write `docs/furnishing-handoff.md`: date, branch/main
HEAD, worktree, user intent, completed vs unverified changes, exact next commands,
known warnings, generated files and unrelated dirty paths. Keep durable lessons
in the skill and changing task state in the handoff. Do not label partial work done.

On completion state what changed, what was verified, whether merged/reloaded, and
any material approximation; link room renders/product list instead of dumping
logs. Source uncertainty (e.g. photo-estimated furniture size) should be explicit.

With IfcOpenShell's Python bindings, keep the `create_shape(...)` result alive
while reading `shape.geometry.materials` or vertices. Chaining
`create_shape(...).geometry.materials` can leave SWIG views referring to freed
geometry and produce misleading empty materials or invalid values.
