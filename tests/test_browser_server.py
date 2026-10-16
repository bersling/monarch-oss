"""Build/check coordination without a server, subprocesses or browser automation."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import open_viewer
import serve


class BrowserServerTest(unittest.TestCase):
    def test_browser_disconnect_is_quiet_but_other_errors_propagate(self):
        handler = object.__new__(serve.Handler)
        for error in (BrokenPipeError(), ConnectionResetError()):
            with patch('http.server.SimpleHTTPRequestHandler.handle', side_effect=error):
                handler.handle()
        with patch('http.server.SimpleHTTPRequestHandler.handle', side_effect=ValueError('broken response')):
            with self.assertRaisesRegex(ValueError, 'broken response'):
                handler.handle()

    def test_checker_edit_and_restored_mtime_are_watched(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(serve, 'ROOT', Path(folder)):
            root = Path(folder)
            (root/'src').mkdir()
            checker = root/'src/check_new.py'
            checker.write_text('before')
            sources = serve.Sources()
            old = checker.stat()
            checker.write_text('after!')
            os.utime(checker, ns=(old.st_atime_ns, old.st_mtime_ns))
            changed, current = sources.changed()
            self.assertEqual(changed, ['src/check_new.py'])
            sources.accept(current)
            self.assertEqual(sources.changed()[0], [])
            # An identical rewrite is harmless; deleting a checker is a change.
            checker.write_text('after!')
            self.assertEqual(sources.changed()[0], [])
            checker.unlink()
            self.assertEqual(sources.changed()[0], ['src/check_new.py'])

    def test_checker_edit_runs_checks_without_rebuilding_ifc(self):
        with patch.object(serve, 'up_to_date', return_value=True), \
             patch.object(serve, 'run_build') as build, \
             patch('viewer_common.validation_current', return_value=False), \
             patch.object(serve, 'STATE'), patch.object(serve, 'CHECKS') as checks:
            self.assertTrue(serve.refresh(['src/check_new.py']))
            build.assert_not_called()
            checks.start.assert_called_once()
            serve.refresh([], force_build=True)
            build.assert_called_once()

    def test_queued_refresh_reuses_completed_build_and_checks(self):
        state = serve.State()
        with patch.object(serve, 'up_to_date', return_value=True), \
             patch.object(serve, 'run_build') as build, patch.object(serve, 'STATE', state), \
             patch.object(serve, 'CHECKS') as checks, \
             patch('viewer_common.validation_current', return_value=True), \
             patch('viewer_common.validation_hash', return_value='verified'):
            checks.busy.return_value = False
            # Both requests can have been queued while the preceding check ran.
            serve.refresh([])
            serve.refresh([])
            build.assert_not_called()
            checks.start.assert_not_called()
            self.assertEqual(state.status['checks']['state'], 'passed')

    def test_refresh_preserves_a_newer_failure_for_the_same_inputs(self):
        state = serve.State()
        state.update('checks', state='failed', version=state.model_version(), validation='verified', log='new failure')
        with patch.object(serve, 'up_to_date', return_value=True), \
             patch.object(serve, 'STATE', state), patch.object(serve, 'CHECKS') as checks, \
             patch('viewer_common.validation_current', return_value=True), \
             patch('viewer_common.validation_hash', return_value='verified'):
            checks.busy.return_value = False
            serve.refresh([])
            self.assertEqual(state.status['checks']['state'], 'failed')
            self.assertEqual(state.status['checks']['log'], 'new failure')

    def test_request_coalescing_preserves_force_without_making_it_sticky(self):
        state = serve.State()
        state.request_build(force=True)
        for _ in range(5):
            state.request_build(checks=True)
        self.assertEqual(state.take_requests(), (True, True, True))
        self.assertEqual(state.take_requests(), (False, False, False))
        state.request_build(checks=True)
        self.assertEqual(state.take_requests(), (True, False, True))

    def test_refresh_endpoint_does_not_force_but_rebuild_does(self):
        handler = object.__new__(serve.Handler)
        handler.headers = {}
        handler.rfile = io.BytesIO()
        handler.send_json = Mock()
        with patch.object(serve, 'STATE') as state:
            for endpoint, force in (('refresh', False), ('rebuild', True)):
                handler.path = '/api/'+endpoint+'?checks=1'
                handler.do_POST()
                state.request_build.assert_called_with(force=force, checks=True)

    def test_changed_geometry_build_failure_does_not_start_checks(self):
        with patch.object(serve, 'up_to_date', return_value=False), \
             patch.object(serve, 'run_build', return_value=False), \
             patch.object(serve, 'CHECKS') as checks:
            self.assertFalse(serve.refresh(['data/room.json']))
            checks.start.assert_not_called()

    def test_disabled_checks_do_not_keep_an_obsolete_green_status(self):
        with patch.object(serve, 'up_to_date', return_value=True), \
             patch.object(serve, 'STATE') as state, patch.object(serve, 'CHECKS') as checks, \
             patch('viewer_common.validation_current', return_value=False):
            serve.refresh(['src/check_new.py'], checks=False)
            checks.start.assert_not_called()
            self.assertEqual(state.update.call_args.args, ('checks',))
            self.assertEqual(state.update.call_args.kwargs['state'], 'stale')

    def test_completed_process_remains_busy_until_result_is_published(self):
        runner = serve.Checks()
        process = Mock(returncode=0)
        process.poll.return_value = 0
        process.communicate.return_value = ('old checks passed', None)
        runner.process = process
        self.assertTrue(runner.busy())
        with patch.object(serve, 'STATE') as state, \
             patch('viewer_common.validation_hash', return_value='new-checker'), \
             patch('viewer_common.record_validation') as record:
            state.model_version.return_value = 'same-model'
            runner.wait(process, 'same-model', 'old-checker')
            self.assertEqual(state.update.call_args.kwargs['state'], 'stale')
            record.assert_not_called()
        self.assertFalse(runner.busy())

    def test_wait_retries_checks_that_became_stale_after_first_request(self):
        states = ['idle', 'running', 'stale', 'running', 'passed']
        statuses = [{'build': {'state': 'ready', 'version': 'model'},
                     'checks': {'state': s, 'version': 'model'}} for s in states]
        now = [0.]
        def advance(_): now[0] += 3
        with patch.object(open_viewer, 'status', side_effect=statuses), \
             patch.object(serve, 'up_to_date', return_value=True), \
             patch.object(open_viewer, 'validation_current', return_value=True), \
             patch.object(open_viewer.time, 'time', side_effect=lambda: now[0]), \
             patch.object(open_viewer.time, 'sleep', side_effect=advance), \
             patch.object(open_viewer.urllib.request, 'urlopen') as request, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(open_viewer.wait(9999), 0)
            self.assertEqual(request.call_count, 2)
            self.assertTrue(all(c.args[0].full_url.endswith('/api/refresh?checks=1') for c in request.call_args_list))

class ServerUpgradeTest(unittest.TestCase):
    def test_missing_state_file_discovers_default_port_without_spawning(self):
        with patch.object(open_viewer, 'read_json', return_value={}), \
             patch.object(serve, 'default_port', return_value=8844), \
             patch.object(open_viewer, 'status', return_value={'root': str(open_viewer.ROOT)}) as status:
            self.assertEqual(open_viewer.running(), 8844)
            status.assert_called_once_with(8844)

    def test_other_checkout_on_same_port_is_not_accepted(self):
        response = Mock()
        response.__enter__ = Mock(return_value=io.StringIO('{"root":"/another/checkout"}'))
        response.__exit__ = Mock(return_value=False)
        with patch.object(open_viewer.urllib.request, 'urlopen', return_value=response):
            self.assertIsNone(open_viewer.status(8844))
        with patch.object(open_viewer, 'status', return_value=None), \
             patch.object(open_viewer.os, 'kill') as kill:
            open_viewer.terminate(8844)
            kill.assert_not_called()

    def test_stale_code_restarts_but_compatible_server_restores_missing_record(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)/'web-server.json'
            current = {'server': {'fingerprint': 'old', 'pid': 12345,
                                 'watch': True, 'checks': True, 'snapdir': '/custom/snaps'}}
            with patch.object(open_viewer, 'state_dir', return_value=Path(folder)), \
                 patch.object(serve, 'state_file', return_value=state), \
                 patch.object(serve, 'runtime_fingerprint', return_value='new'), \
                 patch.object(open_viewer, 'running', return_value=8844), \
                 patch.object(open_viewer, 'status', return_value=current), \
                 patch.object(open_viewer, 'terminate') as terminate, \
                 patch.object(open_viewer, 'start', return_value=8844) as start, \
                 contextlib.redirect_stdout(io.StringIO()):
                # Snapshots preserve the capabilities of an existing live server.
                self.assertEqual(open_viewer.ensure_running(watch=False, checks=False), 8844)
                terminate.assert_called_once_with(8844)
                start.assert_called_once_with(8844, True, True, '/custom/snaps')
                start.reset_mock(); terminate.reset_mock()
                current['server']['fingerprint'] = 'new'
                self.assertEqual(open_viewer.ensure_running(), 8844)
                start.assert_not_called(); terminate.assert_not_called()
                self.assertEqual(open_viewer.read_json(state)['pid'], 12345)
                # Normal ./open.sh upgrades a snapshot-only server to watch/check.
                current['server']['watch'] = current['server']['checks'] = False
                self.assertEqual(open_viewer.ensure_running(), 8844)
                start.assert_called_once_with(8844, True, True, '/custom/snaps')

    def test_legacy_pid_requires_matching_listener_and_absolute_script(self):
        result = Mock(stdout='456\n')
        wrong = Mock(stdout='python /another/checkout/src/serve.py --port 8844\n')
        correct = Mock(stdout=f'python {open_viewer.ROOT}/src/serve.py --port 8844\n')
        with patch.object(open_viewer.subprocess, 'run', side_effect=[result, wrong]):
            self.assertIsNone(open_viewer.legacy_pid(8844))
        with patch.object(open_viewer.subprocess, 'run', side_effect=[result, correct]):
            self.assertEqual(open_viewer.legacy_pid(8844), 456)
        with patch.object(open_viewer.subprocess, 'run', return_value=Mock(stdout='456\n789\n')):
            self.assertIsNone(open_viewer.legacy_pid(8844))

    def test_replaced_pid_is_never_signalled(self):
        with patch.object(open_viewer, 'status', side_effect=[{'server': {'pid': 456}}, {'server': {'pid': 789}}]), \
             patch.object(open_viewer.os, 'kill') as kill:
            with self.assertRaisesRegex(SystemExit, 'changed during restart'):
                open_viewer.terminate(8844)
            kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
