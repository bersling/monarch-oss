import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from extract_outlets import wall_faces, place
from extract_switches import mount
from light_switches import validate
from profile_wall_faces import fixture_faces


class ProfileFixtureTest(unittest.TestCase):
    def setUp(self):
        self.wall = dict(id='RIDGE', mount_axis='y', x0=8.46, x1=13.66,
                        profile=[[5.51, 5.7], [5.71, 5.7], [5.71, 9.76],
                                 [5.61, 9.86], [5.51, 9.76]])
        self.plan = dict(building_dimensions={'storey_height': 2.85},
                         profile_walls=[self.wall])
        self.bank = dict(wall='RIDGE', at=[9, 5.51], normal=[0, -1],
                         height=1.05, columns=1, controls=[{}])

    def test_switch_mounts_and_whole_plate_has_wall_support(self):
        self.assertEqual(validate(self.bank, self.plan, 5.7), [])
        spec = dict(self.bank, symbols=[1])
        self.assertEqual(mount(spec, [{'x': 9.1, 'y': 5.4}], self.plan), [9.1, 5.51])
        for change in (dict(at=[13.65, 5.51]), dict(height=4.08), dict(at=[9, 5.49]),
                       dict(normal=[1, 0])):
            self.assertTrue(validate(dict(self.bank, **change), self.plan, 5.7), change)

    def test_outlet_extraction_uses_y_face_and_x_opening_interval(self):
        faces = wall_faces(self.plan, .3)
        face, at, distance = place({'x': 9, 'y': 5.48, 'dir': [0, 1]}, faces, 5.7, .044)
        self.assertEqual(face['n'], (0, -1))
        self.assertEqual(at, (9, 5.51))
        self.assertAlmostEqual(distance, .03)
        self.plan['windows_abs'] = [dict(id='DOOR', host='RIDGE',
                                         box=[8.9, 5.51, 5.7, 9.7, 5.71, 7.8])]
        self.assertTrue(validate(self.bank, self.plan, 5.7))
        face, _, _ = place({'x': 9, 'y': 5.48, 'dir': [0, 1]},
                           wall_faces(self.plan, .3), 5.7, .044)
        self.assertIsNone(face)

    def test_existing_x_faces_keep_sloped_profile_support(self):
        wall = copy.deepcopy(self.wall)
        wall.pop('mount_axis')
        faces = fixture_faces(wall)
        self.assertEqual([f['off'] for f in faces], [8.46, 13.66])
        self.assertEqual(faces[0]['profile'], wall['profile'])
        plan = dict(self.plan, profile_walls=[wall])
        bank = dict(self.bank, at=[8.46, 5.61], normal=[-1, 0])
        self.assertEqual(validate(bank, plan, 5.7), [])
        self.assertTrue(validate(dict(bank, height=4.16), plan, 5.7))
