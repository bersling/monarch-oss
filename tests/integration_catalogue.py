"""Verify a temporary reusable product in actual house IFC, then restore sources.

Run only in an isolated worktree with its watcher stopped:
    .venv/bin/python tests/integration_catalogue.py
Follow with ./build.sh to restore the final export. Refuses the main checkout.
"""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import documents
from geometry_cache import Geometry
from viewer_common import atomic_json


def main():
    if not (ROOT / '.git').is_file():
        raise SystemExit('Use an isolated worktree; this test adds a temporary placement.')
    path = ROOT / 'data/placements.json'
    before = path.read_bytes()
    instance = {'id': 'CAT_REGRESSION_KALLAX', 'product': 'ikea-kallax-2x2-white',
                'storey': 'OG', 'at': [20, 2, 0], 'rotation_z': 90,
                'plan': next(d['path'] for d in documents.catalogue() if d['id'] == 'floor-og'),
                'status': 'Temporary test fixture, never an owner selection or order'}
    try:
        value = json.loads(before)
        value['placements'].append(instance)
        atomic_json(path, value)
        for script in ('build_model.py', 'geometry_cache.py', 'export_glb.py'):
            subprocess.run([sys.executable, str(ROOT / 'src' / script)], cwd=ROOT, check=True)
        import ifcopenshell
        import ifcopenshell.util.element
        model = ifcopenshell.open(str(ROOT / 'generated/haus_nord_essentials.ifc'))
        entity, = [e for e in model.by_type('IfcFurnishingElement') if e.Name == instance['id']]
        props = ifcopenshell.util.element.get_psets(entity)['Pset_Product']
        assert 'KALLAX 2x2' in entity.Description
        assert entity.Tag == instance['id'] == props['TechnicalID']
        assert props['ArticleNumber'] == '202.758.14 x1'
        assert props['Plan'] == instance['plan']
        assert props['Status'] == instance['status']
        geometry = Geometry()
        element, = geometry.get(instance['id'])
        expected = [19.61, 2, 2.85, 20, 2.765, 3.615]
        assert all(abs(a-b) < 1e-5 for a,b in zip(element['bbox'], expected)), element['bbox']
        import spatial
        verts, faces = geometry.mesh(element)
        # Empty compartment is empty, solid shelf blocks a clearance zone.
        assert not spatial.mesh_enters_box(verts, faces, [19.7, 2.1, 2.95, 19.9, 2.3, 3.1])
        assert spatial.mesh_enters_box(verts, faces, [19.7, 2.1, 2.855, 19.9, 2.3, 2.875])
        print('PASS actual IFC catalogue size, rotation, compartments, standard product label, article and selection status')
    finally:
        path.write_bytes(before)


if __name__ == '__main__':
    main()
