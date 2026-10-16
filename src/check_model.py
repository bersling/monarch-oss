"""Invariants over data/*.json. Run by build.sh -- a failing check fails the build.

Prose rules in a skill file are advisory and get skipped; these are enforced:

  IDS       every id is unique within a storey (across all sections)
  REFS      wall_id / host / door / cuts / interior_finish / mesh / texture references resolve
  MOUNTED   outlets and switches sit on an existing wall face, not in an opening or mid-air
  SWING     furniture inside a door's swing arc (warn; fail below 60 degrees of opening)
  HEADROOM  under a roof, a cupboard must fit below the slope where it stands
  ACCESS    the standing space in front of a cupboard (`access`) must stay empty (warn)
  DIMS      geometry must match the Dimensions the datasheet claims (cm x cm x cm)
  META      furniture props should carry Product, Dimensions and a source (warn)

When a new failure mode turns up, add a check here or a rule in data/rules/, not a
paragraph to the skill.

    uv run python src/check_model.py [--warn] [--meta]     # --warn: report, never exit 1
"""
import json, math, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from house import HOUSE  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
STOREYS = {code: HOUSE.elevation[code] for code in HOUSE.codes}
COVER = 0.088
SECTIONS_WITH_IDS = ('exterior_walls', 'interior_walls', 'profile_walls', 'roof_planes', 'windows', 'windows_abs',
                     'doors', 'free_doors', 'slabs', 'floor_build_up', 'stairs', 'railings', 'floorings',
                     'wall_coverings', 'spaces', 'furniture', 'sanitary', 'door_hardware', 'door_swings',
                     'outlets', 'switches')
fails, warns = [], []
def bad(c, m): fails.append(f"{c:8s} {m}")
def warn(c, m): warns.append(f"{c:8s} {m}")


def prism_bounds(p):
    ys, zs = zip(*p["profile"])
    return [p["x0"], min(ys), min(zs), p["x1"], max(ys), max(zs)]


def furniture_prisms(f):
    return f.get("prisms", []) + [p for part in f.get("parts", []) for p in part.get("prisms", [])]


def primitive_bounds(f):
    """Envelopes of boxes, prisms, cylinders, spheres and bounded meshes."""
    return (f.get("boxes") or []) + [prism_bounds(p) for p in f.get("prisms", [])] + ([f["mesh_bounds"]] if f.get("mesh_bounds") else []) + [
        [x - r, y - r, z0, x + r, y + r, z1]
        for x, y, z0, r, z1 in f.get("cylinders", [])
    ] + [
        [x - r, y - r, z - r, x + r, y + r, z + r]
        for x, y, z, r in f.get("spheres", [])
    ]


def allboxes(f):
    return ((f.get("boxes") or [])
            + ([f["mesh_bounds"]] if f.get("mesh_bounds") else [])
            + [b for p in f.get("parts", []) for b in p.get("boxes", [])]
            + [prism_bounds(p) for p in furniture_prisms(f)])


def bodyboxes(f):
    """Bounds excluding protruding handles/knobs, including round furniture legs."""
    return primitive_bounds(f) + [b for p in f.get("parts", [])
                                 if not p.get("protruding") for b in primitive_bounds(p)]


def bbox(boxes):
    xs = [v for b in boxes for v in (b[0], b[3])]
    ys = [v for b in boxes for v in (b[1], b[4])]
    zs = [v for b in boxes for v in (b[2], b[5])]
    return min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)


def wall_index(plan, z):
    """Solid wall faces at height z, with the openings that are NOT solid there."""
    walls = {w["id"]: w for k in ("exterior_walls", "interior_walls") for w in plan.get(k, [])}
    H = plan["building_dimensions"]["storey_height"]
    blocked = {}
    for key in ("windows", "doors", "free_doors"):
        for o in plan.get(key, []):
            w = walls.get(o.get("wall_id"))
            if not w:
                continue
            s = o.get("sill_height", 0.0)
            if not (s - 1e-6 <= z <= s + o["height"] + 1e-6):
                continue
            (x0, y0), (x1, y1) = w["start"], w["end"]
            base = min(x0, x1) if abs(y1 - y0) < 1e-6 else min(y0, y1)
            a = base + o["offset_along_wall"]
            blocked.setdefault(w["id"], []).append((a, a + o["width"]))
    return walls, blocked, H


def check_ids_and_refs(tag, plan, elev):
    seen = {}
    for section in SECTIONS_WITH_IDS:
        for item in plan.get(section, []):
            pid = item.get("id")
            if not pid:
                bad("IDS", f"{tag} {section}: entry without id: {json.dumps(item)[:80]}")
                continue
            if pid in seen:
                bad("IDS", f"{tag} {pid}: defined twice ({seen[pid]} and {section})")
            seen[pid] = section
    walls = {w["id"] for k in ("exterior_walls", "interior_walls", "profile_walls") for w in plan.get(k, [])}
    for key in ("windows", "doors"):
        for o in plan.get(key, []):
            if "parts" in o and key == "windows":
                continue
            if o.get("wall_id") not in walls:
                bad("REFS", f"{tag} {o.get('id')}: wall_id {o.get('wall_id')!r} is not a wall on this storey")
    for wa in plan.get("windows_abs", []) + [w for w in plan.get("windows", []) if "parts" in w]:
        if wa.get("host", wa.get("wall_id")) not in walls:
            bad("REFS", f"{tag} {wa['id']}: host {wa.get('host')!r} is not a wall on this storey")
        for b in [wa["box"], *wa.get("opening_boxes", [])] + [b for p in wa.get("parts", []) for b in p.get("boxes", [])]:
            if not (b[0] < b[3] and b[1] < b[4] and b[2] < b[5]):
                bad("REFS", f"{tag} {wa['id']}: degenerate absolute box {b}")
    doors = {d["id"] for d in plan.get("doors", []) + plan.get("free_doors", [])}
    doors |= {w["id"] for w in plan.get("windows_abs", []) if w.get("kind") == "door"}
    for sw in plan.get("door_swings", []):
        if sw.get("door") not in doors:
            bad("REFS", f"{tag} {sw['id']}: door {sw.get('door')!r} does not exist")
    roofs = {rp["id"]: rp for rp in plan.get("roof_planes", [])}
    for rp in roofs.values():
        if rp.get("cuts") and rp["cuts"] not in roofs:
            bad("REFS", f"{tag} {rp['id']}: cuts {rp['cuts']!r} is not a roof plane")
        if rp.get("interior_finish") and rp["interior_finish"] not in plan.get("roof_finishes", {}):
            bad("REFS", f"{tag} {rp['id']}: interior_finish {rp['interior_finish']!r} is not in roof_finishes")
    for o in plan.get("outlets", []) + plan.get("switches", []):
        if o.get("wall") not in walls:
            bad("REFS", f"{tag} {o['id']}: wall {o.get('wall')!r} does not exist")
    for f in plan.get("furniture", []) + plan.get("sanitary", []) + plan.get("door_hardware", []):
        for node in [f, *f.get("parts", [])]:
            if node.get("mesh") and not (ROOT / node["mesh"]).is_file():
                bad("REFS", f"{tag} {f['id']}: mesh {node['mesh']} not found")
            if node.get("texture") and not (ROOT / node["texture"]["image"]).is_file():
                bad("REFS", f"{tag} {f['id']}: texture image {node['texture']['image']} not found")
        if f.get("access") and f["access"].get("dir") not in ("+x", "-x", "+y", "-y"):
            bad("REFS", f"{tag} {f['id']}: access.dir must be one of +x -x +y -y")
        if not allboxes(f) and not f.get("cylinders") and not f.get("spheres") and not f.get("mesh") \
                and not any(p.get("cylinders") or p.get("spheres") or p.get("mesh") for p in f.get("parts", [])):
            bad("REFS", f"{tag} {f['id']}: no geometry (boxes, cylinders, spheres, prisms or mesh)")
    for b in [b for f in plan.get("furniture", []) for b in allboxes(f)]:
        if not (b[0] < b[3] and b[1] < b[4] and b[2] < b[5]):
            bad("REFS", f"{tag}: degenerate furniture box {b}")


def check_outlets(tag, plan, elev):
    walls, blocked, H = wall_index(plan, 0.30)
    prof = {p["id"]: p for p in plan.get("profile_walls", [])}
    for wa in plan.get("windows_abs", []):
        x0, y0, z0, x1, y1, z1 = wa["box"]
        if z0 - elev - 1e-6 <= 0.30 <= z1 - elev + 1e-6:
            blocked.setdefault(wa["host"], []).append((min(y0, y1), max(y0, y1)))
    for o in plan.get("outlets", []):
        oid, wid = o["id"], o.get("wall")
        if not wid:
            bad("MOUNTED", f"{tag} {oid}: no host wall -- would hang in the air")
            continue
        half = COVER / 2
        ax, ay = o["at"]
        w, pw = walls.get(wid), prof.get(wid)
        if w:
            (x0, y0), (x1, y1) = w["start"], w["end"]
            horiz = abs(y1 - y0) < 1e-6
            free = ax if horiz else ay
            lo, hi = (min(x0, x1), max(x0, x1)) if horiz else (min(y0, y1), max(y0, y1))
            if 0.30 > w.get("height", H):
                bad("MOUNTED", f"{tag} {oid}: wall {wid} is only {w.get('height', H)} m high")
                continue
        elif pw:
            from light_switches import validate
            bank = dict(o, height=o.get('sill', .30), columns=1, controls=[{}])
            for error in validate(bank, plan, elev):
                bad("MOUNTED", f"{tag} {oid}: {error}")
            continue
        else:
            continue            # REFS reported the missing wall
        if not (lo - 0.05 <= free <= hi + 0.05):
            bad("MOUNTED", f"{tag} {oid}: sits {free:.2f} past the end of {wid} ({lo:.2f}..{hi:.2f})")
        for a, b in blocked.get(wid, []):
            if free + half > a and free - half < b:
                bad("MOUNTED", f"{tag} {oid}: cover {free-half:.2f}..{free+half:.2f} overlaps "
                               f"an opening in {wid} at {a:.2f}..{b:.2f}")
                break


def solids_of(plan):
    """Furniture that occupies space. Finish layers flagged "surface" (paint, 2 mm) block nothing."""
    return [f for f in plan.get("furniture", []) if not f.get("surface")]


def check_swings(tag, plan):
    """No furniture inside a door's swing, and the leaf must reach 60 deg (full passage)."""
    furn = [(f["id"], bbox(allboxes(f))) for f in solids_of(plan) if allboxes(f)]
    for sw in plan.get("door_swings", []):
        hx, hy = sw["hinge"]
        cx, cy = sw["closed"]
        ox, oy = sw["open"]
        r = math.dist((hx, hy), (cx, cy))
        a0 = math.atan2(cy - hy, cx - hx)
        a1 = math.atan2(oy - hy, ox - hx)
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        for fid, (x0, y0, z0, x1, y1, z1) in furn:
            if z0 > 2.05:
                continue
            worst = None
            for i in range(0, 101):
                t = i / 100
                ang = a0 + da * t
                dx, dy = math.cos(ang), math.sin(ang)
                for k in range(41):
                    px, py = hx + dx * r * k / 40, hy + dy * r * k / 40
                    if x0 <= px <= x1 and y0 <= py <= y1:
                        worst = abs(da) * t * 180 / math.pi
                        break
                if worst is not None:
                    break
            if worst is not None:
                message = f"{tag} {sw['door']} hits {fid} at {worst:.0f} deg open"
                if worst < 60:
                    bad("SWING", message + " -- PASSAGE BLOCKED, under 60 deg")
                else:
                    warn("SWING", message + " (passage still clear, leaf not flat)")


def underside(plan, x, y):
    """Lowest roof underside over (x, y) -- absolute z. 99 where there is no roof.
    Roof windows (ifc_class IfcWindow) and planes marked "headroom": false do not count."""
    best = 99.0
    for rp in plan.get("roof_planes", []):
        if rp.get("ifc_class") == "IfcWindow" or rp.get("headroom") is False or rp.get("cuts"):
            continue
        if not (rp["x0"] - 1e-9 <= x <= rp["x1"] + 1e-9):
            continue
        poly = rp["profile"]
        zs = []
        for i in range(len(poly)):
            y0, z0 = poly[i]
            y1, z1 = poly[(i + 1) % len(poly)]
            if (y0 > y) != (y1 > y):
                zs.append(z0 + (z1 - z0) * (y - y0) / (y1 - y0))
        if zs:
            best = min(best, min(zs))
    return best


def check_headroom(tag, plan, elev):
    if not plan.get("roof_planes"):
        return
    for f in solids_of(plan):
        prisms = furniture_prisms(f)
        if prisms:
            # A sloping cabinet is not as tall at its rear as its bounding box.
            # Check the actual profile edges and every ordinary solid separately.
            samples = []
            for part in [f] + f.get("parts", []):
                for x0, y0, z0, x1, y1, z1 in primitive_bounds(dict(part, prisms=[])):
                    samples += [(x0+(x1-x0)*i/8, y0+(y1-y0)*j/8, z1)
                                for i in range(9) for j in range(9)]
            for p in prisms:
                profile = p["profile"]
                for (y0,z0),(y1,z1) in zip(profile, profile[1:]+profile[:1]):
                    samples += [(p["x0"]+(p["x1"]-p["x0"])*i/8,
                                 y0+(y1-y0)*j/16, z0+(z1-z0)*j/16)
                                for i in range(9) for j in range(17)]
            gap, x, y = min((underside(plan,x,y)-elev-z,x,y) for x,y,z in samples)
            if gap < -.005:
                bad("HEADROOM", f"{tag} {f['id']}: actual profile penetrates roof by {-gap:.3f} m at ({x:.2f},{y:.2f})")
            continue
        if not allboxes(f):
            continue
        x0, y0, z0, x1, y1, z1 = bbox(allboxes(f))
        if z1 < 0.5:
            continue                       # low stuff never fouls a slope
        worst, at = 99.0, None
        for i in range(9):
            for j in range(9):
                px = x0 + (x1 - x0) * i / 8
                py = y0 + (y1 - y0) * j / 8
                h = underside(plan, px, py) - elev
                if h < worst:
                    worst, at = h, (px, py)
        if worst < z1 - 0.005:
            # A tall picture at one end does not make the entire low shelf tall.
            # Refine only failed union bounds, keeping the common fast path.
            gap, px, py = min(
                (underside(plan, x+(u-x)*i/8, y+(v-y)*j/8)-elev-top,
                 x+(u-x)*i/8, y+(v-y)*j/8)
                for x,y,_,u,v,top in allboxes(f)
                for i in range(9) for j in range(9))
            if gap < -.005:
                bad("HEADROOM", f"{tag} {f['id']}: a component penetrates the roof by {-gap:.3f} m "
                                f"at ({px:.2f},{py:.2f})")


def check_access(tag, plan):
    """Furniture may declare an `access` zone -- the floor you must stand on to use
    it. Nothing else may sit in it. Collision checks alone never catch this."""
    # Per box, not per object bounding box: a step at one end must not count as a
    # blocker of a shelf at the other end. Flat things (rugs) block nothing.
    items = [(f["id"], b) for f in solids_of(plan)
             for b in allboxes(f) if b[5] > 0.20]
    items += [(f["id"], (f["at"][0] - 0.5, f["at"][1] - 0.5, 0.0,
                         f["at"][0] + 0.5, f["at"][1] + 0.5, 1.0))
              for f in solids_of(plan)
              if f.get("mesh") and not allboxes(f)]
    for f in solids_of(plan):
        acc = f.get("access")
        if not acc or not allboxes(f):
            continue
        x0, y0, _, x1, y1, _ = bbox(allboxes(f))
        dep = acc.get("depth", 0.75)
        zone = {"-y": (x0, y0 - dep, x1, y0), "+y": (x0, y1, x1, y1 + dep),
                "-x": (x0 - dep, y0, x0, y1), "+x": (x1, y0, x1 + dep, y1)}[acc["dir"]]
        for oid, (ox0, oy0, _, ox1, oy1, _) in items:
            if oid == f["id"] or (acc.get("ignore") and oid.startswith(acc["ignore"])):
                continue
            if ox1 > zone[0] and ox0 < zone[2] and oy1 > zone[1] and oy0 < zone[3]:
                warn("ACCESS", f"{tag} {oid} stands in the {dep:.2f} m access zone of {f['id']}")


DIM3 = re.compile(r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*cm")


def check_switches(tag, plan, elev):
    from light_switches import boxes, validate
    furniture = [(f["id"], bodyboxes(f)) for f in plan.get("furniture", [])]
    for switch in plan.get("switches", []):
        for error in validate(switch, plan, elev):
            bad("MOUNTED", f"{tag} {switch['id']}: {error}")
        x0, y0, z0, x1, y1, z1 = bbox(boxes(switch))
        nx, ny = switch["normal"]
        # Include 5 cm immediately in front of the plate for hand access.
        x0 += min(nx, 0) * .05; x1 += max(nx, 0) * .05
        y0 += min(ny, 0) * .05; y1 += max(ny, 0) * .05
        for fid, parts in furniture:
            if any(b[3] > x0 and b[0] < x1 and b[4] > y0 and b[1] < y1
                   and b[5] > z0 and b[2] < z1 for b in parts):
                bad("MOUNTED", f"{tag} {switch['id']} sits behind {fid}; switches stay reachable")


def check_dims_and_meta(tag, plan):
    for f in plan.get("furniture", []) + plan.get("sanitary", []):
        if f.get("parts") and not f.get("boxes"):        # flatten parts for the checks
            f = dict(f, boxes=bodyboxes(f), _allparts=f["parts"])
        pr = f.get("props") or {}
        if not pr:
            warn("META", f"{tag} {f['id']}: no props (Product, Dimensions, SourceURL/Plan, Supplier, License)")
            continue
        missing = [k for k in ("Product", "Dimensions") if not pr.get(k)]
        if not (pr.get("SourceURL") or pr.get("Plan") or pr.get("MeshURL")):
            missing.append("SourceURL/Plan")
        if missing:
            warn("META", f"{tag} {f['id']}: props missing {missing}")
        m = DIM3.search(pr.get("Dimensions", ""))
        if m and f.get("boxes"):
            claim = sorted(float(v) / 100 for v in m.groups())
            # An entry may hold several parts ("each 88 x 50 x 13", frame + headboard).
            # The claim must describe the whole thing OR one of its parts.
            cands = [sorted((b[3] - b[0], b[4] - b[1], b[5] - b[2])) for b in f["boxes"]]
            for prt in f.get("_allparts", []):           # a whole part may be the claim
                if prt.get("boxes"):
                    px0, py0, pz0, px1, py1, pz1 = bbox(prt["boxes"])
                    cands.append(sorted((px1 - px0, py1 - py0, pz1 - pz0)))
            x0, y0, z0, x1, y1, z1 = bbox(f["boxes"])
            cands.append(sorted((x1 - x0, y1 - y0, z1 - z0)))
            # Hybrid furniture includes authored meshes (frames, cushions,
            # foliage). Its overall dimensions include both solids and mesh.
            if f.get("mesh") and (ROOT / f["mesh"]).is_file():
                angle = math.radians(f.get("rotation_z", 0))
                c, s = math.cos(angle), math.sin(angle)
                tx, ty, tz = f.get("at", (0, 0, 0))
                scale = f.get("scale", 1)
                vertices = []
                for line in (ROOT / f["mesh"]).read_text().splitlines():
                    if line.startswith("v "):
                        x, y, z = map(float, line.split()[1:4])
                        vertices.append((tx + scale*(c*x-s*y), ty + scale*(s*x+c*y), tz + scale*z))
                if vertices:
                    mb = [min(v[i] for v in vertices) for i in range(3)] + [max(v[i] for v in vertices) for i in range(3)]
                    x0, y0, z0, x1, y1, z1 = bbox(f["boxes"] + [mb])
                    cands.append(sorted((x1-x0, y1-y0, z1-z0)))
            if not any(all(abs(c - g) <= 0.02 for c, g in zip(claim, cand)) for cand in cands):
                bad("DIMS", f"{tag} {f['id']}: Dimensions says {['%.3f'%c for c in claim]} "
                            f"but no part matches (bbox {['%.3f'%g for g in cands[-1]]})")


def main():
    from products import load_plan
    for tag, elev in STOREYS.items():
        plan = load_plan(tag, ROOT)
        check_ids_and_refs(tag, plan, elev)
        check_outlets(tag, plan, elev)
        check_switches(tag, plan, elev)
        check_swings(tag, plan)
        check_headroom(tag, plan, elev)
        check_access(tag, plan)
        check_dims_and_meta(tag, plan)
        for o in plan.get("outlets", []):
            if o.get("gangs", 1) not in (1, 2, 3, 4):
                bad("MOUNTED", f"{tag} {o['id']}: implausible gang count {o.get('gangs')}")
    # A wall of warnings is the same as no warnings: summarise the routine ones,
    # spell out the conflicts.
    meta = [w for w in warns if w.startswith("META")]
    rest = [w for w in warns if not w.startswith("META")]
    for w in rest:
        print(f"  warn  {w}")
    if meta:
        print(f"  warn  META     {len(meta)} elements missing metadata fields "
              f"(run with --meta to list them)")
        if "--meta" in sys.argv:
            for w in meta:
                print(f"          {w}")
    for f in fails:
        print(f"  FAIL  {f}")
    print(f"check_model: {len(fails)} failures, {len(warns)} warnings")
    if fails and "--warn" not in sys.argv:
        sys.exit(1)


if __name__ == "__main__":
    main()
