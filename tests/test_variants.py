import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import variant


class VariantTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.group = {'target': 'floor.json', 'active': 'one', 'id_prefixes': ['ROOM_'],
                      'variants': {'one': {'file': 'one.json'}, 'empty': {'file': 'empty.json'}}}
        self.index = {'groups': {'room': self.group}}
        (self.data / 'one.json').write_text(json.dumps({'furniture': [{'id': 'ROOM_BED', 'props': {'Product': 'Selected bed'}}]}))
        (self.data / 'empty.json').write_text('{}')
        self.addCleanup(patch.stopall)
        patch.object(variant, 'DATA', self.data).start()
        patch.object(variant, 'REGISTRY', self.data / 'variants.json').start()
        (self.data / 'variants.json').write_text(json.dumps(self.index))

    def test_switch_is_pure_and_removes_prefix_orphans_from_new_sections(self):
        target = {'furniture': [{'id': 'ROOM_BED'}, {'id': 'OTHER'}],
                  'sanitary': [{'id': 'ROOM_ORPHAN'}], 'walls': [{'id': 'WALL'}]}
        before = copy.deepcopy(target)
        result = variant.apply_variant(target, self.group, {})
        self.assertEqual(target, before)
        self.assertEqual(result['furniture'], [{'id': 'OTHER'}])
        self.assertEqual(result['sanitary'], [])
        self.assertEqual(result['walls'], before['walls'])

    def test_save_captures_new_prefix_ids_and_keeps_other_room(self):
        (self.data / 'floor.json').write_text(json.dumps({'furniture': [
            {'id': 'ROOM_BED'}, {'id': 'ROOM_NEW'}, {'id': 'OTHER'}], 'sanitary': [{'id': 'ROOM_NEW_SECTION'}]}))
        variant.cmd_save('room', 'one')
        saved = json.loads((self.data / 'one.json').read_text())
        self.assertEqual(saved['furniture'], [{'id': 'ROOM_BED'}, {'id': 'ROOM_NEW'}])
        self.assertEqual(saved['sanitary'], [{'id': 'ROOM_NEW_SECTION'}])

    def test_property_change_and_orphan_detected_as_drift(self):
        (self.data / 'floor.json').write_text(json.dumps({'furniture': [
            {'id': 'ROOM_BED', 'props': {'Product': 'Wrong bed'}}, {'id': 'ROOM_ORPHAN'}]}))
        error, = variant.active_drift(self.index)
        self.assertIn('ROOM_BED', error)
        self.assertIn('ROOM_ORPHAN', error)

    def test_duplicate_objects_do_not_disappear_in_comparison(self):
        saved = json.loads((self.data / 'one.json').read_text())
        saved['furniture'] *= 2
        (self.data / 'floor.json').write_text(json.dumps(saved))
        self.assertTrue(any('duplicate IDs' in e for e in variant.active_drift(self.index)))

    def test_reload_preserves_existing_order_and_removes_orphans(self):
        target = {'furniture': [{'id': 'OTHER_A'}, {'id': 'ROOM_BED', 'old': True},
                                {'id': 'OTHER_B'}, {'id': 'ROOM_ORPHAN'}, {'id': 'OTHER_C'}]}
        chosen = {'furniture': [{'id': 'ROOM_NEW'}, {'id': 'ROOM_BED', 'new': True}]}
        result = variant.apply_variant(target, self.group, chosen)
        self.assertEqual(result['furniture'], [
            {'id': 'OTHER_A'}, {'id': 'ROOM_BED', 'new': True}, {'id': 'OTHER_B'},
            {'id': 'OTHER_C'}, {'id': 'ROOM_NEW'}])
        self.assertEqual(variant.apply_variant(result, self.group, chosen), result)
        self.assertEqual(chosen['furniture'][0], {'id': 'ROOM_NEW'})

    def test_room_comparison_ignores_draw_order_but_detects_changes_and_duplicates(self):
        bed = {'id': 'ROOM_BED', 'props': {'Product': 'Selected bed'}}
        chair = {'id': 'ROOM_CHAIR', 'at': [1, 2, 0]}
        self.assertTrue(variant.same_items([bed, chair], [chair, bed]))
        self.assertFalse(variant.same_items([bed, chair], [chair, bed, bed]))
        self.assertFalse(variant.same_items([bed, bed, chair], [chair, bed]))
        moved = dict(chair, at=[1.1, 2, 0])
        self.assertFalse(variant.same_items([bed, moved], [chair, bed]))
        self.assertFalse(variant.same_items([chair], [chair, bed]))
