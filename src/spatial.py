"""Exact-enough spatial tests on the tessellated model (numpy, no IFC kernel).

Used by the query CLI (q.py) and the declarative rules (rules.py). All inputs are
IFC world coordinates in metres; boxes are [x0, y0, z0, x1, y1, z1].
"""
import numpy as np

AXES = {'x': 0, 'y': 1, 'z': 2}
FACES = {'xmin': 0, 'ymin': 1, 'zmin': 2, 'xmax': 3, 'ymax': 4, 'zmax': 5}


def overlap(a, b, eps=0.0):
    """Overlap extents of two boxes per axis (negative = gap)."""
    a, b = np.asarray(a), np.asarray(b)
    return np.minimum(a[3:], b[3:]) - np.maximum(a[:3], b[:3]) - eps


def boxes_intersect(a, b, eps=1e-6):
    return bool((overlap(a, b) > eps).all())


def gap(a, b):
    """Clear distance between two boxes (0 if they touch or overlap)."""
    d = np.maximum(0.0, -overlap(a, b))
    return float(np.linalg.norm(d))


def tris_box(verts, faces, box, eps=1e-6):
    """Boolean per triangle: does it intersect the box interior (shrunk by eps)?

    Separating-axis test (Akenine-Moeller), vectorised over all triangles.
    """
    box = np.asarray(box, float)
    c = (box[:3] + box[3:]) / 2
    h = (box[3:] - box[:3]) / 2 - eps
    if (h <= 0).any() or not len(faces):
        return np.zeros(len(faces), bool)
    t = verts[faces] - c                       # (n, 3, 3)
    v0, v1, v2 = t[:, 0], t[:, 1], t[:, 2]
    ok = np.ones(len(faces), bool)
    # 1) box face normals
    ok &= (np.min(t, axis=1) <= h).all(1) & (np.max(t, axis=1) >= -h).all(1)
    e = [v1 - v0, v2 - v1, v0 - v2]
    # 2) triangle normal
    n = np.cross(e[0], e[1])
    r = (np.abs(n) * h).sum(1)
    d = np.einsum('ij,ij->i', n, v0)
    ok &= np.abs(d) <= r
    # 3) 9 cross axes
    for edge in e:
        for k in range(3):
            unit = np.zeros(3)
            unit[k] = 1
            a = np.cross(unit, edge)
            p = np.stack([np.einsum('ij,ij->i', a, v) for v in (v0, v1, v2)], 1)
            rr = (np.abs(a) * h).sum(1)
            ok &= ~((p.min(1) > rr) | (p.max(1) < -rr))
    return ok


def points_in_box(points, box, eps=1e-6):
    box = np.asarray(box)
    return ((points > box[:3] + eps) & (points < box[3:] - eps)).all(1)


def points_in_mesh(points, verts, faces):
    """Ray-parity point-in-closed-mesh test (ray along +x with a tilt to dodge edges)."""
    if not len(points) or not len(faces):
        return np.zeros(len(points), bool)
    direction = np.array([1.0, 1.3e-3, 2.7e-3])
    direction /= np.linalg.norm(direction)
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    e1, e2 = b - a, c - a
    p = np.cross(direction, e2)
    det = np.einsum('ij,ij->i', e1, p)
    good = np.abs(det) > 1e-14
    a, e1, e2, p, det = a[good], e1[good], e2[good], p[good], det[good]
    inv = 1.0 / det
    inside = np.zeros(len(points), bool)
    for start in range(0, len(points), 2048):
        chunk = points[start:start + 2048][:, None, :]          # (P, 1, 3)
        s = chunk - a[None]                                      # (P, T, 3)
        u = np.einsum('ptk,tk->pt', s, p) * inv
        q = np.cross(s, e1[None])
        v = np.einsum('k,ptk->pt', direction, q) * inv
        t = np.einsum('ptk,tk->pt', q, e2) * inv
        hits = (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)
        inside[start:start + 2048] = hits.sum(1) % 2 == 1
    return inside


def mesh_enters_box(verts, faces, box, eps=1e-6):
    """Test surfaces and volume, including a zone fully enclosed by a solid.

    A closed mesh can enclose every point of a clearance zone without a single
    triangle or mesh vertex entering it. If no surface crosses the zone, its
    centre distinguishes solid volume from an empty cavity.
    """
    box = np.asarray(box, float)
    if (box[3:] - box[:3] <= 2 * eps).any() or not len(verts):
        return False
    if not boxes_intersect(bounds((verts, faces)), box, eps):
        return False
    if tris_box(verts, faces, box, eps).any() or points_in_box(verts, box, eps).any():
        return True
    return bool(points_in_mesh(((box[:3] + box[3:]) / 2)[None], verts, faces)[0])


def depth_inside(points, verts, faces):
    """Distance of (inside) points to the nearest face plane: penetration depth for
    convex solids, an underestimate for concave ones (conservative)."""
    a = verts[faces[:, 0]]
    n = np.cross(verts[faces[:, 1]] - a, verts[faces[:, 2]] - a)
    length = np.linalg.norm(n, axis=1)
    keep = length > 1e-12
    n, a = n[keep] / length[keep, None], a[keep]
    return np.abs(np.einsum('ptk,tk->pt', points[:, None, :] - a[None], n)).min(1)


def edges(faces):
    e = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    return np.unique(e, axis=0)


def segment_crossings(p0, p1, verts, faces):
    """(E, T) parameters t in (0, 1) where segments p0->p1 cross triangles (nan = no crossing).
    Segments lying in a triangle's plane never count, so flush faces are not a crossing."""
    d = p1 - p0
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    e1, e2 = b - a, c - a
    pv = np.cross(d[:, None, :], e2[None])                          # (E, T, 3)
    det = np.einsum('etk,tk->et', pv, e1)
    ok = np.abs(det) > 1e-12 * (np.linalg.norm(d, axis=1)[:, None] * np.linalg.norm(np.cross(e1, e2), axis=1)[None] + 1e-30)
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    sv = p0[:, None, :] - a[None]
    u = np.einsum('etk,etk->et', sv, pv) * inv
    q = np.cross(sv, e1[None])
    v = np.einsum('ek,etk->et', d, q) * inv
    t = np.einsum('etk,tk->et', q, e2) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-9) & (t < 1 - 1e-9)
    return np.where(hit, t, np.nan)


def edge_depth(verts_p, faces_p, verts_q, faces_q, region):
    """Deepest point inside closed solid Q of any of P's edges (midpoints of the edge
    stretches between crossings). Catches a box pushed right through a thin wall,
    where no vertex of either solid lies inside the other."""
    e = edges(faces_p)
    p0, p1 = verts_p[e[:, 0]], verts_p[e[:, 1]]
    lo, hi = np.minimum(p0, p1), np.maximum(p0, p1)
    keep = ((hi >= region[:3]) & (lo <= region[3:])).all(1)
    p0, p1 = p0[keep], p1[keep]
    tri = verts_q[faces_q]
    tkeep = ((tri.max(1) >= region[:3]) & (tri.min(1) <= region[3:])).all(1)
    if not len(p0) or not tkeep.any():
        return 0.0
    worst = 0.0
    for start in range(0, len(p0), 512):
        a, b = p0[start:start + 512], p1[start:start + 512]
        t = segment_crossings(a, b, verts_q, faces_q[tkeep])
        crossing = ~np.isnan(t).all(1)
        if not crossing.any():
            continue
        a, b, t = a[crossing], b[crossing], t[crossing]
        mids = []
        for i in range(len(a)):
            marks = np.concatenate([[0.0], np.unique(np.round(t[i][~np.isnan(t[i])], 12)), [1.0]])
            for k in range(len(marks) - 1):
                m = (marks[k] + marks[k + 1]) / 2
                mids.append(a[i] + m * (b[i] - a[i]))
        mids = np.array(mids)
        # Midpoint classification, not parity: an edge lying in a flush face is on the
        # boundary (depth ~0) and never counts.
        inside = points_in_mesh(mids, verts_q, faces_q)
        if inside.any():
            worst = max(worst, float(depth_inside(mids[inside], verts_q, faces_q).max()))
    return worst


def sample(verts, faces):
    """Vertices plus triangle centroids."""
    return np.vstack([verts, verts[faces].mean(1)]) if len(faces) else verts


def bounds(mesh):
    return np.concatenate([mesh[0].min(0), mesh[0].max(0)])


def penetration(mesh_a, mesh_b, box_a=None, box_b=None, tolerance=0.005):
    """How deep solids A and B interpenetrate (m), 0 if within tolerance.

    Depth = distance to the nearest face plane of the deepest vertex, centroid or
    edge point of one solid inside the other (exact for convex solids, conservative
    for concave ones).
    """
    box_a = bounds(mesh_a) if box_a is None else np.asarray(box_a, float)
    box_b = bounds(mesh_b) if box_b is None else np.asarray(box_b, float)
    if not boxes_intersect(box_a, box_b, tolerance):
        return 0.0
    region = np.concatenate([np.maximum(box_a[:3], box_b[:3]) - 1e-9, np.minimum(box_a[3:], box_b[3:]) + 1e-9])
    worst = 0.0
    for (vp, fp), (vs, fs) in ((mesh_a, mesh_b), (mesh_b, mesh_a)):
        pts = sample(vp, fp)
        pts = pts[((pts >= region[:3]) & (pts <= region[3:])).all(1)]
        if len(pts):
            inside = points_in_mesh(pts, vs, fs)
            if inside.any():
                worst = max(worst, float(depth_inside(pts[inside], vs, fs).max()))
        worst = max(worst, edge_depth(vp, fp, vs, fs, region))
    return worst if worst > tolerance else 0.0


def ray_hit(origin, direction, verts, faces):
    """Distance along a unit ray to the first triangle it hits (inf if none)."""
    if not len(faces):
        return np.inf
    o, d = np.asarray(origin, float), np.asarray(direction, float)
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    e1, e2 = b - a, c - a
    p = np.cross(d, e2)
    det = np.einsum('ij,ij->i', e1, p)
    ok = np.abs(det) > 1e-14
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = o - a
    u = np.einsum('ij,ij->i', s, p) * inv
    q = np.cross(s, e1)
    v = (q @ d) * inv
    t = np.einsum('ij,ij->i', q, e2) * inv
    hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 1e-9)
    return float(t[hit].min()) if hit.any() else np.inf
