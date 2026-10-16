"""Slab underside changes must preserve footprint, voids and finish recesses."""
from pathlib import Path
import sys
import unittest
from shapely.geometry import box
from shapely.ops import unary_union
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from floor_finishes import slab_solids


class SlabCellsTest(unittest.TestCase):
    def test_downstand_preserves_void_and_top_recess(self):
        slab = {'id': 'floor', 'rects': [[0,0,2,4], [2,0,4,1], [2,3,4,4]],
                'thickness': .35, 'z_top': 0, 'recess_finishes': True,
                'underside_zones': [{'rects': [[1,0,3,4]], 'z_bottom': -.45}]}
        cells = list(slab_solids(slab, [{'rects': [[0,0,4,2]], 'z_top': 0, 'thickness': .02}]))
        shapes = [box(*r) for r, _, _ in cells]
        footprint = unary_union([box(*r) for r in slab['rects']])
        self.assertAlmostEqual(unary_union(shapes).symmetric_difference(footprint).area, 0)
        self.assertAlmostEqual(sum(p.area for p in shapes), footprint.area)
        self.assertTrue(all(p.intersection(box(2,1,4,3)).area == 0 for p in shapes))
        for rect, bottom, top in cells:
            x,y=box(*rect).centroid.coords[0]
            self.assertEqual(bottom, -.45 if 1 < x < 3 else -.35)
            self.assertEqual(top, -.02 if y < 2 else 0)

    def test_overlapping_zones_rejected(self):
        slab = {'id':'floor', 'rect':[0,0,4,4], 'thickness':.35,
                'underside_zones':[{'rects':[[0,0,3,3],[2,2,4,4]], 'z_bottom':-.45}]}
        with self.assertRaisesRegex(ValueError, 'overlapping underside'):
            list(slab_solids(slab, []))
