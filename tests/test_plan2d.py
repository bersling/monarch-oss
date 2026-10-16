"""Check the plotted paths, where a wrong signed offset can mislead a layout."""
import contextlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import plan2d


class SketchOpeningsTest(unittest.TestCase):
    def test_reversed_wall_openings_and_storey_aliases(self):
        plan = {
            'exterior_walls': [
                {'id': 'E', 'start': [9, 10], 'end': [9, 0], 'thickness': .4},
                {'id': 'S', 'start': [10, 1], 'end': [0, 1], 'thickness': .4},
            ],
            'windows': [dict(wall_id=w, offset_along_wall=6, width=1.2) for w in ('E', 'S')],
            'doors': [dict(wall_id=w, offset_along_wall=2, width=.9) for w in ('E', 'S')],
        }
        drawings = []
        def capture(page, **kwargs):
            drawings.extend(page.get_drawings())
            return SimpleNamespace(width=1, height=1, save=lambda _: None)
        for storey in ('UF', 'upper_floor', 'upper_floor.json'):
            with self.subTest(storey=storey):
                drawings.clear()
                with patch('products.load_plan', return_value=plan), \
                     patch.object(plan2d.pymupdf.Page, 'get_pixmap', capture), \
                     contextlib.redirect_stdout(io.StringIO()):
                    plan2d.main([storey, '0,0,10,10', 'unused.png', '--no-furniture'])
                def rectangles(fill):
                    paths = [p for p in drawings if p['fill'] is not None and
                             all(abs(a-b)<1e-5 for a,b in zip(p['fill'],fill))]
                    result=[]
                    for path in paths:
                        for op, rect, *_ in path['items']:
                            if op=='re':
                                result.append(tuple(round(v,3) for v in (
                                    (rect.x0-plan2d.M)/plan2d.K,
                                    10-(rect.y1-plan2d.M)/plan2d.K,
                                    (rect.x1-plan2d.M)/plan2d.K,
                                    10-(rect.y0-plan2d.M)/plan2d.K)))
                    return result
                self.assertEqual(rectangles((.8,.9,1)), [(8.8,2.8,9.2,4), (2.8,.8,4,1.2)])
                self.assertEqual(rectangles((1,1,1)), [(8.8,7.1,9.2,8), (7.1,.8,8,1.2)])
