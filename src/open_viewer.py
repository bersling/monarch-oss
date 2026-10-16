"""./open.sh: start (or reuse) this checkout's browser viewer and show the model.

    ./open.sh              ensure the server runs, open a browser tab unless one is connected
    ./open.sh --wait       also block until the model includes every source edit made so far
                           and its checks finished; prints the result, exit 1 if either failed
    ./open.sh --no-browser --wait   the same without touching the browser (agents in CI)
    ./open.sh --wait-build          return as soon as the model is built (checks keep running in
                                    the background; take screenshots meanwhile, then --wait before landing)
    ./open.sh --stop       stop this checkout's server

The server (src/serve.py) watches data/, assets/ and the builder sources and rebuilds on
every save, so after an edit you normally do nothing: the open tab hot-reloads.
"""
import json
import fcntl
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
import serve  # noqa: E402
from viewer_common import atomic_json, read_json, state_dir, validation_current, validation_hash  # noqa: E402


def status(port, timeout=1.0):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status', timeout=timeout) as r:
            data = json.load(r)
        return data if data.get('root') == str(ROOT) else None
    except (OSError, ValueError):
        return None


def running():
    info = read_json(serve.state_file())
    # A live server can outlast its temporary state file. Discover it by its
    # normal port before spawning a second process that cannot bind there.
    for port in dict.fromkeys([info.get('port'), serve.default_port()]):
        if port and status(port):
            return port
    return None


def start(port=None, watch=True, checks=True, snapdir=None):
    port = port or serve.default_port()
    log = state_dir(ROOT) / 'web-server.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(ROOT / 'src/serve.py'), '--port', str(port)]
    if not watch:
        command.append('--no-watch')
    if not checks:
        command.append('--no-checks')
    if snapdir:
        command.extend(['--snapdir', str(snapdir)])
    with log.open('a') as stream:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(100):
        if process.poll() is not None:
            raise SystemExit(f'viewer server exited ({process.returncode}); see {log}')
        if status(port):
            return port
        time.sleep(0.1)
    raise SystemExit(f'viewer server did not start; see {log}')


def legacy_pid(port):
    """Recover a pre-fingerprint server only after verifying its listener and command.

    Modern servers report their own PID. This macOS/Unix fallback handles a lost
    old state file without trusting a potentially reused PID in that file.
    """
    try:
        result = subprocess.run(['lsof', '-t', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN'],
                                capture_output=True, text=True, timeout=3)
        pids = set(result.stdout.split())
        if len(pids) != 1:
            return None
        pid = int(pids.pop())
        command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='],
                                 capture_output=True, text=True, timeout=3)
        if str(ROOT / 'src/serve.py') in shlex.split(command.stdout):
            return pid
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def terminate(port):
    current = status(port)
    if not current:
        return
    pid = current.get('server', {}).get('pid') or legacy_pid(port)
    if not isinstance(pid, int) or pid <= 1 or pid == os.getpid():
        raise SystemExit(f'Cannot identify the checkout-owned viewer on port {port}; no process was stopped.')
    # Recheck ownership immediately before signalling; never stop a different
    # checkout merely because it happens to use the same default-port hash.
    latest = status(port)
    if not latest:
        return
    reported = latest.get('server', {}).get('pid')
    if (reported is not None and reported != pid) or (reported is None and legacy_pid(port) != pid):
        raise SystemExit('viewer process changed during restart; retry ./open.sh')
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(100):
        if status(port) is None:
            return
        time.sleep(.1)
    raise SystemExit(f'viewer process {pid} did not stop; no replacement was started')


def ensure_running(watch=True, checks=True):
    """Reuse compatible code/capabilities, otherwise replace only this checkout's server."""
    directory = state_dir(ROOT)
    directory.mkdir(parents=True, exist_ok=True)
    # Concurrent ./open.sh and screenshots must not both spawn or replace a server.
    with (directory / 'web-launch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        port = running()
        snapdir = None
        if port:
            current = status(port)
            if current is None:
                return start(port, watch, checks)
            runtime = current.get('server', {})
            compatible = (runtime.get('fingerprint') == serve.runtime_fingerprint(ROOT)
                          and (not watch or runtime.get('watch'))
                          and (not checks or runtime.get('checks')))
            if compatible:
                info = {'pid': runtime['pid'], 'port': port, 'root': str(ROOT),
                        'snapdir': runtime['snapdir']}
                if read_json(serve.state_file()) != info:
                    atomic_json(serve.state_file(), info)
                return port
            info = read_json(serve.state_file())
            snapdir = runtime.get('snapdir') or (info.get('snapdir') if info.get('root') == str(ROOT) else None)
            # A screenshot must not downgrade an existing watched/checked server.
            watch = watch or runtime.get('watch', True)
            checks = checks or runtime.get('checks', True)
            print('restarting viewer for updated server code or live-check capabilities', flush=True)
            terminate(port)
        return start(port, watch, checks, snapdir)


def wait(port, build_only=False):
    """Block until the model includes every source edit made so far and (unless build_only) its checks ran."""
    t0, last, last_request = time.time(), None, None
    while True:
        s = status(port, 5)
        if s is None:
            raise SystemExit('viewer server went away')
        b, c = s['build'], s['checks']
        line = f"build {b['state']}, checks {c['state']}"
        if line != last:
            print(line, flush=True)
            last = line
        if b['state'] != 'building':
            if b['state'] == 'failed' and not serve.up_to_date(ROOT):
                print(b.get('log', ''))
                return 1
            if serve.up_to_date(ROOT) and build_only:
                print(f"model up to date ({b.get('seconds', 0) or 0} s build); checks {c['state']} in the background")
                return 0
            if serve.up_to_date(ROOT):
                if c['state'] == 'running':
                    pass
                elif (c['state'] == 'failed' and c.get('version') == b.get('version')
                      and c.get('validation') == validation_hash(ROOT)):
                    print(c.get('log', ''))
                    return 1
                elif c['state'] == 'passed' and c.get('version') == b.get('version') and validation_current(ROOT):
                    print(f"model up to date ({b.get('seconds', 0) or 0} s build); checks {c['state']}"
                          + (f": {c['summary']}" if c.get('summary') else ''))
                    if c['state'] == 'failed':
                        print(c.get('log', ''))
                    return 0
                elif last_request is None or time.time() - last_request > 2:
                    # Idle/stale checks are not proof. Request current checks,
                    # including servers originally started by snap_web --no-checks.
                    urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/api/refresh?checks=1', method='POST'))
                    last_request = time.time()
            elif time.time() - t0 > 2 and (last_request is None or time.time() - last_request > 2):
                # Edits the watcher cannot see (made before it started, or a branch switch).
                urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/api/refresh?checks=1', method='POST'))
                last_request = time.time()
        if time.time() - t0 > 900:
            raise SystemExit('timed out after 15 min')
        time.sleep(0.5)


def stop():
    port = running()
    if port:
        terminate(port)
        print(f'stopped viewer server on port {port}')
    else:
        print('no viewer server running for this checkout')


def main(args):
    if '--stop' in args:
        stop()
        return 0
    port = ensure_running()
    url = f'http://127.0.0.1:{port}/'
    s = status(port)
    if '--no-browser' not in args and not s.get('viewers'):
        subprocess.run(['open', url], check=False)
    print(f'viewer: {url}  (log {state_dir(ROOT) / "web-server.log"})')
    if '--wait-build' in args:
        return wait(port, build_only=True)
    if '--wait' in args:
        return wait(port)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
