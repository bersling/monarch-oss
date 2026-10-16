import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import products
from check_model import bodyboxes, bbox


class ProductTest(unittest.TestCase):
    def setUp(self):
        self.product = {'id': 'shelf', '_file': 'data/products/shelf.json', 'label': 'Real selected shelf',
                        'kind': 'catalogue_product', 'props': {'Dimensions': '80 x 40 x 120 cm'},
                        'geometry': {'parts': [{'name': 'frame', 'rgb': [.9, .9, .9],
                                                'boxes': [[0, 0, 0, .8, .4, 1.2]]}],
                                     'access': {'dir': '+y', 'depth': .75}}}
        self.instance = {'id': 'CAT_SHELF', 'product': 'shelf', 'storey': 'DG', 'at': [10, 2, 0],
                         'rotation_z': 90, 'plan': 'plans/DG.pdf', 'status': 'Proposed, not ordered'}

    def test_cardinal_rotation_and_identity_are_exact_and_pure(self):
        before = copy.deepcopy(self.product)
        item = products.expand(self.instance, self.product)
        self.assertEqual(self.product, before)
        self.assertEqual(item['parts'][0]['boxes'], [[9.6, 2, 0, 10, 2.8, 1.2]])
        self.assertEqual(item['access'], {'dir': '-x', 'depth': .75})
        self.assertEqual(item['props']['TechnicalID'], 'CAT_SHELF')
        self.assertEqual(item['props']['Product'], 'Real selected shelf')
        self.assertEqual(item['props']['Plan'], 'plans/DG.pdf')
        self.assertEqual(item['props']['Status'], 'Proposed, not ordered')

    def test_no_unverified_scaling_or_missing_status(self):
        for changes in [{'rotation_z': 45}, {'scale': .9}, {'status': ''}, {'at': [1, 2, float('nan')]}, {'id': 'DG_SE_NEW'}]:
            with self.assertRaises(ValueError):
                products.expand({**self.instance, **changes}, self.product)

    def test_augments_storey_without_mutating_variants_or_overwriting_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data/products').mkdir(parents=True)
            (root / 'data/products/shelf.json').write_text(json.dumps(self.product))
            (root / 'data/placements.json').write_text(json.dumps({'placements': [self.instance]}))
            plan = {'furniture': [{'id': 'EXISTING'}]}
            result = products.augment(plan, 'DG', root)
            self.assertEqual(plan, {'furniture': [{'id': 'EXISTING'}]})
            self.assertEqual([f['id'] for f in result['furniture']], ['EXISTING', 'CAT_SHELF'])
            self.assertIs(products.augment(plan, 'OG', root), plan)
            with self.assertRaises(ValueError):
                products.augment(result, 'DG', root)

    def test_reusable_kallax_has_manufacturer_envelope_and_real_compartments(self):
        library = products.catalogue()
        for pid, width, columns in [('ikea-kallax-2x2-white', .765, 2), ('ikea-kallax-4x2-white', 1.465, 4)]:
            item = products.expand({**self.instance, 'product': pid, 'rotation_z': 0, 'at': [0, 0, 0]}, library[pid])
            self.assertEqual(bbox(bodyboxes(item)), (0, 0, 0, width, .39, .765))
            dividers = next(p for p in item['parts'] if p['name'] == 'white_internal_dividers')
            self.assertEqual(len(dividers['boxes']), 1 + 2*(columns-1))
            self.assertEqual(item['props']['ProductKind'], 'catalogue_product')
