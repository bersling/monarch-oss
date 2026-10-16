"""Spatial primitives (src/spatial.py) and declarative rules (src/rules.py) on synthetic meshes."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import rules  # noqa: E402
import spatial  # noqa: E402


def cube(x0, y0, z0, x1, y1, z1):
    v = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], float)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    f = np.array([t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))])
    return v, f


class SpatialTest(unittest.TestCase):
    def test_box_gap_and_overlap(self):
        a, b = [0, 0, 0, 1, 1, 1], [1.5, 0, 0, 2, 1, 1]
        self.assertAlmostEqual(spatial.gap(a, b), 0.5)
        self.assertFalse(spatial.boxes_intersect(a, b))
        self.assertTrue(spatial.boxes_intersect(a, [0.9, 0.9, 0.9, 2, 2, 2]))
        self.assertFalse(spatial.boxes_intersect(a, [1, 0, 0, 2, 1, 1]), 'touching faces are not an overlap')

    def test_triangle_box_overlap(self):
        v, f = cube(0, 0, 0, 1, 1, 1)
        self.assertTrue(spatial.tris_box(v, f, [0.9, 0.2, 0.2, 1.2, 0.4, 0.4]).any())
        self.assertFalse(spatial.tris_box(v, f, [1.01, 0.2, 0.2, 1.2, 0.4, 0.4]).any())
        # A zone fully inside the cube touches no triangle: callers also test vertices.
        self.assertFalse(spatial.tris_box(v, f, [0.2, 0.2, 0.2, 0.4, 0.4, 0.4]).any())
        # A big triangle slicing through a small box without any vertex inside it.
        tri_v = np.array([[-5, -5, 0.5], [5, -5, 0.5], [0, 5, 0.5]])
        self.assertTrue(spatial.tris_box(tri_v, np.array([[0, 1, 2]]), [0.4, 0.4, 0.4, 0.6, 0.6, 0.6]).all())

    def test_points_in_mesh(self):
        v, f = cube(0, 0, 0, 1, 1, 1)
        pts = np.array([[0.5, 0.5, 0.5], [1.5, 0.5, 0.5], [0.01, 0.99, 0.5], [-0.01, 0.5, 0.5]])
        self.assertEqual(spatial.points_in_mesh(pts, v, f).tolist(), [True, False, True, False])

    def test_mesh_enters_box_includes_enclosed_volume(self):
        v, f = cube(0, 0, 0, 3, 3, 3)
        self.assertTrue(spatial.mesh_enters_box(v, f, [1, 1, 1, 2, 2, 2]))
        self.assertFalse(spatial.mesh_enters_box(v, f, [3, 1, 1, 4, 2, 2]))
        self.assertTrue(spatial.mesh_enters_box(v, f, [2.9, 1, 1, 4, 2, 2]))
        # A void inside a hollow closed mesh must stay free.
        inner, inner_f = cube(.5, .5, .5, 2.5, 2.5, 2.5)
        self.assertFalse(spatial.mesh_enters_box(np.vstack([v, inner]),
                        np.vstack([f, inner_f + len(v)]), [1, 1, 1, 2, 2, 2]))

    def test_penetration_depth(self):
        wall = cube(0, 0, 0, 0.2, 3, 2.5)
        shelf = cube(0.17, 1, 0, 0.6, 1.8, 2)                  # 3 cm into the wall
        depth = spatial.penetration(shelf, wall, np.r_[shelf[0].min(0), shelf[0].max(0)],
                                    np.r_[wall[0].min(0), wall[0].max(0)], 0.005)
        self.assertAlmostEqual(depth, 0.03, places=6)
        flush = cube(0.2, 1, 0, 0.6, 1.8, 2)
        self.assertEqual(spatial.penetration(flush, wall, tolerance=0.005), 0.0)

    def test_thin_wall_straddled(self):
        """A box pushed right through a thin wall has no vertex inside it; centroids catch it."""
        wall = cube(1, 0, 0, 1.02, 3, 2.5)
        box = cube(0.5, 1, 0.5, 1.5, 1.2, 0.7)
        self.assertGreater(spatial.penetration(box, wall, tolerance=0.005), 0.005)

    def test_ray_hit(self):
        v, f = cube(2, -1, -1, 3, 1, 1)
        self.assertAlmostEqual(spatial.ray_hit([0, 0, 0], [1, 0, 0], v, f), 2.0)
        self.assertEqual(spatial.ray_hit([0, 0, 0], [-1, 0, 0], v, f), np.inf)


class FakeGeometry:
    def __init__(self, boxes):
        self.elements, self.meshes = [], {}
        for key, (cls, box) in boxes.items():
            self.elements.append({'key': key, 'name': key, 'class': cls, 'storey': 'GF', 'bbox': list(box)})
            self.meshes[key] = cube(*box)

    def mesh(self, e):
        return self.meshes[e['key']]

    def get(self, ref):
        return [e for e in self.elements if e['key'] == ref]


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.g = FakeGeometry({
            'W_NORTH': ('IfcWall', (0, 5, 0, 6, 5.2, 2.5)),
            'SHELF': ('IfcFurnishingElement', (1, 4.6, 0, 1.8, 5.03, 2)),        # 3 cm into the wall
            'BED': ('IfcFurnishingElement', (3, 2, 0, 4.4, 4.0, 0.5)),
        })

    def run_rule(self, rule):
        (_, _, problems, notes), = rules.evaluate(self.g, [rule])
        return problems, notes

    def test_penetration_and_known(self):
        rule = {'rule': 'no_penetration', 'what': {'class': 'IfcFurnishingElement'}, 'into': {'class': 'IfcWall'}}
        problems, _ = self.run_rule(rule)
        self.assertEqual(len(problems), 1)
        self.assertIn('SHELF pokes 3.0 cm into W_NORTH', problems[0])
        problems, notes = self.run_rule({**rule, 'known': {'SHELF|W_NORTH': 'UNREVIEWED: old'}})
        self.assertEqual((problems, len(notes)), ([], 1))
        problems, notes = self.run_rule({**rule, 'known': {'W_NORTH|SHELF': 'flush-mounted, intended'}})
        self.assertEqual((problems, notes), ([], []))

    def test_clear_zone(self):
        problems, _ = self.run_rule({'rule': 'clear', 'zone': [3.5, 3.8, 0, 5, 4.5, 2]})
        self.assertEqual(len(problems), 1)
        self.assertIn('BED', problems[0])
        problems, _ = self.run_rule({'rule': 'clear', 'zone': [3.5, 3.8, 0, 5, 4.5, 2], 'except': ['BED']})
        self.assertEqual(problems, [])

    def test_clear_zone_enclosed_by_bed_is_blocked(self):
        problems, _ = self.run_rule({'rule': 'clear', 'zone': [3.2, 2.2, .1, 3.8, 3.8, .4]})
        self.assertEqual(len(problems), 1)
        self.assertIn('BED', problems[0])

    def test_bbox_gap_inside(self):
        self.assertEqual(self.run_rule({'rule': 'bbox', 'element': 'BED', 'expect': {'xmin': 3.0},
                                        'size': [1.4, 2.0, 0.5]})[0], [])
        problems, _ = self.run_rule({'rule': 'bbox', 'element': 'BED', 'expect': {'ymax': 4.1}})
        self.assertIn('off -10.0 cm', problems[0])
        self.assertEqual(self.run_rule({'rule': 'gap', 'a': 'BED', 'b': 'W_NORTH', 'axis': 'y', 'min': 0.9})[0], [])
        self.assertTrue(self.run_rule({'rule': 'gap', 'a': 'BED', 'b': 'W_NORTH', 'axis': 'y', 'max': 0.5})[0])
        self.assertTrue(self.run_rule({'rule': 'inside', 'element': 'BED', 'zone': [0, 0, 0, 4, 5, 3]})[0])
        self.assertIn('not in the IFC', self.run_rule({'rule': 'bbox', 'element': 'NOPE', 'expect': {}})[0][0])

    def test_ray_checks_nearest_surface_and_voids(self):
        rule = {'rule':'ray', 'element':'BED', 'origin':[3.5,3,2],
                'direction':[0,0,-4], 'distance':1.5}
        self.assertEqual(self.run_rule(rule)[0], [])
        self.assertTrue(self.run_rule({**rule, 'distance':2})[0], 'A bottom-face match cannot hide the closer top')
        self.assertTrue(self.run_rule({**rule, 'distance':None})[0])
        self.assertEqual(self.run_rule({**rule, 'origin':[0,0,2], 'distance':None})[0], [])
        self.assertTrue(self.run_rule({**rule, 'origin':[0,0,2]})[0], 'Missing surfaces must fail')



class FlushTest(unittest.TestCase):
    def test_flush_and_edge_on_face_is_not_penetration(self):
        wall = cube(0, 0, 0, 0.2, 3, 2.5)
        for box in [(0.2, 1, 0, 0.6, 1.8, 2), (0.2, -1, 0, 0.6, 4, 2.5), (0.2, 1, 0, 0.6, 1.8, 3)]:
            self.assertEqual(spatial.penetration(cube(*box), wall, tolerance=0.0), 0.0, box)


if __name__ == '__main__':
    unittest.main()
