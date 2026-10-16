"""Launcher regression without starting or terminating any real Blender processes."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import viewer
from viewer_common import MODEL, atomic_json, record_build, record_validation, source_hash


class ViewerLauncherTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / 'state'
        for folder in ('src', 'data', 'assets', 'generated'):
            (self.root / folder).mkdir()
        for name in ('build.sh', 'check.sh', 'src/check_model.py', 'src/check_cellar_services.py', 'src/check_stair_finishes.py', 'src/check_dg_site_measurements.py', 'src/dg_site_geometry.py', 'src/check_dg_foyer.py', 'src/check_dg_floor_build_up.py', 'src/check_terrain_cutouts.py', 'src/check_entrance_wardrobe.py', 'src/check_kids_dg.py', 'src/check_furnishing_finishes.py', 'src/joinery_finishes.py', 'src/build_model.py', 'src/floor_finishes.py', 'src/stair_finishes.py', 'src/cellar_services.py', 'src/light_switches.py', 'src/terrain_cutouts.py', 'src/viewer_common.py', 'src/check_master_inga.py', 'src/check_dg_southeast.py', 'src/check_office_products.py', 'src/check_office_kallax.py'):
            (self.root / name).write_text('fixture')
        (self.root / 'data/room.json').write_text('{}')
        self.model = self.root / 'generated' / MODEL
        self.model.write_text('fixture IFC')
        record_build(self.root, source_hash(self.root))
        record_validation(self.root)
        self.addCleanup(patch.stopall)
        patch.object(viewer, 'ROOT', self.root).start()
        patch.object(viewer, 'state_dir', return_value=self.state).start()
        self.alive = patch.object(viewer, 'alive', side_effect=lambda pid: pid == 1234).start()
        self.run = patch.object(viewer.subprocess, 'run').start()
        self.launch = patch.object(viewer.subprocess, 'Popen').start()
        self.launch.return_value.pid = 1234
        self.legacy = patch.object(viewer, 'legacy_viewers', return_value=[]).start()
        atomic_json(self.state / 'status.json', {'pid': 1234, 'state': 'ready'})

    def call(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return viewer.main(list(args))

    def test_unchanged_build_and_viewer_are_noops(self):
        from viewer_common import file_hash
        atomic_json(self.state / 'status.json', {'pid': 1234, 'state': 'ready', 'digest': file_hash(self.model)})
        self.assertEqual(self.call(), 0)
        self.run.assert_not_called()
        self.launch.assert_not_called()
        self.assertFalse((self.state / 'request.json').exists())

    def test_changed_room_checker_invalidates_validation_only(self):
        (self.root / 'src/check_office_kallax.py').write_text('updated door clearance check')
        self.call()
        self.run.assert_not_called()
        self.launch.assert_called_once()
        self.assertIn(str(self.root / 'src/background_checks.py'), self.launch.call_args.args[0])

    def test_no_build_queues_immutable_snapshot(self):
        (self.root / 'data/room.json').write_text('{"changed":true}')
        self.call('--no-build')
        self.run.assert_not_called()
        self.launch.assert_not_called()
        request = json.loads((self.state / 'request.json').read_text())
        snapshot = Path(request['path'])
        self.assertEqual(snapshot.read_bytes(), self.model.read_bytes())
        self.assertEqual(snapshot.stat().st_mode & 0o222, 0)

    def test_changed_sources_build_the_ifc_and_check_in_the_background(self):
        (self.root / 'data/room.json').write_text('{"changed":true}')
        self.call()
        self.run.assert_called_once_with([viewer.sys.executable, str(self.root / 'src/build_model.py')], cwd=self.root, check=True)
        self.launch.assert_called_once()   # detached check.sh runner, not a Blender window
        self.assertIn(str(self.root / 'src/background_checks.py'), self.launch.call_args.args[0])
        self.assertTrue(self.launch.call_args.kwargs['start_new_session'])

    def test_forced_build_runs_everything_in_the_foreground(self):
        self.call('--build')
        self.run.assert_called_once_with([str(self.root / 'build.sh')], cwd=self.root, check=True)
        self.launch.assert_not_called()

    def test_unvalidated_build_only_starts_background_checks(self):
        record_build(self.root, source_hash(self.root))  # Build succeeded; checks haven't.
        self.call()
        self.run.assert_not_called()
        self.launch.assert_called_once()
        self.assertIn(str(self.root / 'src/background_checks.py'), self.launch.call_args.args[0])

    def test_running_checks_of_the_same_build_are_not_duplicated(self):
        from viewer_common import file_hash
        record_build(self.root, source_hash(self.root))
        atomic_json(self.state / 'checks.json', {'pid': 1234, 'state': 'running', 'sources': source_hash(self.root), 'ifc': file_hash(self.model)})
        self.call()
        self.launch.assert_not_called()

    def test_failed_checks_are_reported_on_the_next_open(self):
        from viewer_common import file_hash
        atomic_json(self.state / 'checks.json', {'pid': 99, 'state': 'failed', 'ifc': file_hash(self.model), 'log': 'x.log', 'tail': 'AssertionError: boom'})
        atomic_json(self.state / 'status.json', {'pid': 1234, 'state': 'ready', 'digest': file_hash(self.model)})
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(viewer.main([]), 0)
        self.assertIn('CHECKS FAILED for this build', err.getvalue())
        self.assertIn('boom', err.getvalue())

    def test_legacy_window_does_not_prevent_launch_and_next_call_reuses_new_viewer(self):
        atomic_json(self.state / 'status.json', {})
        self.legacy.return_value = ['123 Blender --python-expr ... --monarch-viewer']
        self.call('--no-build')
        self.launch.assert_called_once()
        command = self.launch.call_args.args[0]
        self.assertIn(str(self.root / 'src/blender_bridge.py'), command)
        self.assertTrue((self.state / 'request.json').exists())
        self.assertEqual(json.loads((self.state / 'launch.json').read_text())['pid'], 1234)
        # Before the listener has even registered, another call must reuse this process.
        self.call('--no-build')
        self.launch.assert_called_once()
        self.run.assert_not_called()  # No process-termination command.

    def test_starting_viewer_is_not_duplicated(self):
        atomic_json(self.state / 'status.json', {})
        atomic_json(self.state / 'launch.json', {'pid': 1234})
        self.call('--no-build')
        self.launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
