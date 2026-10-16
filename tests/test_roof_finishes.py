import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from roof_finishes import split_lining


class RoofLiningTest(unittest.TestCase):
    def test_both_slopes_preserve_envelope_and_normal_thickness(self):
        for slope in (-1., 0., .5, 1.):
            profile = [[0., 4.], [2., 4. + 2 * slope],
                       [2., 4.5 + 2 * slope], [0., 4.5]]
            lining, core = split_lining(profile, .035)
            self.assertEqual(lining[:2], profile[:2])
            self.assertEqual(core[2:], profile[2:])
            self.assertEqual(lining[2:][::-1], core[:2])
            self.assertAlmostEqual((core[0][1] - lining[0][1]) / math.hypot(1, slope), .035)

    def test_rejects_invalid_or_consumed_envelope(self):
        profile = [[0, 0], [1, 1], [1, 1.2], [0, .2]]
        for thickness in (-.1, 0, .2, float('nan')):
            with self.assertRaises(ValueError):
                split_lining(profile, thickness)
        with self.assertRaises(ValueError):
            split_lining([[0, 0], [1, 1], [2, 1.2], [0, .2]], .035)
