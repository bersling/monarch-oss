"""Split a roof envelope behind its retained finished soffit, in metres."""
import math


def split_lining(profile, thickness):
    """Return lining/core YZ profiles; thickness is normal to the inner slope.

    The first two vertices are the finished underside, followed by the upper
    vertices in reverse Y order. Both end cuts remain vertical. No exterior or
    finished-interior datum moves when a lining is added.
    """
    if len(profile) != 4 or not math.isfinite(thickness) or thickness <= 0:
        raise ValueError('Roof lining needs a quadrilateral and positive thickness')
    a, b, c, d = ([float(v) for v in point] for point in profile)
    if (not all(math.isfinite(v) for p in (a, b, c, d) for v in p)
            or b[0] <= a[0] or abs(c[0] - b[0]) > 1e-7
            or abs(d[0] - a[0]) > 1e-7):
        raise ValueError('Roof lining needs increasing Y and vertical end cuts')
    offset = thickness * math.hypot(1, (b[1] - a[1]) / (b[0] - a[0]))
    aa, bb = [a[0], a[1] + offset], [b[0], b[1] + offset]
    if aa[1] >= d[1] or bb[1] >= c[1]:
        raise ValueError('Roof lining consumes the full roof envelope')
    return [a, b, bb, aa], [aa, bb, c, d]
