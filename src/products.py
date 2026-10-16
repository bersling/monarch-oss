"""Reusable product geometry and independent placements, with no room generator.

Products are local geometry in data/products/*.json. Instances live in
data/placements.json and survive room regeneration and variant reloads.
Coordinates are metres, x east / y north, z above finished storey floor.
The placement origin is the product's LOCAL origin, not its rotated bbox min.
"""
import argparse
import copy
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STOREYS = {'UG': 'basement', 'EG': 'ground_floor', 'OG': 'first_floor', 'DG': 'second_floor'}


def catalogue(root=ROOT):
    result = {}
    for path in sorted((Path(root) / 'data/products').glob('*.json')):
        product = json.loads(path.read_text())
        if product['id'] in result:
            raise ValueError(f"Duplicate product definition: {product['id']}")
        product['_file'] = str(path.relative_to(root))
        result[product['id']] = product
    return result


def placements(root=ROOT):
    path = Path(root) / 'data/placements.json'
    items = json.loads(path.read_text())['placements'] if path.is_file() else []
    seen = set()
    for item in items:
        if item['storey'] not in STOREYS:
            raise ValueError(f"Unknown placement storey: {item['storey']}")
        if item['id'] in seen:
            raise ValueError(f"Duplicate placed identity: {item['id']}")
        seen.add(item['id'])
    return items


def expand(instance, product):
    if not instance['id'].startswith('CAT_'):
        raise ValueError('Independent placements use CAT_ ids so room generators cannot delete them.')
    if instance['storey'] not in STOREYS:
        raise ValueError(f"Unknown storey: {instance['storey']}")
    at = instance['at']
    rotation = instance.get('rotation_z', 0)
    if len(at) != 3 or not all(math.isfinite(float(v)) for v in [*at, rotation]):
        raise ValueError('Placement needs three finite coordinates and a finite rotation.')
    if rotation % 90 != 0:
        raise ValueError('Use 0, 90, 180 or 270 degrees; cardinal rotations preserve exact solid bounds.')
    if set(instance) - {'id', 'product', 'storey', 'at', 'rotation_z', 'plan', 'status', 'notes'}:
        raise ValueError('Unknown placement field; scaling and product dimension overrides are not supported.')
    if not instance.get('plan') or not instance.get('status'):
        raise ValueError('Every placement needs a source plan and a selection/order status.')
    item = copy.deepcopy(product['geometry'])
    item['id'] = instance['id']
    item['props'] = {**product['props'], 'Product': product['label'], 'ProductLabel': product['label'],
                     'TechnicalID': instance['id'], 'ProductKind': product['kind'],
                     'CatalogueDefinition': product['_file'], 'PlacementSource': 'data/placements.json',
                     'Plan': instance['plan'], 'Status': instance['status']}
    if instance.get('notes'):
        item['props']['PlacementNotes'] = instance['notes']
    # Bake cardinal rotation/translation into the editable native primitives.
    # This keeps all existing room checks, 2D sketches and renderers usable.
    radians = math.radians(rotation)
    c, s = round(math.cos(radians)), round(math.sin(radians))

    def point(p):
        x, y, z = p
        return [round(c*x-s*y+at[0], 8), round(s*x+c*y+at[1], 8), round(z+at[2], 8)]

    def box(b):
        a, z = point(b[:3]), point(b[3:])
        return [min(a[i], z[i]) for i in range(3)] + [max(a[i], z[i]) for i in range(3)]

    def transform(node):
        if node.get('prisms'):
            raise ValueError('Catalogue prisms require a mesh conversion; do not rotate a YZ prism as a box.')
        if node.get('boxes'):
            node['boxes'] = [box(b) for b in node['boxes']]
        if node.get('cylinders'):
            node['cylinders'] = [point([x, y, z0]) + [r, round(z1+at[2], 8)] for x, y, z0, r, z1 in node['cylinders']]
        if node.get('spheres'):
            node['spheres'] = [point(sp[:3]) + sp[3:] for sp in node['spheres']]
        if node.get('mesh'):
            node['at'] = point(node.get('at', [0, 0, 0]))
            node['rotation_z'] = node.get('rotation_z', 0) + rotation
            if node.get('mesh_bounds'):
                node['mesh_bounds'] = box(node['mesh_bounds'])
        for part in node.get('parts', []):
            transform(part)
    transform(item)
    if item.get('access'):
        directions = ['+x', '+y', '-x', '-y']
        item['access']['dir'] = directions[(directions.index(item['access']['dir']) + int(rotation / 90)) % 4]
    return item


def augment(plan, storey, root=ROOT):
    items = [p for p in placements(root) if p['storey'] == storey]
    if not items:
        return plan
    products = catalogue(root)
    result = copy.deepcopy(plan)
    existing = {item['id'] for entries in result.values() if isinstance(entries, list)
                for item in entries if isinstance(item, dict) and 'id' in item}
    for instance in items:
        if instance['id'] in existing:
            raise ValueError(f"Duplicate placed identity: {instance['id']}")
        if instance['product'] not in products:
            raise ValueError(f"Unknown product: {instance['product']}")
        result.setdefault('furniture', []).append(expand(instance, products[instance['product']]))
        existing.add(instance['id'])
    return result


def load_plan(name, root=ROOT):
    name = STOREYS.get(name, name.removesuffix('.json'))
    code = next(s for s, n in STOREYS.items() if n == name)
    return augment(json.loads((Path(root) / f'data/{name}.json').read_text()), code, root)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    show = sub.add_parser('show'); show.add_argument('product')
    add = sub.add_parser('add'); add.add_argument('product'); add.add_argument('id')
    add.add_argument('--storey', choices=STOREYS, required=True)
    add.add_argument('--at', required=True, help='x,y,z; metres above finished storey floor')
    add.add_argument('--rotation', type=float, default=0)
    add.add_argument('--plan', required=True)
    add.add_argument('--status', required=True)
    move = sub.add_parser('move'); move.add_argument('id'); move.add_argument('--at', required=True)
    move.add_argument('--rotation', type=float)
    remove = sub.add_parser('remove'); remove.add_argument('id')
    args = parser.parse_args(argv)
    products = catalogue()
    if args.command == 'list':
        for p in products.values():
            print(f"{p['id']} · {p['label']} · {p['props']['Dimensions']} · {p['_file']}")
        return 0
    if args.command == 'show':
        if args.product not in products:
            parser.error(f'Unknown product {args.product!r}; use ./place list')
        print(json.dumps(products[args.product], indent=2, ensure_ascii=False))
        return 0
    items = placements()
    from viewer_common import atomic_json
    if args.command == 'remove':
        if not any(p['id'] == args.id for p in items):
            parser.error(f'{args.id}: no independent placement found')
        atomic_json(ROOT / 'data/placements.json', {'placements': [p for p in items if p['id'] != args.id]})
        print(f'Removed {args.id}. Viewer rebuilds on save; run ./build.sh to verify.')
        return 0
    at = [float(x) for x in args.at.split(',')]
    if args.command == 'add':
        if args.product not in products:
            parser.error(f'Unknown product {args.product!r}; use ./place list')
        if any(p['id'] == args.id for p in items):
            parser.error(f'{args.id} already exists; use move')
        instance = {'id': args.id, 'product': args.product, 'storey': args.storey, 'at': at,
                    'rotation_z': args.rotation, 'plan': args.plan, 'status': args.status}
        items.append(instance)
    else:
        hits = [p for p in items if p['id'] == args.id]
        if len(hits) != 1:
            parser.error(f'{args.id}: expected one independent placement')
        instance = hits[0]
        instance['at'] = at
        if args.rotation is not None:
            instance['rotation_z'] = args.rotation
    expand(instance, products[instance['product']])
    from documents import resolve
    if not resolve(instance['plan']):
        parser.error('Source plan does not resolve; use ./q plans')
    for code, name in STOREYS.items():
        plan = json.loads((ROOT / f'data/{name}.json').read_text())
        if any(it.get('id') == args.id for entries in plan.values() if isinstance(entries, list)
               for it in entries if isinstance(it, dict)):
            parser.error(f'{args.id} is already defined in data/{name}.json')
    atomic_json(ROOT / 'data/placements.json', {'placements': items})
    print(f"Placed {args.id}: {products[instance['product']]['label']}. Viewer rebuilds on save; run ./build.sh to verify.")
    return 0


if __name__ == '__main__':
    from model_lock import Lease
    from contextlib import nullcontext
    with Lease(ROOT) if len(sys.argv) > 1 and sys.argv[1] in ('add', 'move', 'remove') else nullcontext():
        sys.exit(main(sys.argv[1:]))
