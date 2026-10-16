import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import check_model


class HeadroomTest(unittest.TestCase):
    def test_pendant_dimensions_include_glass_spheres_below_cables(self):
        lamp = dict(id='PENDANT', parts=[
            dict(cylinders=[[0, 0, 1.2, .15, 2]]),
            dict(spheres=[[0, 0, 1, .25]])], props={
                'Product': 'Lamp', 'Lieferant': 'Supplier', 'Contract': 'Proposal',
                'Plan': 'Plan', 'Dimensions': '50 x 50 x 125 cm'})
        check_model.fails.clear()
        check_model.check_dims_and_meta('TEST', dict(furniture=[lamp]))
        self.assertEqual(check_model.fails, [])
        lamp['props']['Dimensions'] = '50 x 50 x 100 cm'  # misses the sphere's bottom
        check_model.check_dims_and_meta('TEST', dict(furniture=[lamp]))
        self.assertEqual(len(check_model.fails), 1)
        self.assertIn('Dimensions says', check_model.fails[0])
        check_model.fails.clear()

    def test_tall_picture_does_not_make_whole_low_shelf_tall(self):
        plan = dict(roof_planes=[dict(id='ROOF', x0=0, x1=1,
                    profile=[[0, 3], [2, 1], [2, 1.2], [0, 3.2]])],
                    furniture=[dict(id='SHELF', parts=[
                        dict(boxes=[[0, 0, .1, 1, 2, .2]]),
                        dict(boxes=[[0, .2, .2, 1, .3, 2]])])])
        check_model.fails.clear()
        check_model.check_headroom('TEST', plan, 0)
        self.assertEqual(check_model.fails, [])
        broken = copy.deepcopy(plan)
        broken['furniture'][0]['parts'][1]['boxes'][0][1:5:3] = [1.8, 1.9]
        check_model.check_headroom('TEST', broken, 0)
        self.assertEqual(len(check_model.fails), 1)
        self.assertIn('component penetrates', check_model.fails[0])
        check_model.fails.clear()
