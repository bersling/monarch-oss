"""Fixture faces of a YZ profile extruded between x0 and x1.

Most profile walls mount fixtures on their X-normal faces. An east-west wall
uses mount_axis='y': its two vertical profile ends carry fixtures. The returned
profile is in each face's free-coordinate/absolute-height plane.
"""


def fixture_faces(wall):
    axis = wall.get('mount_axis', 'x')
    profile = wall['profile']
    if axis == 'x':
        lo, hi = min(p[0] for p in profile), max(p[0] for p in profile)
        return [dict(axis=axis, off=wall[key], n=(sign, 0), lo=lo, hi=hi,
                     profile=profile) for key, sign in (('x0', -1), ('x1', 1))]
    if axis != 'y':
        raise ValueError(f"{wall['id']}: unsupported fixture mount_axis {axis!r}")
    result = []
    for off, sign in ((min(p[0] for p in profile), -1), (max(p[0] for p in profile), 1)):
        heights = [p[1] for p in profile if abs(p[0]-off) < 1e-8]
        if max(heights)-min(heights) < 1e-8:
            continue  # A pointed profile end has no vertical mounting face.
        lo, hi = wall['x0'], wall['x1']
        bottom, top = min(heights), max(heights)
        result.append(dict(axis=axis, off=off, n=(0, sign), lo=lo, hi=hi,
                           profile=[[lo, bottom], [hi, bottom], [hi, top], [lo, top]]))
    return result
