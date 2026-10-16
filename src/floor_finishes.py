"""Rectangular finish footprints and recessed slab cells (storey-local Z)."""

def intersection(a, b):
    r = [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]
    return r if r[2]-r[0] > 1e-8 and r[3]-r[1] > 1e-8 else None


def subtract(a, b):
    """Disjoint rectangles for a minus b, with no cracks or overlapping solids."""
    i = intersection(a, b)
    if i is None:
        return [a]
    x0, y0, x1, y1 = a
    u0, v0, u1, v1 = i
    return [r for r in ([x0,y0,u0,y1], [u1,y0,x1,y1],
                       [u0,y0,u1,v0], [u0,v1,u1,y1])
            if r[2]-r[0] > 1e-8 and r[3]-r[1] > 1e-8]


def cut_all(rects, cutters):
    for cutter in cutters:
        rects = [piece for r in rects for piece in subtract(r, cutter)]
    return rects


def slab_cells(slab, floorings):
    """Yield footprint/top cells; finishes replace material inside the assembly."""
    top = slab.get('z_top', 0.)
    remaining = slab.get('rects', [slab.get('rect')])
    cells = []
    if slab.get('recess_finishes'):
        for fl in floorings:
            if abs(fl.get('z_top', .01)-top) > 1e-8:
                continue
            for finish in fl['rects']:
                for r in remaining:
                    i = intersection(r, finish)
                    if i:
                        cells.append((i, top-fl.get('thickness', .01)))
                remaining = cut_all(remaining, [finish])
    return cells + [(r, top) for r in remaining]


def slab_solids(slab, floorings):
    """Yield disjoint footprint/bottom/top cells, preserving stair voids and finishes.

    Underside zones use storey-local elevations. They split only the existing
    slab footprint; they cannot fill an opening or add a second overlapping slab.
    """
    bottom = slab.get('z_top', 0.) - slab['thickness']
    zones = slab.get('underside_zones', [])
    used = []
    for zone in zones:
        for rect in zone['rects']:
            if any(intersection(rect, other) for other in used):
                raise ValueError(f"{slab['id']}: overlapping underside zones")
            used.append(rect)
    for rect, top in slab_cells(slab, floorings):
        remaining = [rect]
        for zone in zones:
            for cutter in zone['rects']:
                for part in remaining:
                    hit = intersection(part, cutter)
                    if hit:
                        if zone['z_bottom'] >= top:
                            raise ValueError(f"{slab['id']}: underside reaches above slab top")
                        yield hit, zone['z_bottom'], top
                remaining = cut_all(remaining, [cutter])
        for part in remaining:
            if bottom >= top:
                raise ValueError(f"{slab['id']}: underside reaches above slab top")
            yield part, bottom, top
