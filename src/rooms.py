"""Small room index: exact files, active variant and one-room working commands.

./q room [name]         inspect a room without loading IFC geometry
./room sketch NAME     quick 2D drawing from the current storey JSON
./room generate NAME   run only its current generator and sync its active variant
./room check NAME      run its registered checks plus global rules/source/variant checks
./room snap NAME       actual-IFC plan and overview, using the browser renderer
"""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def load(root=ROOT):
    return json.loads((Path(root) / 'data/rooms.json').read_text())['rooms']


def find(query, root=ROOT):
    query = query.casefold()
    rooms = load(root)
    exact = [r for r in rooms if query in [r['id'].casefold(), *[a.casefold() for a in r['aliases']]]]
    matches = exact or [r for r in rooms if query in ' '.join([r['id'], r['name'], *r['aliases']]).casefold()]
    if len(matches) != 1:
        raise SystemExit(f'Room {query!r}: ' + ('ambiguous: ' + ', '.join(r['id'] for r in matches)
                                              if matches else 'unknown; use ./q room'))
    return matches[0]


def active(room, root=ROOT):
    if not room.get('group'):
        return 'default', None
    index = json.loads((Path(root) / 'data/variants.json').read_text())
    group = index['groups'][room['group']]
    name = group['active']
    return name, 'data/' + group['variants'][name]['file']


def bounds(room, root=ROOT):
    if room.get('bounds'):
        return room['bounds']
    source = room.get('bounds_source')
    if not source:
        raise SystemExit(f"{room['id']}: no rectangular sketch extent registered; use its room planner.")
    path, key = source
    value = json.loads((Path(root) / path).read_text())[key]
    if isinstance(value, dict):
        pieces = [v for v in value.values() if isinstance(v, list) and len(v) == 4
                  and all(isinstance(x, (int, float)) for x in v)]
        return [min(v[0] for v in pieces), min(v[1] for v in pieces),
                max(v[2] for v in pieces), max(v[3] for v in pieces)]
    return value


def info(args, root=ROOT):
    rooms = [find(' '.join(args), root)] if args else load(root)
    for room in rooms:
        name, saved = active(room, root)
        print(f"{room['id']} · {room['name']} · {room['storey']} · active {name}")
        print(f"  Edit: {room['layout']}" + ('; ' + '; '.join(room['inputs']) if room['inputs'] else ''))
        print(f"  Placed: data/{room['target']}" + (f'; saved: {saved}' if saved else ''))
        generator = room['generators'].get(name)
        if generator:
            print('  Generator: .venv/bin/python ' + ' '.join(generator))
        print('  Docs: ' + '; '.join(room['docs']))
        print('  Decisions: ' + room['decisions'])
        print(f"  Workflow: ./room generate {room['id']} ; ./room check {room['id']} ; ./room snap {room['id']}")
        print(f"  Plan: ./q plans {room['storey']}\n  Actual bounds: ./q find {room['prefixes'][0]}\n")


def run(command):
    subprocess.run(command, cwd=ROOT, check=True)


def main(args):
    if not args or args[0] in ('help', '--help', '-h'):
        print(__doc__)
        return 0
    action = args[0]
    if action == 'info':
        info(args[1:])
        return 0
    if action not in ('sketch', 'generate', 'check', 'snap') or len(args) != 2:
        raise SystemExit(__doc__)
    room = find(args[1])
    name, _ = active(room)
    if action == 'generate':
        generator = room['generators'].get(name)
        if generator is None:
            raise SystemExit(f"No generator registered for {room['id']}/{name}; inspect its docs.")
        from model_lock import Lease
        with Lease(ROOT) as lease:
            subprocess.run([sys.executable, *generator], cwd=ROOT, check=True, **lease.subprocess_options())
            if room.get('group'):
                subprocess.run([sys.executable, 'src/variant.py', 'load', room['group'], name, '--no-build'],
                               cwd=ROOT, check=True, **lease.subprocess_options())
        print('Room data updated. The running viewer rebuilds; ./build.sh builds and verifies everything.')
    elif action == 'sketch':
        output = ROOT / 'generated/rooms' / room['id'] / 'sketch.png'
        output.parent.mkdir(parents=True, exist_ok=True)
        run([sys.executable, 'src/plan2d.py', room['target'].removesuffix('.json'),
             ','.join(str(x) for x in bounds(room)), str(output)])
    elif action == 'check':
        from model_lock import Lease
        from serve import up_to_date
        with Lease(ROOT) as lease:
            if not up_to_date():
                subprocess.run([sys.executable, 'src/pipeline.py', 'build-only'], cwd=ROOT,
                               check=True, **lease.subprocess_options())
            subprocess.run([str(ROOT / 'check.sh'), *room['checks'], 'rules', 'documents', 'variant'],
                           cwd=ROOT, check=True, **lease.subprocess_options())
    elif action == 'snap':
        run([str(ROOT / 'open.sh'), '--no-browser', '--wait-build'])
        run([sys.executable, 'src/snap_web.py', room['id'] + '-plan', 'plan=' + room['storey'],
             room['id'] + '-overview', 'room=' + room['prefixes'][0], '--out',
             str(ROOT / 'generated/rooms' / room['id']), '--urls'])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
