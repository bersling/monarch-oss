"""Geometric edge cases in actual-IFC source overlays."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from plan_compare import section_segments, to_pdf, projected_segments


class PlanCompareTest(unittest.TestCase):
    def test_projection_keeps_holes_and_concave_edges(self):
        def project(cells):
            vertices=[];faces=[]
            for x,y in cells:
                start=len(vertices)
                vertices.extend([[x,y,0], [x+1,y,0], [x+1,y+1,1], [x,y+1,1]])
                faces.extend([[start,start+1,start+2], [start,start+2,start+3]])
            return projected_segments(np.array(vertices,float),np.array(faces))
        ring=project([(x,y) for x in range(3) for y in range(3) if (x,y)!=(1,1)])
        self.assertAlmostEqual(sum(np.linalg.norm(b-a) for a,b in ring),16.)
        # This re-entrant corner would disappear in a convex hull approximation.
        concave=project([(0,0),(1,0),(0,1)])
        self.assertTrue(any(np.allclose(p,[1,1]) for p in concave.reshape(-1,2)))
        self.assertAlmostEqual(sum(np.linalg.norm(b-a) for a,b in concave),8.)

    def test_section_through_vertices_and_horizontal_edges(self):
        vertices = np.array([[0.,0.,0.], [2.,0.,2.], [0.,2.,2.],
                             [2.,2.,0.]])
        faces = np.array([[0,1,2], [1,2,3]])
        cuts = section_segments(vertices, faces, 1.)
        self.assertEqual(cuts.shape, (2,2,2))
        np.testing.assert_allclose(cuts[0], [[1,0], [0,1]])
        np.testing.assert_allclose(cuts[1], [[1,2], [2,1]])
        self.assertEqual(section_segments(vertices, faces, 2.).shape, (0,2,2))
        # A vertex on the cutting plane must not create a zero-length stroke.
        self.assertEqual(section_segments(vertices, faces, 0.).shape, (0,2,2))
        horizontal_edge = np.array([[0.,0.,1.], [2.,0.,1.], [0.,2.,2.]])
        edge = section_segments(horizontal_edge, np.array([[0,1,2]]), 1.)
        np.testing.assert_allclose(edge, [[[2,0],[0,0]]])

    def test_registration_scale_and_north_axis(self):
        registration = {'anchor_model_xy': [.25,1.86],
                        'anchor_pdf_pt': [692.4597,1520.1294],
                        'points_per_metre': 72 / .0254 / 50}
        result = to_pdf([[.25,1.86], [1.25,2.86]], registration)
        np.testing.assert_allclose(result[0], registration['anchor_pdf_pt'])
        np.testing.assert_allclose(result[1]-result[0], [56.6929133858,-56.6929133858])


if __name__ == '__main__':
    unittest.main()
