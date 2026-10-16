"""Incremental geometry cache and GLB export on a small synthetic IFC."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import ifcopenshell
import ifcopenshell.api
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import export_glb  # noqa: E402
import geometry_cache  # noqa: E402


def make_ifc(path, shelf_x=1.0, wall_colour=(0.8, 0.8, 0.8)):
    run = ifcopenshell.api.run
    f = run('project.create_file', version='IFC4')
    project = run('root.create_entity', f, ifc_class='IfcProject', name='P')
    run('unit.assign_unit', f, length={'is_metric': True, 'raw': 'METERS'})
    model = run('context.add_context', f, context_type='Model')
    body = run('context.add_context', f, context_type='Model', context_identifier='Body',
               target_view='MODEL_VIEW', parent=model)
    site = run('root.create_entity', f, ifc_class='IfcSite', name='S')
    building = run('root.create_entity', f, ifc_class='IfcBuilding', name='B')
    storey = run('root.create_entity', f, ifc_class='IfcBuildingStorey', name='Ground floor')
    run('aggregate.assign_object', f, products=[site], relating_object=project)
    run('aggregate.assign_object', f, products=[building], relating_object=site)
    run('aggregate.assign_object', f, products=[storey], relating_object=building)

    def product(cls, name, item, colour):
        e = run('root.create_entity', f, ifc_class=cls, name=name)
        run('geometry.edit_object_placement', f, product=e)
        rep = f.createIfcShapeRepresentation(body, 'Body', 'Tessellation', [item])
        run('geometry.assign_representation', f, product=e, representation=rep)
        style = run('style.add_style', f, name=name)
        run('style.add_surface_style', f, style=style, ifc_class='IfcSurfaceStyleShading',
            attributes={'SurfaceColour': {'Name': None, 'Red': colour[0], 'Green': colour[1], 'Blue': colour[2]}})
        run('style.assign_representation_styles', f, shape_representation=rep, styles=[style])
        run('spatial.assign_container', f, products=[e], relating_structure=storey)
        pset = run('pset.add_pset', f, product=e, name='Pset_Product')
        run('pset.edit_pset', f, pset=pset, properties={'Product': f'{name} product'})
        return e

    def box(x0, y0, z0, x1, y1, z1):
        pts = [(float(x), float(y), float(z)) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
        quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        tris = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
        return f.createIfcTriangulatedFaceSet(f.createIfcCartesianPointList3D(pts), None, None,
                                              [[a + 1, b + 1, c + 1] for a, b, c in tris], None)

    product('IfcWall', 'W_NORTH', box(0, 5, 0, 6, 5.2, 2.5), wall_colour)
    product('IfcFurnishingElement', 'SHELF', box(shelf_x, 4.6, 0, shelf_x + 0.8, 5.0, 2.0), (0.9, 0.9, 0.9))
    product('IfcFurnishingElement', 'BED', box(3, 2, 0, 4.4, 4, 0.5), (0.3, 0.4, 0.5))
    f.write(str(path))


def flat(out_dir):
    g = geometry_cache.Geometry(out_dir)
    result = {}
    for e in g.elements:
        v, f = g.mesh(e)
        fm = g.face_material[e['faces'][0]:e['faces'][1]]
        colours = [tuple(g.meta['palette'][i]['rgb']) for i in fm]
        result[e['key']] = (v.round(9).tolist(), f.tolist(), colours, e['psets'], e['bbox'])
    return result


class GeometryCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.ifc = self.dir / 'model.ifc'

    def build(self, incremental=True, **kw):
        make_ifc(self.ifc, **kw)
        meta, _ = geometry_cache.build(self.ifc, self.dir, incremental=incremental)
        return meta

    def test_incremental_equals_full_and_reuses(self):
        self.build(incremental=False)
        geometry = geometry_cache.Geometry(self.dir)
        self.assertEqual(geometry.get('SHELF@GF'), geometry.get('SHELF'))
        self.assertEqual(geometry.get('SHELF@UG'), [])
        again = flat(self.dir)
        # Rebuilding the same content (fresh GUIDs, same STEP content) reuses everything.
        self.build()
        self.assertEqual(flat(self.dir), again)
        # Move the shelf, recolour the wall: exactly those two re-mesh, result equals a full run.
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.build(shelf_x=2.0, wall_colour=(0.2, 0.3, 0.4))
        self.assertIn('2 re-meshed, 1 reused', out.getvalue())
        incremental = flat(self.dir)
        self.build(incremental=False, shelf_x=2.0, wall_colour=(0.2, 0.3, 0.4))
        self.assertEqual(incremental, flat(self.dir))
        self.assertEqual(incremental['SHELF'][4][0], 2.0)
        self.assertEqual(incremental['W_NORTH'][2][0], (0.2, 0.3, 0.4))
        self.assertEqual(incremental['BED'][3]['Pset_Product']['Product'], 'BED product')

    def test_glb_export(self):
        self.build(incremental=False)
        glb = self.dir / 'model.glb'
        export_glb.build(geometry_cache.Geometry(self.dir), glb)
        data = glb.read_bytes()
        magic, version, length = struct.unpack('<III', data[:12])
        self.assertEqual((magic, version, length), (0x46546C67, 2, len(data)))
        json_len, kind = struct.unpack('<II', data[12:20])
        doc = json.loads(data[20:20 + json_len])
        self.assertEqual(kind, 0x4E4F534A)
        self.assertEqual(doc['extras']['build_id'], geometry_cache.Geometry(self.dir).meta['build_id'])
        self.assertEqual({n['extras']['key'] for n in doc['nodes']}, {'W_NORTH', 'SHELF', 'BED'})
        # y-up: the bed's IFC z range 0..0.5 becomes glTF y; IFC y 2..4 becomes glTF z -4..-2.
        bed = next(n for n in doc['nodes'] if n['name'] == 'BED')
        acc = doc['accessors'][doc['meshes'][bed['mesh']]['primitives'][0]['attributes']['POSITION']]
        np.testing.assert_allclose(acc['min'], [3, 0, -4])
        np.testing.assert_allclose(acc['max'], [4.4, 0.5, -2])
        # sRGB 0.5 -> linear 0.214
        colour = doc['materials'][doc['meshes'][bed['mesh']]['primitives'][0]['material']]
        self.assertAlmostEqual(colour['pbrMetallicRoughness']['baseColorFactor'][2], 0.214, places=3)
        self.assertTrue(all('NORMAL' not in p['attributes'] for m in doc['meshes'] for p in m['primitives']))
        geometry = geometry_cache.Geometry(self.dir)
        shelf = next(e for e in geometry.elements if e['name'] == 'SHELF')
        shelf['psets']['Pset_Product']['SurfaceShading'] = 'smooth'
        export_glb.build(geometry, glb)
        data = glb.read_bytes(); json_len = struct.unpack_from('<I', data, 12)[0]
        doc = json.loads(data[20:20+json_len]); binary = data[28+json_len:]
        for node in doc['nodes']:
            for primitive in doc['meshes'][node['mesh']]['primitives']:
                attributes = primitive['attributes']
                if node['name'] != 'SHELF':
                    self.assertNotIn('NORMAL', attributes)
                    continue
                acc = doc['accessors'][attributes['NORMAL']]
                self.assertEqual(acc['count'], doc['accessors'][attributes['POSITION']]['count'])
                view = doc['bufferViews'][acc['bufferView']]
                normals = np.frombuffer(binary, dtype='<f4', count=acc['count']*3, offset=view['byteOffset']).reshape(-1, 3)
                self.assertTrue(np.isfinite(normals).all())
                np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-6)

    def test_mixed_cache_publication_is_rejected(self):
        self.build(incremental=False)
        old_metadata = (self.dir / 'elements.json').read_bytes()
        self.build(shelf_x=2.0)
        (self.dir / 'elements.json').write_bytes(old_metadata)
        with self.assertRaisesRegex(ValueError, 'different builds'):
            geometry_cache.Geometry(self.dir)

    def test_staleness_uses_content_even_when_mtime_is_restored(self):
        import os
        from unittest.mock import patch
        self.build(incremental=False)
        cached = geometry_cache.Geometry(self.dir)
        with patch.object(geometry_cache, 'MODEL', self.ifc):
            self.assertFalse(cached.stale())
            before = self.ifc.stat()
            make_ifc(self.ifc, shelf_x=2.0)
            os.utime(self.ifc, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertTrue(cached.stale())


if __name__ == '__main__':
    unittest.main()
