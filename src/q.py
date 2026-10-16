"""Ask the built model instead of guessing coordinates.  ./q <command> ...

  find TEXT                   elements whose key, product label or class contains TEXT
  storeys                     storey codes, names, floor elevations and plan files (data/house.json)
  sources [TEXT]              registered source documents (data/plan_documents.json), if any
  compare [CODE|roof|all]     overlay actual IFC sections on registered PDF plans (plan_compare.py)
  room [NAME]                 room cards from data/rooms.json: files, prefixes, active variant
  show REF                    class, storey, bounds, size, product label, properties and the
                              data/*.json file + path that defines it (where to edit)
  at X,Y[,Z] [--storey S]     what is at a point, and the distance to the nearest surface in
                              +x -x +y -y (and down/up): "the wall face is 0.412 m east"
  near REF [--radius R]       neighbours of REF within R (default 0.5 m) with clear gaps per axis
  free X0,Y0,Z0,X1,Y1,Z1      is this box free? lists every element whose actual mesh enters it
  clash REF                   actual-mesh penetrations of REF into anything else (> 5 mm)
  fits REF --at X,Y[,Z]       would REF's current footprint fit with its min corner moved to X,Y(,Z)?

REF is the IFC Name (= the JSON "id"), with @<storey code> when a name repeats on
several storeys. Coordinates are IFC world metres: x east, y north, z up; storey codes
and floor elevations come from data/house.json. Finishes sit below these datums.
Everything answers from the actual built IFC geometry (generated/geometry.npz), so
run ./build.sh (or keep ./open.sh's viewer running) after editing data/.
"""
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spatial  # noqa: E402
from geometry_cache import load  # noqa: E402
from house import HOUSE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STOREY_Z = dict(HOUSE.elevation)
SOLID = {'IfcWall', 'IfcWallStandardCase', 'IfcSlab', 'IfcStairFlight', 'IfcRailing', 'IfcDoor', 'IfcWindow',
         'IfcCovering', 'IfcFooting'}
IGNORE = {'IfcSpace', 'IfcAnnotation', 'IfcGeographicElement'}


def cm(v):
    return f'{v * 100:.1f} cm'


def m(v):
    return f'{v:.3f}'


def box_text(b):
    return f'x {m(b[0])}..{m(b[3])}  y {m(b[1])}..{m(b[4])}  z {m(b[2])}..{m(b[5])}'


def size_text(b):
    return f'{cm(b[3] - b[0])} x {cm(b[4] - b[1])} x {cm(b[5] - b[2])} (x, y, z)'


def parse_floats(text, n=None):
    values = [float(v) for v in text.replace(';', ',').split(',') if v.strip()]
    if n and len(values) not in (n if isinstance(n, tuple) else (n,)):
        raise SystemExit(f'expected {n} comma-separated numbers, got {text!r}')
    return values


def option(args, name, default=None):
    if name in args:
        i = args.index(name)
        value = args[i + 1]
        del args[i:i + 2]
        return value
    return default


def storey_of(z):
    return HOUSE.storey_of_z(z)


def resolve(g, ref):
    found = g.get(ref)
    if not found:
        close = [e['key'] for e in g.elements if ref.lower() in e['key'].lower()][:12]
        raise SystemExit(f'{ref}: not in the built model.' + (f' Did you mean: {", ".join(close)}' if close else ''))
    if len(found) > 1:
        raise SystemExit(f'{ref} is on several storeys: {", ".join(e["key"] for e in found)}')
    return found[0]


def sources(ref):
    """(file, json path) of every data/ object whose "id" is ref."""
    name = ref.split('@')[0]
    hits = []

    def walk(node, path, file):
        if isinstance(node, dict):
            if node.get('id') == name:
                hits.append((file, path))
            for k, v in node.items():
                walk(v, f'{path}.{k}', file)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f'{path}[{i}]', file)

    for file in sorted((ROOT / 'data').rglob('*.json')):
        text = file.read_text()
        if f'"{name}"' in text:
            walk(json.loads(text), '', file.relative_to(ROOT))
    return hits


def warn_if_stale(g):
    from viewer_common import read_json, source_hash
    stamp = read_json(ROOT / 'generated/viewer-build.json')
    if stamp.get('sources') and stamp['sources'] != source_hash(ROOT):
        print('! data/ or assets/ changed since the last build: answers show the last built model '
              '(./build.sh, or the ./open.sh viewer rebuilds automatically)\n')


def cmd_find(g, args):
    text = ' '.join(args).lower()
    rows = [e for e in g.elements if text in e['key'].lower() or text in (e['label'] or '').lower()
            or text in e['class'].lower()]
    for e in rows[:80]:
        b = e['bbox']
        label = f"  {e['label'][:70]}" if e['label'] else ''
        print(f"{e['key']:34} {e['class']:22} {e['storey']:4} {size_text(b):34}{label}")
    print(f'{len(rows)} match(es)' + (' (first 80 shown)' if len(rows) > 80 else ''))


def cmd_show(g, args):
    e = resolve(g, args[0])
    b = e['bbox']
    print(f"{e['key']}  {e['class']}  storey {e['storey']}")
    if e['label']:
        print(f"product  {e['label']}")
    print(f'bounds   {box_text(b)}')
    print(f'size     {size_text(b)}')
    if e['storey'] in STOREY_Z:
        print(f"z above storey floor  {m(b[2] - STOREY_Z[e['storey']])}..{m(b[5] - STOREY_Z[e['storey']])}")
    for pset, props in e['psets'].items():
        print(f'{pset}:')
        for k, v in props.items():
            print(f'  {k}: {v}')
    hits = sources(e['key'])
    print('defined in: ' + ('; '.join(f'{f} {p}' for f, p in hits) if hits else
                            'no data/ object with this id (generated inside a builder: grep src/)'))


def ray_distances(g, point, exclude=()):
    """Nearest hit per axis direction among solid elements: {dir: (distance, element)}."""
    out = {}
    dirs = {'+x': (1, 0, 0), '-x': (-1, 0, 0), '+y': (0, 1, 0), '-y': (0, -1, 0), 'down': (0, 0, -1), 'up': (0, 0, 1)}
    p = np.array(point, float)
    for label, d in dirs.items():
        d = np.array(d, float)
        k = int(np.argmax(np.abs(d)))
        best = (np.inf, None)
        for e in g.elements:
            if e['class'] in IGNORE or e['key'] in exclude:
                continue
            b = e['bbox']
            others = [i for i in range(3) if i != k]
            if any(not (b[i] - 1e-9 <= p[i] <= b[i + 3] + 1e-9) for i in others):
                continue
            if (d[k] > 0 and b[k + 3] < p[k]) or (d[k] < 0 and b[k] > p[k]):
                continue
            ahead = (b[k] - p[k]) if d[k] > 0 else (p[k] - b[k + 3])
            if ahead > best[0]:
                continue
            t = spatial.ray_hit(p, d, *g.mesh(e))
            if t < best[0]:
                best = (t, e)
        out[label] = best
    return out


def cmd_at(g, args):
    storey = option(args, '--storey')
    xyz = parse_floats(args[0], (2, 3))
    if len(xyz) == 2:
        s = storey or HOUSE.default_code()
        if s not in STOREY_Z:
            raise SystemExit(f'unknown storey {s!r}; codes: {", ".join(STOREY_Z)}')
        xyz.append(STOREY_Z[s] + 1.0)
        print(f'(z = {s} floor + 1.00 m = {m(xyz[2])})')
    p = np.array(xyz)
    inside = [e for e in g.elements if e['class'] not in IGNORE and spatial.points_in_box(p[None], e['bbox'], -1e-9)[0]]
    inside = [e for e in inside if spatial.points_in_mesh(p[None], *g.mesh(e))[0]]
    spaces = [e for e in g.elements if e['class'] == 'IfcSpace' and spatial.points_in_box(p[None], e['bbox'], -1e-9)[0]]
    if spaces:
        print('room: ' + ', '.join(f"{e['key']} ({e['label'] or ''})" for e in spaces))
    print('inside: ' + (', '.join(e['key'] for e in inside) if inside else 'nothing (free air)'))
    for label, (t, e) in ray_distances(g, p).items():
        if e is None:
            print(f'  {label:5} nothing')
        else:
            face = p + t * np.array({'+x': (1, 0, 0), '-x': (-1, 0, 0), '+y': (0, 1, 0), '-y': (0, -1, 0),
                                     'down': (0, 0, -1), 'up': (0, 0, 1)}[label])
            k = {'+x': 0, '-x': 0, '+y': 1, '-y': 1, 'down': 2, 'up': 2}[label]
            print(f"  {label:5} {m(t)} m to {e['key']} ({e['class']}) surface at {'xyz'[k]} = {m(face[k])}")


def cmd_near(g, args):
    radius = float(option(args, '--radius', 0.5))
    e = resolve(g, args[0])
    b = np.array(e['bbox'])
    rows = []
    for o in g.elements:
        if o is e or o['class'] in IGNORE:
            continue
        ob = np.array(o['bbox'])
        d = spatial.gap(b, ob)
        if d <= radius:
            gaps = np.maximum(ob[:3] - b[3:], b[:3] - ob[3:])     # per axis, negative = overlap
            rows.append((d, o, gaps))
    rows.sort(key=lambda r: r[0])
    print(f"{e['key']}  {box_text(b)}")
    for d, o, gaps in rows[:40]:
        side = []
        for i, axis in enumerate('xyz'):
            if gaps[i] >= -1e-6:
                direction = ('+' if o['bbox'][i] >= b[i + 3] - 1e-6 else '-') + axis
                side.append(f'{direction} gap {cm(gaps[i])}')
        where = ', '.join(side) if side else 'bounds overlap'
        print(f"  {o['key']:34} {o['class']:22} {where}")
    print(f'{len(rows)} within {radius} m (bounding boxes; ./q clash {e["key"]} for actual penetrations)')


def cmd_free(g, args):
    zone = parse_floats(args[0], 6)
    hits = []
    for e in g.elements:
        if e['class'] in IGNORE or not spatial.boxes_intersect(e['bbox'], zone):
            continue
        v, f = g.mesh(e)
        if spatial.mesh_enters_box(v, f, zone):
            hits.append(e)
    print(f'box {box_text(zone)}')
    if not hits:
        print('FREE: no element enters the box')
    for e in hits:
        ov = spatial.overlap(e['bbox'], zone)
        print(f"  blocked by {e['key']:34} {e['class']:22} overlap of bounds {cm(ov[0])} x {cm(ov[1])} x {cm(ov[2])}")


def cmd_clash(g, args):
    e = resolve(g, args[0])
    tol = float(option(args, '--tol', 0.005))
    mesh = g.mesh(e)
    found = 0
    for o in g.elements:
        if o is e or o['class'] in IGNORE or not spatial.boxes_intersect(e['bbox'], o['bbox'], tol):
            continue
        depth = spatial.penetration(mesh, g.mesh(o), np.array(e['bbox']), np.array(o['bbox']), tol)
        if depth:
            found += 1
            print(f"  {e['key']} and {o['key']} ({o['class']}) interpenetrate by {cm(depth)}")
    print('no penetrations above ' + cm(tol) if not found else f'{found} penetration(s)')


def cmd_fits(g, args):
    at = parse_floats(option(args, '--at'), (2, 3))
    e = resolve(g, args[0])
    b = np.array(e['bbox'])
    shift = np.array([at[0] - b[0], at[1] - b[1], (at[2] - b[2]) if len(at) == 3 else 0.0])
    zone = np.concatenate([b[:3] + shift, b[3:] + shift])
    v, f = g.mesh(e)
    moved = v + shift
    print(f"{e['key']} moved by {', '.join(cm(s) for s in shift)} -> {box_text(zone)}")
    blocked = 0
    for o in g.elements:
        if o is e or o['class'] in IGNORE or not spatial.boxes_intersect(zone, o['bbox'], 0.005):
            continue
        depth = spatial.penetration((moved, f), g.mesh(o), zone, np.array(o['bbox']), 0.005)
        if depth:
            blocked += 1
            print(f"  collides with {o['key']} ({o['class']}) by {cm(depth)}")
    print('FITS (actual meshes, 5 mm tolerance)' if not blocked else f'{blocked} collision(s)')


def cmd_storeys():
    for s in HOUSE.storeys:
        print(f"{s['code']:5} {s['name']:24} floor at z = {float(s['elevation']):+.3f}   data/{s['file']}")
    print('SITE  everything contained in the IfcSite' + (f"   data/{HOUSE.site_file}" if HOUSE.site_file else ''))


COMMANDS = {'find': cmd_find, 'show': cmd_show, 'at': cmd_at, 'near': cmd_near, 'free': cmd_free,
            'clash': cmd_clash, 'fits': cmd_fits}


def main(argv):
    if argv and argv[0] == 'compare':
        from plan_compare import main as compare
        return compare(argv[1:])
    if argv and argv[0] == 'room':
        from rooms import info
        info(argv[1:])
        return 0
    if argv and argv[0] in ('sources', 'plans'):
        from documents import cmd_sources
        cmd_sources(argv[1:])
        return 0
    if argv and argv[0] == 'storeys':
        cmd_storeys()
        return 0
    if not argv or argv[0] in ('-h', '--help', 'help') or argv[0] not in COMMANDS:
        print(__doc__)
        return 0 if argv and argv[0] in ('-h', '--help', 'help') else 2
    g = load()
    warn_if_stale(g)
    COMMANDS[argv[0]](g, list(argv[1:]))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
