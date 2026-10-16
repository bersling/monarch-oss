"""Build/check leases serialize writers and survive subprocess inheritance."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from model_lock import Lease

SRC = str(Path(__file__).resolve().parents[1] / 'src')
PROBE = """
import sys
sys.path.insert(0, sys.argv[1])
from model_lock import Lease
print('started', flush=True)
with Lease(sys.argv[2]):
    print('acquired', flush=True)
"""


class ModelLockTest(unittest.TestCase):
    def test_child_inherits_and_independent_writer_waits(self):
        with tempfile.TemporaryDirectory() as root:
            with Lease(root) as lease:
                with Lease(root):
                    child = subprocess.run([sys.executable, '-c', PROBE, SRC, root],
                                           capture_output=True, text=True, timeout=5,
                                           **lease.subprocess_options())
                    self.assertEqual(child.returncode, 0, child.stderr)
                    self.assertEqual(child.stdout, 'started\nacquired\n')
                contender = subprocess.Popen([sys.executable, '-c', PROBE, SRC, root],
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(contender.stdout.readline(), 'started\n')
                    with self.assertRaises(subprocess.TimeoutExpired):
                        contender.communicate(timeout=0.15)
                except BaseException:
                    contender.kill()
                    contender.communicate()
                    raise
            try:
                stdout, stderr = contender.communicate(timeout=5)
                self.assertEqual(contender.returncode, 0, stderr)
                self.assertEqual(stdout, 'acquired\n')
            finally:
                if contender.poll() is None:
                    contender.kill()
                    contender.communicate()

    def test_exception_releases_lease(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(RuntimeError):
                with Lease(root):
                    raise RuntimeError('failed build')
            child = subprocess.run([sys.executable, '-c', PROBE, SRC, root],
                                   capture_output=True, text=True, timeout=5)
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertIn('acquired', child.stdout)
