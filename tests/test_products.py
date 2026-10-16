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
        self.instance = {'id': 'CAT_SHELF', 'product': 'shelf', 'storey': 'UF', 'at': [10, 2, 0],
                         'rotation_z': 90, 'source': 'docs/data-format.md', 'status': 'Proposed, not ordered'}

    def test_cardinal_rotation_and_identity_are_exact_and_pure(self):
        before = copy.deepcopy(self.product)
        item = products.expand(self.instance, self.product)
        self.assertEqual(self.product, before)
        self.assertEqual(item['parts'][0]['boxes'], [[9.6, 2, 0, 10, 2.8, 1.2]])
        self.assertEqual(item['access'], {'dir': '-x', 'depth': .75})
        self.assertEqual(item['props']['TechnicalID'], 'CAT_SHELF')
        self.assertEqual(item['props']['Product'], 'Real selected shelf')
        self.assertEqual(item['props']['Plan'], 'docs/data-format.md')
        self.assertEqual(item['props']['Status'], 'Proposed, not ordered')

    def test_no_unverified_scaling_or_missing_status(self):
        for changes in [{'rotation_z': 45}, {'scale': .9}, {'status': ''}, {'at': [1, 2, float('nan')]}, {'id': 'UF_NEW'}, {'storey': 'XX'}]:
            with self.assertRaises(ValueError):
                products.expand({**self.instance, **changes}, self.product)

    def test_augments_storey_without_mutating_variants_or_overwriting_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data/products').mkdir(parents=True)
            (root / 'data/products/shelf.json').write_text(json.dumps(self.product))
            (root / 'data/placements.json').write_text(json.dumps({'placements': [self.instance]}))
            plan = {'furniture': [{'id': 'EXISTING'}]}
            result = products.augment(plan, 'UF', root)
            self.assertEqual(plan, {'furniture': [{'id': 'EXISTING'}]})
            self.assertEqual([f['id'] for f in result['furniture']], ['EXISTING', 'CAT_SHELF'])
            self.assertIs(products.augment(plan, 'GF', root), plan)
            with self.assertRaises(ValueError):
                products.augment(result, 'UF', root)

    def test_catalogue_product_keeps_its_envelope_and_shelves(self):
        library = products.catalogue()
        item = products.expand({**self.instance, 'product': 'demo-bookshelf', 'rotation_z': 0, 'at': [0, 0, 0]},
                               library['demo-bookshelf'])
        self.assertEqual(bbox(bodyboxes(item)), (0, 0, 0, .8, .4, 1.2))
        shelves = next(p for p in item['parts'] if p['name'] == 'shelves')
        self.assertEqual(len(shelves['boxes']), 2)
        self.assertEqual(item['props']['ProductKind'], 'catalogue_product')
        self.assertEqual(products.check(), [])
