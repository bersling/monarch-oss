"""Shared switch-bank geometry and checks (metres above finished floor)."""
import math

MODULE = 0.06
EDGE = 0.014
DEPTH = 0.012


def size(bank):
    cols = bank["columns"]
    rows = math.ceil(len(bank["controls"]) / cols)
    return cols * MODULE + 2 * EDGE, rows * MODULE + 2 * EDGE


def boxes(bank):
    """Frame and distinct rocker faces, in storey-local coordinates."""
    x, y = bank["at"]
    nx, ny = bank["normal"]
    z = bank["height"]
    w, h = size(bank)

    def box(u0, u1, z0, z1, d0, d1):
        if nx:
            a, b = sorted((x + nx * d0, x + nx * d1))
            return [a, y + u0, z0, b, y + u1, z1]
        a, b = sorted((y + ny * d0, y + ny * d1))
        return [x + u0, a, z0, x + u1, b, z1]

    out = [box(-w / 2, w / 2, z - h / 2, z + h / 2, 0, DEPTH)]
    for i, _ in enumerate(bank["controls"]):
        row, col = divmod(i, bank["columns"])
        u = -w / 2 + EDGE + MODULE * (col + 0.5)
        v = z + h / 2 - EDGE - MODULE * (row + 0.5)
        out.append(box(u - .026, u + .026, v - .026, v + .026, DEPTH, .016))
    return out


def validate(bank, plan, elevation):
    """Check whole plate against its host, openings and sloped wall material."""
    from shapely.geometry import Polygon, box as rect
    errors = []
    x, y = bank["at"]
    nx, ny = bank["normal"]
    w, h = size(bank)
    z = bank["height"]
    if (nx, ny) not in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        return ["normal must be an outward axis unit vector"]
    walls = {v["id"]: v for key in ("exterior_walls", "interior_walls", "profile_walls")
             for v in plan.get(key, [])}
    wall = walls.get(bank["wall"])
    if not wall:
        return ["missing host wall"]
    free = y if nx else x
    if "profile" in wall:
        from profile_wall_faces import fixture_faces
        face = next((f for f in fixture_faces(wall) if f['n'] == (nx, ny)), None)
        if face is None or abs((x if nx else y) - face['off']) > .001:
            errors.append("plate is not on the specified profile wall face")
        if face is not None:
            plate = rect(free - w / 2, elevation + z - h / 2, free + w / 2, elevation + z + h / 2)
            if not Polygon(face['profile']).buffer(.001).covers(plate):
                errors.append("profile wall does not support the whole plate")
    else:
        (x0, y0), (x1, y1) = wall["start"], wall["end"]
        horizontal = abs(y1 - y0) < 1e-6
        t = wall["thickness"] / 2
        if bank.get("face") == "start":
            # Exposed south end of the WC partition: face normal is along the wall.
            if horizontal or (nx, ny) != (0, -1) or abs(y - min(y0, y1)) > .001:
                errors.append("invalid wall end face")
            lo, hi = x0 - t, x0 + t
        else:
            off = y0 + ny * t if horizontal else x0 + nx * t
            if (horizontal and nx) or (not horizontal and ny) or abs((y if horizontal else x) - off) > .001:
                errors.append("plate is not on the specified wall face")
            lo, hi = sorted((x0, x1) if horizontal else (y0, y1))
        if free - w / 2 < lo - .001 or free + w / 2 > hi + .001:
            errors.append("plate extends past wall end")
        if z - h / 2 < 0 or z + h / 2 > wall.get("height", plan["building_dimensions"]["storey_height"]):
            errors.append("plate extends beyond wall height")
        for key in ("windows", "doors", "free_doors"):
            for opening in plan.get(key, []):
                if opening.get("wall_id") != wall["id"]:
                    continue
                base = min(x0, x1) if horizontal else min(y0, y1)
                a = base + opening["offset_along_wall"]
                b = a + opening["width"]
                oz = opening.get("sill_height", 0)
                if free + w / 2 > a and free - w / 2 < b and z + h / 2 > oz and z - h / 2 < oz + opening["height"]:
                    errors.append(f"plate overlaps {opening['id']}")
    for opening in plan.get("windows_abs", []):
        if opening.get("host") != wall["id"]:
            continue
        ox0, oy0, oz0, ox1, oy1, oz1 = opening["box"]
        a, b = (oy0, oy1) if nx else (ox0, ox1)
        if free + w / 2 > a and free - w / 2 < b and elevation + z + h / 2 > oz0 and elevation + z - h / 2 < oz1:
            errors.append(f"plate overlaps {opening['id']}")
    return errors
