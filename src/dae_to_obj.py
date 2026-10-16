"""Minimal COLLADA (.dae) -> OBJ converter, pure stdlib.

Blender 5.x dropped its Collada importer, but Collada is what SketchUp and many
model archives export, so this keeps that source usable:

    uv run python src/dae_to_obj.py ~/Downloads/model.dae /tmp/model.obj
    blender -b --python src/convert_asset.py -- /tmp/model.obj assets/x.obj up=Z fit_x=0.38

Handles: library_geometries (triangles + polylist), the vertices/source
indirection, node hierarchies with <matrix>, instance_geometry,
instance_node into library_nodes, the asset <unit> scale (SketchUp exports
in inches!) and Y_UP -> Z_UP. Geometry only — materials are ignored, since
the build pipeline colors objects per entry.
"""
import sys
import xml.etree.ElementTree as ET

src_path, out_path = sys.argv[1], sys.argv[2]
root = ET.parse(src_path).getroot()
NS = root.tag.split('}')[0] + '}'


def tag(el):
    return el.tag.replace(NS, '')


def find(el, name):
    return el.find(NS + name)


def findall(el, name):
    return el.findall(NS + name)


# --- asset: unit scale + up axis ---
unit_scale, up_axis = 1.0, 'Y_UP'
asset = find(root, 'asset')
if asset is not None:
    u = find(asset, 'unit')
    if u is not None:
        unit_scale = float(u.get('meter', 1.0))
    ua = find(asset, 'up_axis')
    if ua is not None:
        up_axis = ua.text.strip()

# --- geometries: id -> (positions, triangles) ---
geometries = {}
lib_geo = find(root, 'library_geometries')
for geo in (findall(lib_geo, 'geometry') if lib_geo is not None else []):
    mesh = find(geo, 'mesh')
    if mesh is None:
        continue
    sources = {}
    for s in findall(mesh, 'source'):
        fa = find(s, 'float_array')
        if fa is None or not fa.text:
            continue
        acc = find(find(s, 'technique_common'), 'accessor') if find(s, 'technique_common') is not None else None
        stride = int(acc.get('stride', 3)) if acc is not None else 3
        vals = [float(v) for v in fa.text.split()]
        sources['#' + s.get('id')] = [vals[i:i + stride][:3] for i in range(0, len(vals), stride)]
    # <vertices> maps a VERTEX id onto a POSITION source
    verts_map = {}
    for v in findall(mesh, 'vertices'):
        for inp in findall(v, 'input'):
            if inp.get('semantic') == 'POSITION':
                verts_map['#' + v.get('id')] = sources.get(inp.get('source'), [])
    tris = []
    positions = []
    for prim in list(mesh):
        if tag(prim) not in ('triangles', 'polylist', 'polygons'):
            continue
        inputs = findall(prim, 'input')
        stride = max(int(i.get('offset', 0)) for i in inputs) + 1
        voff = next((int(i.get('offset', 0)) for i in inputs if i.get('semantic') == 'VERTEX'), None)
        vsrc = next((i.get('source') for i in inputs if i.get('semantic') == 'VERTEX'), None)
        if voff is None or vsrc not in verts_map:
            continue
        if not positions:
            positions = verts_map[vsrc]
        p_el = find(prim, 'p')
        if p_el is None or not p_el.text:
            continue
        idx = [int(x) for x in p_el.text.split()]
        vidx = idx[voff::stride]
        if tag(prim) == 'triangles':
            for i in range(0, len(vidx) - 2, 3):
                tris.append(vidx[i:i + 3])
        else:  # polylist: <vcount> gives the polygon sizes; fan-triangulate
            vc_el = find(prim, 'vcount')
            counts = [int(c) for c in vc_el.text.split()] if vc_el is not None and vc_el.text else []
            pos = 0
            for c in counts:
                poly = vidx[pos:pos + c]
                for k in range(1, c - 1):
                    tris.append([poly[0], poly[k], poly[k + 1]])
                pos += c
    if positions and tris:
        geometries['#' + geo.get('id')] = (positions, tris)

# --- library_nodes for instance_node indirection ---
lib_nodes = {}
ln = find(root, 'library_nodes')
for n in (findall(ln, 'node') if ln is not None else []):
    lib_nodes['#' + n.get('id')] = n

IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def mat_mul(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def apply(m, p):
    x, y, z = p[0], p[1], p[2]
    return (
        m[0] * x + m[1] * y + m[2] * z + m[3],
        m[4] * x + m[5] * y + m[6] * z + m[7],
        m[8] * x + m[9] * y + m[10] * z + m[11],
    )


chunks = []  # (verts, tris, center) per instanced geometry


EXCLUDE = set()
for a in sys.argv:
    if a.startswith('--exclude='):
        EXCLUDE = {x.strip().lstrip('#') for x in a.split('=', 1)[1].split(',') if x.strip()}


def emit(geo_url, matrix):
    entry = geometries.get(geo_url)
    if not entry or geo_url.lstrip('#') in EXCLUDE:
        return
    positions, tris = entry
    pts = [apply(matrix, p) for p in positions]
    center = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
    chunks.append((pts, tris, center, geo_url.lstrip('#')))


def walk(node, matrix, depth=0):
    if depth > 64:
        return
    m = matrix
    for child in list(node):
        t = tag(child)
        if t == 'matrix' and child.text:
            vals = [float(v) for v in child.text.split()]
            if len(vals) == 16:
                m = mat_mul(m, vals)
        elif t == 'instance_geometry':
            emit(child.get('url'), m)
        elif t == 'instance_node':
            target = lib_nodes.get(child.get('url'))
            if target is not None:
                walk(target, m, depth + 1)
        elif t == 'node':
            walk(child, m, depth + 1)


scene = find(root, 'library_visual_scenes')
for vs in (findall(scene, 'visual_scene') if scene is not None else []):
    walk(vs, IDENTITY)

if not chunks:
    sys.exit('no geometry found in ' + src_path)

# --cluster: keep only the biggest spatial cluster. SketchUp scene
# exports routinely ship the object *plus* the "Susan" scale figure and stray
# helper boxes, parked metres away from the thing you actually want.
if '--cluster' in sys.argv:
    thresh = 1.5 / unit_scale          # link chunks within 1.5 m
    groups = []                        # [[chunk indices], ...]
    for i, (_, _, c, _gid) in enumerate(chunks):
        hit = [g for g in groups
               if any(sum((c[k] - chunks[j][2][k]) ** 2 for k in range(3)) < thresh ** 2 for j in g)]
        if hit:
            hit[0].append(i)
            for extra in hit[1:]:      # merge groups this chunk bridges
                hit[0].extend(extra)
                groups.remove(extra)
        else:
            groups.append([i])
    best = max(groups, key=lambda g: sum(len(chunks[i][0]) for i in g))
    dropped = sum(len(chunks[i][0]) for g in groups if g is not best for i in g)
    if dropped:
        print(f'--cluster: kept {sum(len(chunks[i][0]) for i in best)} verts in the main cluster, '
              f'dropped {dropped} verts in {len(groups)-1} other cluster(s)')
    chunks = [chunks[i] for i in sorted(best)]

if '--list' in sys.argv:
    print('chunks (id, verts, size in m) — pass --exclude=id,id to drop helpers:')
    for pts, tris, c, gid in chunks:
        sz = [(max(p[i] for p in pts) - min(p[i] for p in pts)) * unit_scale for i in range(3)]
        print(f'  {gid:10s} n={len(pts):6d} size=({sz[0]:.2f}, {sz[1]:.2f}, {sz[2]:.2f})')

out_v, out_f = [], []
for pts, tris, _c, _gid in chunks:
    base = len(out_v)
    out_v.extend(pts)
    out_f.extend([[base + i for i in t] for t in tris])

# unit scale, and Y_UP -> Z_UP if needed
conv = []
for x, y, z in out_v:
    x, y, z = x * unit_scale, y * unit_scale, z * unit_scale
    conv.append((x, -z, y) if up_axis == 'Y_UP' else (x, y, z))

with open(out_path, 'w') as f:
    f.write(f'# from {src_path} (unit={unit_scale}, up={up_axis})\n')
    for v in conv:
        f.write(f'v {v[0]:.5f} {v[1]:.5f} {v[2]:.5f}\n')
    for tri in out_f:
        f.write(f'f {tri[0]+1} {tri[1]+1} {tri[2]+1}\n')

xs = [v[0] for v in conv]; ys = [v[1] for v in conv]; zs = [v[2] for v in conv]
print(f'{out_path}: {len(conv)} verts, {len(out_f)} tris, '
      f'size {max(xs)-min(xs):.3f} x {max(ys)-min(ys):.3f} x {max(zs)-min(zs):.3f} m')
