from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from snap_web import parse_views


class SnapshotArgumentsTest(unittest.TestCase):
    def test_room_named_camera_is_not_an_element_selector(self):
        for name in ('room', 'plan', 'frame', 'detail'):
            self.assertEqual(parse_views([f'{name}=1,2,3,4,5,6', 'fov=75']),
                             [dict(name=name, cam=[1,2,3], target=[4,5,6], fov='75')])

    def test_room_and_plan_selectors_still_start_views(self):
        self.assertEqual(parse_views(['room=KIDS_DG_', 'plan=DG']),
                         [dict(name='room_KIDS_DG', frame='KIDS_DG_'), dict(name='plan_DG', plan='DG')])

    def test_incomplete_room_coordinates_fail_early(self):
        with self.assertRaisesRegex(SystemExit, 'expected NAME=cam_x'):
            parse_views(['room=1,2,3'])
