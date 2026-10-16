"""Declarative model rules: data/rules/*.json evaluated on the actual IFC geometry.

    uv run python src/rules.py                  all rule files
    uv run python src/rules.py gym              files whose name contains "gym"

Rooms are data, not scripts: prefer a few lines here over a new check_*.py. Rule
kinds (every rule may carry "id", "why" and "severity": "error" | "warn"):

  no_penetration  "what" solids must not poke into "into" solids by more than
                  "tolerance" (default 0.005 m). "known": {"A|B": "reason"} accepts
                  a documented, intentional overlap (e.g. a flush-mounted cabinet).
                  A reason starting "UNREVIEWED" keeps a pre-existing overlap
                  visible as a warning without failing; anything new fails.
  clear           nothing of "what" (default: any furnishing/equipment) may enter
                  "zone" [x0,y0,z0,x1,y1,z1]; "except": [keys] may.
  bbox            element's actual bounds: "expect" any of xmin..zmax (absolute) and
                  "size" [dx, dy, dz]; "tol" default 0.003 m.
  inside          element's bounds must lie within "zone" (tol).
  gap             clear distance between "a" and "b" along "axis" within
                  "min"/"max" (a negative distance is an overlap).
  ray             nearest surface of "element" from "origin" in "direction";
                  "distance" in metres (null requires an unobstructed ray).

Selectors ("what", "into"): {"class": [...], "storey": "UG|EG|OG|DG|SITE",
"name": "GYM_*" (glob, list allowed), "exclude": [globs]}. Element references are the
IFC Name (= JSON id), with "@EG" when the name repeats on several storeys.
"""
import fnmatch
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spatial  # noqa: E402
from geometry_cache import load as load_geometry  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / 'data' / 'rules'
FURNISHING = ['IfcFurnishingElement', 'IfcSanitaryTerminal', 'IfcElectricAppliance', 'IfcUnitaryEquipment',
              'IfcTank', 'IfcPump', 'IfcFilter', 'IfcBuildingElementProxy']
STRUCTURE = ['IfcWall', 'IfcWallStandardCase', 'IfcSlab', 'IfcStairFlight', 'IfcRailing',
             'IfcBeam', 'IfcColumn', 'IfcMember']


def as_list(value):
    return value if isinstance(value, list) else [value]


def select(geometry, selector):
    if isinstance(selector, str):
        selector = {'name': selector}
    classes = set(as_list(selector.get('class', [])))
    storey = selector.get('storey')
    names = as_list(selector.get('name', ['*']))
    exclude = as_list(selector.get('exclude', []))
    out = []
    for e in geometry.elements:
        if classes and e['class'] not in classes:
            continue
        if storey and e['storey'] != storey:
            continue
        if not any(fnmatch.fnmatchcase(e['key'], n) or fnmatch.fnmatchcase(e['name'], n) for n in names):
            continue
        if any(fnmatch.fnmatchcase(e['key'], n) or fnmatch.fnmatchcase(e['name'], n) for n in exclude):
            continue
        out.append(e)
    return out


def one(geometry, ref):
    found = geometry.get(ref)
    if len(found) != 1:
        raise LookupError(f'{ref!r}: {"not in the IFC" if not found else "ambiguous: " + ", ".join(e["key"] for e in found)}')
    return found[0]


def fmt(v):
    return f'{v:.3f}'


def rule_no_penetration(g, rule):
    tol = rule.get('tolerance', 0.005)
    what, into = select(g, rule['what']), select(g, rule.get('into', {'class': STRUCTURE}))
    known = rule.get('known', {})
    unreviewed = []
    boxes = np.array([e['bbox'] for e in into]) if into else np.zeros((0, 6))
    problems, seen = [], set()
    for a in what:
        if not len(boxes):
            break
        box = np.array(a['bbox'])
        near = np.where(((np.minimum(box[3:], boxes[:, 3:]) - np.maximum(box[:3], boxes[:, :3])) > tol).all(1))[0]
        for i in near:
            b = into[i]
            if a is b or (b['key'], a['key']) in seen:
                continue
            seen.add((a['key'], b['key']))
            depth = spatial.penetration(g.mesh(a), g.mesh(b), np.array(a['bbox']), np.array(b['bbox']), tol)
            if depth:
                reason = known.get(f"{a['key']}|{b['key']}") or known.get(f"{b['key']}|{a['key']}")
                message = f"{a['key']} pokes {depth * 100:.1f} cm into {b['key']}"
                if reason and reason.startswith('UNREVIEWED'):
                    unreviewed.append(message + ' (unreviewed, listed in the rule file)')
                elif not reason:
                    problems.append(message)
    return problems, unreviewed


def rule_clear(g, rule):
    zone = np.array(rule['zone'], float)
    what = select(g, rule.get('what', {'class': FURNISHING}))
    allowed = set(rule.get('except', []))
    hits = []
    for e in what:
        if e['key'] in allowed or e['name'] in allowed or not spatial.boxes_intersect(e['bbox'], zone):
            continue
        v, f = g.mesh(e)
        if spatial.mesh_enters_box(v, f, zone):
            ov = spatial.overlap(e['bbox'], zone)
            hits.append(f"{e['key']} enters the zone ({' x '.join(fmt(x) for x in ov)} m overlap of bounds)")
    return hits


def rule_bbox(g, rule):
    e = one(g, rule['element'])
    b, tol, out = np.array(e['bbox']), rule.get('tol', 0.003), []
    for face, want in rule.get('expect', {}).items():
        have = b[spatial.FACES[face]]
        if abs(have - want) > tol:
            out.append(f"{e['key']} {face} is {fmt(have)}, expected {fmt(want)} (off {100 * (have - want):+.1f} cm)")
    if 'size' in rule:
        have = b[3:] - b[:3]
        for axis, h, w in zip('xyz', have, rule['size']):
            if w is not None and abs(h - w) > tol:
                out.append(f"{e['key']} size {axis} is {fmt(h)}, expected {fmt(w)}")
    return out


def rule_inside(g, rule):
    e = one(g, rule['element'])
    b, z, tol = np.array(e['bbox']), np.array(rule['zone'], float), rule.get('tol', 0.001)
    out = []
    for i, face in enumerate(spatial.FACES):
        if (i < 3 and b[i] < z[i] - tol) or (i >= 3 and b[i] > z[i] + tol):
            out.append(f"{e['key']} {face} {fmt(b[i])} is outside the zone ({fmt(z[i])})")
    return out


def rule_gap(g, rule):
    a, b = one(g, rule['a']), one(g, rule['b'])
    k = spatial.AXES[rule['axis']]
    A, B = np.array(a['bbox']), np.array(b['bbox'])
    distance = max(B[k] - A[k + 3], A[k] - B[k + 3])
    out = []
    if 'min' in rule and distance < rule['min'] - 1e-6:
        out.append(f"{a['key']} to {b['key']} along {rule['axis']} is {fmt(distance)}, min {fmt(rule['min'])}")
    if 'max' in rule and distance > rule['max'] + 1e-6:
        out.append(f"{a['key']} to {b['key']} along {rule['axis']} is {fmt(distance)}, max {fmt(rule['max'])}")
    return out


def rule_ray(g, rule):
    element = one(g, rule['element'])
    direction = np.asarray(rule['direction'], float)
    length = np.linalg.norm(direction)
    if not np.isfinite(length) or length <= 0:
        raise ValueError('ray direction must be finite and nonzero')
    distance = spatial.ray_hit(rule['origin'], direction / length, *g.mesh(element))
    expected = rule['distance']
    if expected is None:
        return [] if np.isinf(distance) else [f"{element['key']} obstructs the ray at {fmt(distance)} m"]
    if not np.isfinite(distance) or abs(distance - expected) > rule.get('tol', .003):
        return [f"{element['key']} surface is {fmt(distance)} m along ray, expected {fmt(expected)} m"]
    return []


KINDS = {'no_penetration': rule_no_penetration, 'clear': rule_clear, 'bbox': rule_bbox,
         'inside': rule_inside, 'gap': rule_gap, 'ray': rule_ray}


def evaluate(geometry, rules):
    """Yield (rule, severity, [problems], [warnings])."""
    for rule in rules:
        warnings = []
        try:
            problems = KINDS[rule['rule']](geometry, rule)
            if isinstance(problems, tuple):
                problems, warnings = problems
        except LookupError as missing:
            problems = [str(missing)]
        yield rule, rule.get('severity', 'error'), problems, warnings


def load(words=()):
    files = sorted(RULES.glob('*.json'))
    return [(f, json.loads(f.read_text())) for f in files if not words or any(w in f.stem for w in words)]


def main(args):
    geometry = load_geometry()
    errors = warnings = total = 0
    for path, doc in load([a for a in args if not a.startswith('-')]):
        for rule, severity, problems, notes in evaluate(geometry, doc['rules']):
            total += 1
            label = f"{path.stem}:{rule.get('id', rule['rule'])}"
            for p in problems:
                print(f"{'FAIL' if severity == 'error' else 'warn'}  {label}  {p}")
            for p in notes:
                print(f"warn  {label}  {p}")
            if problems and severity == 'error':
                errors += 1
            elif problems or notes:
                warnings += 1
    print(f'rules: {total} evaluated, {errors} failing, {warnings} warning')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
