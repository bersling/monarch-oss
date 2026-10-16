"""Locked build/check runner shared by shell entrypoints and the browser server."""
from pathlib import Path
import subprocess
import sys

from model_lock import Lease
from viewer_common import validation_hash

ROOT = Path(__file__).resolve().parents[1]


def main(args):
    if not args or args[0] not in ('build', 'build-only', 'check'):
        raise SystemExit('Usage: pipeline.py build | build-only | check [check names]')
    mode, words = args[0], args[1:]
    with Lease(ROOT) as lease:
        def run(script, arguments=()):
            subprocess.run([sys.executable, str(ROOT / 'src' / script), *arguments], cwd=ROOT,
                           check=True, **lease.subprocess_options())
        if mode.startswith('build'):
            for script in ('build_model.py', 'geometry_cache.py', 'export_glb.py'):
                run(script)
        if mode != 'build-only':
            before = validation_hash(ROOT)
            run('run_checks.py', words)
            if not words:
                if before != validation_hash(ROOT):
                    raise SystemExit('Check code changed during validation; run the checks again.')
                run('viewer_common.py', ['--validated'])
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
