"""2D furnishing sketch of one room, straight from the storey JSON -- the "think in
2D first" step before any 3D is built.

    uv run python src/plan2d.py <storey> <x0,y0,x1,y1> <out.png> [concept.json] [--no-furniture]

--clean drops sockets, switches and headroom lines. Storey accepts a code from data/house.json or a storey JSON basename.

Draws, in model coordinates (x east, y north), everything that constrains a layout:
walls (exterior/interior/profile), windows and roof windows, door swings, sockets
with their ids, the roof-headroom contours 1.0 / 1.5 / 2.0 / 2.2 m (attic storeys),
and the footprints of the furniture already in the JSON. A concept file adds
labelled rectangles/circles on top:

    [{"label": "Bed 210x110", "box": [9.25, 5.645, 11.35, 6.745], "rgb": [0.8, 0.6, 0.4]},
     {"label": "Rug d160", "circle": [11.0, 7.65, 0.8]}]

Workflow: sketch the concept here, look at the PNG, move rectangles until the plan
reads right (walkways, door swing, headroom, sockets), THEN write the 3D entries.
Rendering the same room again without a concept file shows the built result in the
same drawing, so intent and result can be compared side by side.
"""
import json
import math
import pathlib
import sys

import pymupdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from check_model import underside, bodyboxes, furniture_prisms, prism_bounds  # noqa: E402
from house import HOUSE  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
K = 120.0            # pt per metre
M = 40.0             # margin in pt
GREY = (0.45, 0.45, 0.45)
WALL = (0.25, 0.25, 0.25)
BLUE = (0.30, 0.55, 0.90)
RED = (0.85, 0.15, 0.15)
GREEN = (0.15, 0.55, 0.25)


def main(argv):
    storey, bbox, out = argv[0], [float(v) for v in argv[1].split(",")], argv[2]
    concept = [a for a in argv[3:] if not a.startswith("--")]
    show_furniture = "--no-furniture" not in argv
    clean = "--clean" in argv      # no sockets, switches or headroom lines (handout plans)
    # --highlight=<json> {"labels": {furniture id: number}, "hide": [id substrings]}: numbered
    # furniture orange with a big number, the rest grey without ids (assembly handout)
    hl = next((json.load(open(a.split("=", 1)[1])) for a in argv if a.startswith("--highlight=")), None)
    from products import load_plan
    plan = load_plan(storey, ROOT)
    elev = HOUSE.elevation[HOUSE.code(storey)]
    X0, Y0, X1, Y1 = bbox
    W, H = (X1 - X0) * K + 2 * M, (Y1 - Y0) * K + 2 * M
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)

    def P(x, y):
        return pymupdf.Point(M + (x - X0) * K, M + (Y1 - y) * K)

    def R(x0, y0, x1, y1):
        return pymupdf.Rect(P(x0, y1), P(x1, y0))

    def text(x, y, s, size=7, color=(0, 0, 0)):
        page.insert_text(P(x, y), s, fontsize=size, color=color, fontname="helv")

    sh = page.new_shape()
    # grid every 0.5 m, labels every metre
    gx = math.ceil(X0 * 2) / 2
    while gx <= X1:
        sh.draw_line(P(gx, Y0), P(gx, Y1))
        gx += 0.5
    gy = math.ceil(Y0 * 2) / 2
    while gy <= Y1:
        sh.draw_line(P(X0, gy), P(X1, gy))
        gy += 0.5
    sh.finish(color=(0.88, 0.88, 0.88), width=0.4)

    # walls
    for w in plan.get("exterior_walls", []) + plan.get("interior_walls", []):
        (ax, ay), (bx, by) = w["start"], w["end"]
        t = w["thickness"] / 2
        if abs(ay - by) < 1e-6:
            sh.draw_rect(R(min(ax, bx), ay - t, max(ax, bx), ay + t))
        else:
            sh.draw_rect(R(ax - t, min(ay, by), ax + t, max(ay, by)))
    for pw in plan.get("profile_walls", []):
        ys = [q[0] for q in pw["profile"]]
        sh.draw_rect(R(pw["x0"], min(ys), pw["x1"], max(ys)))
    sh.finish(color=WALL, fill=(0.75, 0.75, 0.75), width=0.8)

    # windows in walls, absolute windows/doors, roof windows
    walls = {w["id"]: w for k in ("exterior_walls", "interior_walls") for w in plan.get(k, [])}
    assembled_windows = plan.get('windows_abs', []) + [w for w in plan.get('windows', []) if 'parts' in w]
    for o in plan.get("windows", []):
        if 'parts' in o:
            continue
        w = walls[o["wall_id"]]
        (ax, ay), (bx, by) = w["start"], w["end"]
        t = w["thickness"] / 2
        if abs(ay - by) < 1e-6:
            direction = 1 if bx > ax else -1
            a = ax + direction * o["offset_along_wall"]
            b = a + direction * o["width"]
            sh.draw_rect(R(min(a, b), ay - t, max(a, b), ay + t))
        else:
            direction = 1 if by > ay else -1
            a = ay + direction * o["offset_along_wall"]
            b = a + direction * o["width"]
            sh.draw_rect(R(ax - t, min(a, b), ax + t, max(a, b)))
    for wa in assembled_windows:
        if wa.get("kind") == "door":
            continue
        x0, y0, _, x1, y1, _ = wa["box"]
        for a,b,_,c,d,_ in wa.get('opening_boxes', [wa['box']]):
            # Do not draw the void's extra cutting depth beyond the host envelope.
            sh.draw_rect(R(max(x0,a), max(y0,b), min(x1,c), min(y1,d)))
    sh.finish(color=BLUE, fill=(0.80, 0.90, 1.0), width=0.8)
    for wa in assembled_windows:
        for part in wa.get('parts', []):
            for a,b,z0,c,d,z1 in part['boxes']:
                if z0 <= elev+1.2 <= z1:
                    sh.draw_rect(R(a,b,c,d))
            sh.finish(color=BLUE if part.get('glazing') else WALL,
                      fill=part.get('rgb', (0.80,0.90,1.0)), width=0.5)
    for rp in plan.get("roof_planes", []):
        if rp.get("style") == "glass":
            ys = [q[0] for q in rp["profile"]]
            sh.draw_rect(R(rp["x0"], min(ys), rp["x1"], max(ys)))
            text(rp["x0"] + 0.05, min(ys) + 0.08, rp["id"], 6, BLUE)
    sh.finish(color=BLUE, width=0.8, dashes="[4 3] 0")

    # door openings + swings
    for o in plan.get("doors", []):
        w = walls[o["wall_id"]]
        (ax, ay), (bx, by) = w["start"], w["end"]
        t = w["thickness"] / 2
        if abs(ay - by) < 1e-6:
            direction = 1 if bx > ax else -1
            a = ax + direction * o["offset_along_wall"]
            b = a + direction * o["width"]
            sh.draw_rect(R(min(a, b), ay - t, max(a, b), ay + t))
        else:
            direction = 1 if by > ay else -1
            a = ay + direction * o["offset_along_wall"]
            b = a + direction * o["width"]
            sh.draw_rect(R(ax - t, min(a, b), ax + t, max(a, b)))
    for wa in plan.get("windows_abs", []):
        if wa.get("kind") == "door":
            x0, y0, _, x1, y1, _ = wa["box"]
            sh.draw_rect(R(x0, y0, x1, y1))
    sh.finish(color=WALL, fill=(1, 1, 1), width=0.5)
    for sw in plan.get("door_swings", []):
        hx, hy = sw["hinge"]
        cx, cy = sw["closed"]
        ox, oy = sw["open"]
        sh.draw_line(P(hx, hy), P(cx, cy))
        r = math.dist((hx, hy), (cx, cy))
        a0 = math.atan2(cy - hy, cx - hx)
        a1 = math.atan2(oy - hy, ox - hx)
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        pts = [P(hx + r * math.cos(a0 + da * i / 24), hy + r * math.sin(a0 + da * i / 24)) for i in range(25)]
        sh.draw_polyline(pts)
        sh.draw_line(P(hx, hy), P(ox, oy))
    sh.finish(color=GREY, width=0.6, dashes="[3 2] 0")

    # headroom contours (attic)
    if plan.get("roof_planes") and not clean:
        for level, col in ((1.0, (0.9, 0.5, 0.1)), (1.5, (0.9, 0.5, 0.1)), (2.0, (0.9, 0.3, 0.1)), (2.2, (0.9, 0.3, 0.1))):
            pts, n = [], 60
            for i in range(n + 1):
                x = X0 + (X1 - X0) * i / n
                # scan y for the crossing, north half of the storey slopes down northwards and vice versa
                found = None
                prev = None
                for j in range(161):
                    y = Y0 + (Y1 - Y0) * j / 160
                    h = underside(plan, x, y) - elev
                    if prev is not None and (prev[1] - level) * (h - level) < 0:
                        found = prev[0] + (y - prev[0]) * (level - prev[1]) / (h - prev[1])
                        break
                    prev = (y, h)
                if found is not None:
                    pts.append(P(x, found))
                    if i == n // 2:
                        text(x + 0.03, found + 0.03, f"{level:.1f} m i.L.", 6, col)
            if len(pts) > 1:
                sh.draw_polyline(pts)
            sh.finish(color=col, width=0.6, dashes="[6 3] 0")

    # sockets
    for o in [] if clean else plan.get("outlets", []):
        ax, ay = o["at"]
        if not (X0 <= ax <= X1 and Y0 <= ay <= Y1):
            continue
        nx, ny = o.get("normal", (0, 1))
        sh.draw_rect(R(ax - 0.044 - abs(ny) * 0.0, ay - 0.044, ax + 0.044, ay + 0.044))
        sh.draw_line(P(ax, ay), P(ax + nx * 0.15, ay + ny * 0.15))
        text(ax + nx * 0.17 - 0.1 * abs(ny), ay + ny * 0.17 - 0.03 * abs(nx), f"{o['id'][-2:]} {o.get('gangs', 1)}x", 5.5, RED)
    sh.finish(color=RED, fill=(1, 0.85, 0.85), width=0.6)

    # Light switches: bank width follows the actual wall, with a leader into the room.
    from light_switches import size as switch_size
    for switch in [] if clean else plan.get("switches", []):
        ax, ay = switch["at"]
        if not (X0 <= ax <= X1 and Y0 <= ay <= Y1):
            continue
        nx, ny = switch["normal"]
        width, _ = switch_size(switch)
        sh.draw_line(P(ax - abs(ny) * width / 2, ay - abs(nx) * width / 2),
                     P(ax + abs(ny) * width / 2, ay + abs(nx) * width / 2))
        sh.draw_line(P(ax, ay), P(ax + nx * .24, ay + ny * .24))
        label = '/'.join(str(c.get('circuit', '')) for c in switch.get('controls', []))
        text(ax + nx * .26, ay + ny * .26, f"S{switch['id'][-2:]} {label}", 5.5, (.65, 0, .65))
    sh.finish(color=(.65, 0, .65), width=1.5)

    # existing furniture footprints
    hl_text = []
    if show_furniture:
        furn = plan.get("furniture", []) + plan.get("sanitary", [])
        if hl is not None:          # numbered pieces last, so grey neighbours never cover them
            furn.sort(key=lambda f: f["id"] in hl["labels"])
        for f in furn:
            if hl is not None and (any(t in f["id"] for t in hl.get("hide", []))
                                   or not f["id"].startswith(hl.get("only", ""))):
                continue
            drawn = []
            bxs = bodyboxes(f)
            cyls = (f.get("cylinders") or []) + [c for p in f.get("parts", []) for c in p.get("cylinders", [])]
            drew = False
            for b in (f.get("boxes") or []) + [b for p in f.get("parts", []) for b in p.get("boxes", [])] + [prism_bounds(p) for p in furniture_prisms(f)]:
                if b[3] < X0 or b[0] > X1 or b[4] < Y0 or b[1] > Y1:
                    continue
                sh.draw_rect(R(b[0], b[1], b[3], b[4]))
                drawn.append(b)
                drew = True
            for cx, cy, _, r, _ in cyls:      # round things stay round (rug, lamp, legs)
                if X0 <= cx <= X1 and Y0 <= cy <= Y1:
                    sh.draw_circle(P(cx, cy), r * K)
                    drew = True
            if f.get("mesh") and f.get("at"):
                ax, ay = f["at"][0], f["at"][1]
                if X0 <= ax <= X1 and Y0 <= ay <= Y1:
                    if f.get("mesh_bounds"):
                        x0, y0, _, x1, y1, _ = f["mesh_bounds"]
                        sh.draw_rect(R(x0, y0, x1, y1))
                    else:
                        sh.draw_circle(P(ax, ay), 0.25 * K)
                    drew = True
            if drew and hl is None:
                xs = [b[0] for b in bxs] or [f["at"][0]]
                ys = [b[4] for b in bxs] or [f["at"][1]]
                text(min(xs) + 0.03, max(ys) - 0.1, f["id"], 5.5, (0.2, 0.2, 0.6))
            if hl is not None:
                n = hl["labels"].get(f["id"])      # "" = orange without a number
                if n and drawn:
                    big = max(drawn, key=lambda b: (b[3] - b[0]) * (b[4] - b[1]))
                    hl_text.append((n, ((big[0] + big[3]) / 2, (big[1] + big[4]) / 2),
                                    [min(b[0] for b in drawn), min(b[1] for b in drawn),
                                     max(b[3] for b in drawn), max(b[4] for b in drawn)]))
                fill = tuple(hl.get("colors", {}).get(f["id"], (0.95, 0.6, 0.25)))
                sh.finish(color=(0.15, 0.15, 0.15), fill=fill if n is not None else (0.88, 0.88, 0.88),
                          width=0.5, fill_opacity=0.9 if n is not None else 0.6)
        if hl is None:
            sh.finish(color=(0.2, 0.2, 0.6), fill=(0.85, 0.85, 0.95), width=0.6, fill_opacity=0.5)

    # concept overlay (labels go on top after the shape commit, else fills hide them)
    labels = []
    for cpath in concept:
        for it in json.load(open(cpath)):
            rgb = tuple(it.get("rgb", (0.85, 0.6, 0.3)))
            if "box" in it:
                x0, y0, x1, y1 = it["box"]
                sh.draw_rect(R(x0, y0, x1, y1))
                lx, ly = x0 + 0.04, y1 - 0.11
            else:
                cx, cy, r = it["circle"]
                sh.draw_circle(P(cx, cy), r * K)
                lx, ly = cx - r * 0.7, cy
            sh.finish(color=(0.1, 0.1, 0.1), fill=rgb, width=0.8, fill_opacity=0.55)
            size = it.get("label_size", 6.5)
            if it.get("label_center") and "box" in it:
                lx = (x0 + x1) / 2 - len(it.get("label", "")) * size * 0.28 / K
                ly = (y0 + y1) / 2 - size * 0.35 / K
            labels.append((lx, ly, it.get("label", ""), size))
    sh.commit()
    for lx, ly, s, size in labels:
        text(lx, ly, s, size)
    r = (hl or {}).get("disc", 11)
    # one disc per group of touching pieces with the same number (a row of drawers gets one "5"):
    # a lone piece keeps the centre of its largest part (an L-shaped unit), a group its middle
    groups = []
    for n, centre, ext in hl_text:
        g = next((g for g in groups if g[0] == n and ext[0] < g[2][2] + .3 and g[2][0] < ext[2] + .3
                  and ext[1] < g[2][3] + .3 and g[2][1] < ext[3] + .3), None)
        if g is None:
            groups.append([n, centre, list(ext), 1])
        else:
            g[2] = [min(g[2][0], ext[0]), min(g[2][1], ext[1]), max(g[2][2], ext[2]), max(g[2][3], ext[3])]
            g[3] += 1
    hl_text = [(c[0], c[1], n) if k == 1 else ((e[0] + e[2]) / 2, (e[1] + e[3]) / 2, n) for n, c, e, k in groups]
    for cx, cy, s in hl_text:      # white disc with the number, readable on any fill
        page.draw_circle(P(cx, cy), r, color=(0.15, 0.15, 0.15), fill=(1, 1, 1), width=r / 12)
        fs = 1.36 * r
        fs *= min(1.0, 1.55 * r / pymupdf.get_text_length(s, fontname="hebo", fontsize=fs))   # "12*" fits
        tw = pymupdf.get_text_length(s, fontname="hebo", fontsize=fs)
        page.insert_text(P(cx, cy) + (-tw / 2, 0.36 * fs), s, fontsize=fs, fontname="hebo")
    for rx, ry, s, dark in (hl or {}).get("rooms", []):      # room codes for orientation
        size = (hl or {}).get("room_size", 2.6 * r)
        page.insert_text(P(rx, ry) + (-0.3 * size * len(s), 0.35 * size), s, fontsize=size, fontname="hebo",
                         color=(0.1, 0.1, 0.1) if dark else (0.6, 0.6, 0.6))
    for gx in range(math.ceil(X0), math.floor(X1) + 1):
        text(gx - 0.08, Y0 - 0.12, f"{gx}", 7, GREY)
    for gy in range(math.ceil(Y0), math.floor(Y1) + 1):
        text(X0 - 0.30, gy - 0.03, f"{gy}", 7, GREY)
    if not clean:
        page.insert_text((6, 12), f"{storey}  x {X0}..{X1}  y {Y0}..{Y1}   (grid 0.5 m)", fontsize=7, color=GREY)
    pix = page.get_pixmap(dpi=144)
    pix.save(out)
    print(f"wrote {out} ({pix.width}x{pix.height})")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    main(sys.argv[1:])
