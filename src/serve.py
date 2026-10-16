"""Browser viewer dev server: watch sources, rebuild, push hot reloads.

    ./open.sh                         start (or reuse) this checkout's server and open the browser
    uv run python src/serve.py        run in the foreground (--port N, --no-watch, --no-checks)

Each checkout (main or worktree) gets its own port, derived from its path, so
parallel sessions never share a viewer. The build runs in a subprocess: a failing
or crashing build only turns the viewer's status red, the last good model stays up.

HTTP API (all JSON, consumed by web/viewer.js and src/snap_web.py):
  GET  /                      web/index.html          GET /web/*   static viewer files
  GET  /generated/model.glb   GET /generated/elements.json
  GET  /files/<path>          read-only plans/, docs/, assets/, renders/ (PDF links in the panel)
  GET  /events                Server-Sent Events: a status object on connect and on every change
  GET  /api/status            the same status object + "viewers" (open tabs) and "root"
  POST /api/rebuild           force a rebuild
  POST /api/refresh           make current, reusing a valid build/check result
  POST /api/log               viewer diagnostics -> <state_dir>/web-client.log
  POST /api/snap?name=N       body: PNG bytes from the viewer (snap mode) -> <snapdir>/N.png

status = {"build":  {"state": "idle|building|ready|failed", "version": str, "seconds": float,
                     "changed": [paths], "log": str, "finished": ts},
          "checks": {"state": "idle|running|passed|failed|stale", "summary": str, "log": str,
                     "version": str}}
"""
import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import queue
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import html
import documents

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'src'
WEB = ROOT / 'web'
GENERATED = ROOT / 'generated'
PYTHON = sys.executable
WATCH_DIRS = ['data', 'assets']
PIPELINE = ('geometry_cache.py', 'export_glb.py')      # build steps after build_model.py
PUBLIC_DIRS = ('plans', 'docs', 'assets', 'renders')


def runtime_fingerprint(root=ROOT):
    """Code loaded by this server process, separate from the generated model."""
    from viewer_common import file_hash
    digest = hashlib.sha256()
    for name in ('serve.py', 'documents.py', 'viewer_common.py', 'model_lock.py'):
        digest.update(name.encode())
        digest.update(file_hash(Path(root) / 'src' / name).encode())
    return digest.hexdigest()


# Capture once: recomputing this in /api/status would hide an obsolete process.
RUNTIME_FINGERPRINT = runtime_fingerprint()


def default_port(root=ROOT):
    return 8700 + int(hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest(), 16) % 200


def state_file(root=ROOT):
    sys.path.insert(0, str(SRC))
    from viewer_common import state_dir
    return state_dir(root) / 'web-server.json'


def snapshot(root=None):
    """File signatures for build and validation inputs, including replaced files."""
    root = Path(root or ROOT)
    files = {}
    def remember(p):
        try:
            s = p.stat()
            if p.is_file():
                files[str(p.relative_to(root))] = (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        except FileNotFoundError:
            pass                         # an editor may rename a file during polling
    for d in WATCH_DIRS:
        for p in (root / d).rglob('*'):
            if not p.name.startswith('.'):
                remember(p)
    # validation_hash includes all local Python helpers and both wrappers.
    # Checker edits must rerun checks even when no IFC generation is needed.
    for p in [*sorted((root / 'src').glob('*.py')), root / 'build.sh', root / 'check.sh']:
        remember(p)
    from viewer_common import read_json
    for doc in read_json(root / 'data/plan_documents.json').get('documents', []):
        p = root / doc['path']
        remember(p)
    return files


def up_to_date(root=ROOT):
    """The viewer model reflects the current sources: same test as build.sh's build stamp
    (content hash), plus geometry cache and model.glb at least as new as the IFC."""
    from viewer_common import MODEL, file_hash, read_json, source_hash
    ifc, glb, cache = root / 'generated' / MODEL, root / 'generated/model.glb', root / 'generated/elements.json'
    if not (ifc.exists() and glb.exists() and cache.exists()):
        return False
    stamp = read_json(root / 'generated/viewer-build.json')
    if stamp.get('sources') != source_hash(root) or stamp.get('ifc') != file_hash(ifc):
        return False
    if read_json(cache).get('build_id') != stamp.get('ifc'):
        return False
    return glb.stat().st_mtime >= ifc.stat().st_mtime and cache.stat().st_mtime >= ifc.stat().st_mtime


class State:
    def __init__(self):
        self.lock = threading.Condition()
        self.status = {'build': {'state': 'idle', 'version': self.model_version(), 'changed': [], 'log': ''},
                       'checks': {'state': 'idle', 'summary': '', 'log': ''}}
        self.listeners = []
        self.rebuild = threading.Event()
        self.force_requested = False
        self.check_requested = False
        self.snapdir = GENERATED / 'snaps'
        self.watch = True
        self.auto_checks = True

    def request_build(self, force=False, checks=False):
        with self.lock:
            self.force_requested |= force
            self.check_requested |= checks
            self.rebuild.set()

    def take_requests(self):
        with self.lock:
            result = self.rebuild.is_set(), self.force_requested, self.check_requested
            self.rebuild.clear()
            self.force_requested = self.check_requested = False
            return result

    @staticmethod
    def model_version():
        glb = GENERATED / 'model.glb'
        return str(glb.stat().st_mtime_ns) if glb.exists() else ''

    def update(self, part, **values):
        with self.lock:
            self.status[part].update(values)
            message = json.dumps(self.status)
            for q in list(self.listeners):
                q.put(message)

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.listeners.append(q)
            q.put(json.dumps(self.status))
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)


STATE = State()


def tail(text, lines=40):
    return '\n'.join(text.rstrip().splitlines()[-lines:])


def run_build(changed):
    """build_model -> geometry_cache -> export_glb, each a subprocess. True if it succeeded."""
    STATE.update('build', state='building', changed=changed[:20], started=time.time(), log='')
    t0, out = time.time(), []
    for step in ('pipeline.py',):
        result = subprocess.run([PYTHON, str(SRC / step), 'build-only'], cwd=ROOT, capture_output=True, text=True)
        out.append(f'$ {step}\n{result.stdout}{result.stderr}')
        if result.returncode != 0:
            STATE.update('build', state='failed', seconds=round(time.time() - t0, 1), log=tail('\n'.join(out)),
                         finished=time.time())
            return False
    if not up_to_date():
        STATE.update('build', state='failed', log='Sources changed during the build; retrying.', finished=time.time())
        STATE.rebuild.set()
        return False
    STATE.update('build', state='ready', version=STATE.model_version(), seconds=round(time.time() - t0, 1),
                 log=tail('\n'.join(out), 12), finished=time.time())
    return True


class Checks:
    """One background run_checks.py at a time, including publishing its result."""

    def __init__(self):
        self.process = None
        self.lock = threading.Lock()

    def busy(self):
        with self.lock:
            return self.process is not None

    def start(self):
        with self.lock:
            if self.process is not None:
                return
            version = STATE.model_version()
            from viewer_common import validation_hash
            validation = validation_hash(ROOT)
            STATE.update('checks', state='running', summary='', log='', version=version, validation=validation)
            self.process = subprocess.Popen([PYTHON, str(SRC / 'run_checks.py')], cwd=ROOT, text=True,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            process = self.process
        threading.Thread(target=self.wait, args=(process, version, validation), daemon=True).start()

    def wait(self, process, version, validation):
        output = process.communicate()[0]
        from viewer_common import validation_hash
        if version != STATE.model_version() or validation != validation_hash(ROOT):
            state = 'stale'
        else:
            state = 'passed' if process.returncode == 0 else 'failed'
        if state == 'passed':
            from viewer_common import record_validation
            try:
                record_validation(ROOT)
            except RuntimeError as error:
                state = 'stale'
                output += f'\n{error}\n'
        lines = output.strip().splitlines()
        with self.lock:
            STATE.update('checks', state=state, summary=lines[-1] if lines else '', log=tail(output, 60))
            self.process = None


CHECKS = Checks()


def checks_running(root=ROOT):
    """Avoid queueing a model writer behind a separate check invocation."""
    lock = root / 'generated/.checks.lock'
    try:
        pid = int(lock.read_text().strip() or 0)
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


class Sources:
    """Content fingerprints of the build inputs; mtime-only changes (a file rewritten
    with identical bytes) are not changes."""

    def __init__(self):
        self.mtimes = snapshot()
        self.digests = {p: self.digest(p) for p in self.mtimes}

    @staticmethod
    def digest(rel):
        try:
            return hashlib.sha1((ROOT / rel).read_bytes()).hexdigest()
        except OSError:
            return None

    def changed(self):
        """Paths whose content differs from the last accepted state (does not accept)."""
        current = snapshot()
        out = []
        for p in set(current) | set(self.mtimes):
            if current.get(p) != self.mtimes.get(p) and self.digest(p) != self.digests.get(p):
                out.append(p)
        return sorted(out), current

    def accept(self, current):
        previous = self.mtimes
        self.digests = {p: self.digests.get(p) if previous.get(p) == m else self.digest(p) for p, m in current.items()}
        self.mtimes = current


def refresh(changed, force_build=False, checks=True):
    """A validation-only edit reuses the current IFC and refreshes its checks."""
    if force_build or not up_to_date():
        if not run_build(changed or ['(manual rebuild)']):
            return False
    else:
        STATE.update('build', state='ready', version=STATE.model_version(), changed=changed[:20])
    from viewer_common import validation_current, validation_hash
    if not validation_current(ROOT):
        if checks:
            CHECKS.start()
        else:
            STATE.update('checks', state='stale', summary='Current model needs checks', log='')
    elif not CHECKS.busy():
        # A queued refresh may reach here after an earlier run already finished.
        # Reuse it, while preserving a more recent failure of the same inputs.
        with STATE.lock:
            current = STATE.status['checks']
            version, validation = STATE.model_version(), validation_hash(ROOT)
            if not (current['state'] == 'failed' and current.get('version') == version
                    and current.get('validation') == validation):
                if (current['state'], current.get('version'), current.get('validation')) != ('passed', version, validation):
                    STATE.update('checks', state='passed', summary='Current model verified', log='',
                                 version=version, validation=validation)
    return True


def watcher(watch, checks):
    sources = Sources()
    while True:
        awakened = STATE.rebuild.wait(0.5)
        changed = sources.changed()[0] if watch else []
        if not changed and not awakened:
            continue
        # Finish publishing the current check result before starting another run.
        while checks_running() or CHECKS.busy():
            time.sleep(0.3)
        time.sleep(0.3)                     # let an editor/agent finish writing a batch of files
        changed, current = sources.changed() if watch else ([], snapshot())
        requested, forced, check_requested = STATE.take_requests()
        if not changed and not (awakened or requested):
            sources.accept(current)
            continue
        sources.accept(current)
        refresh(changed, force_build=forced, checks=checks or check_requested)
        # Files edited during the build trigger the next round.
        if watch and sources.changed()[0]:
            STATE.rebuild.set()


class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            # Browsers cancel downloads/keep-alive connections when tabs close
            # or snapshots finish. This is a transport disconnect, not a model
            # or server error; let unrelated failures reach the normal logger.
            pass

    def log_message(self, *args):
        pass

    def send_json(self, value, code=200):
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, content_type=None):
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type or self.guess_type(str(path)))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(url.path)
        if path in ('/', '/index.html'):
            return self.send_file(WEB / 'index.html', 'text/html; charset=utf-8')
        if path == '/events':
            return self.events()
        if path == '/api/status':
            with STATE.lock:
                viewers = len(STATE.listeners)
            return self.send_json({**STATE.status, 'viewers': viewers, 'root': str(ROOT),
                'server': {'pid': os.getpid(), 'fingerprint': RUNTIME_FINGERPRINT,
                           'watch': STATE.watch, 'checks': STATE.auto_checks,
                           'snapdir': str(STATE.snapdir)}})
        if path == '/api/documents':
            return self.send_json({'documents': documents.listing(ROOT), 'errors': documents.verify(ROOT)})
        if path.startswith('/web/'):
            return self.send_file(safe(WEB, path[5:]))
        if path in ('/generated/model.glb', '/generated/elements.json'):
            return self.send_file(GENERATED / path.rsplit('/', 1)[1],
                                  'model/gltf-binary' if path.endswith('.glb') else 'application/json')
        if path.startswith('/files/'):
            rel = path[7:]
            target = documents.resolve(rel, ROOT)
            if target and target.is_dir():
                return self.send_directory(target, rel)
            if target:
                return self.send_file(target)
        self.send_error(404)

    def send_directory(self, directory, rel):
        links = []
        for child in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
            reference = rel.rstrip('/') + '/' + child.name
            if child.name.startswith('.') or documents.resolve(reference, ROOT) is None:
                continue
            links.append(f'<li><a href="{html.escape(documents.href(reference))}">{html.escape(child.name)}</a></li>')
        body = ('<!doctype html><meta charset="utf-8"><title>Model documents</title>'
                '<style>body{font:16px system-ui;max-width:75rem;margin:3rem auto;padding:0 2rem}li{margin:.8rem 0}</style>'
                f'<h1>{html.escape(rel)}</h1><ul>{"".join(links)}</ul>').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        if url.path == '/api/log':            # viewer diagnostics (web/viewer.js), one JSON line each
            from viewer_common import state_dir
            with (state_dir(ROOT) / 'web-client.log').open('a') as log:
                log.write(body.decode('utf-8', 'replace').rstrip() + '\n')
            return self.send_json({'ok': True})
        if url.path in ('/api/rebuild', '/api/refresh'):
            STATE.request_build(force=url.path == '/api/rebuild',
                                checks=urllib.parse.parse_qs(url.query).get('checks') == ['1'])
            return self.send_json({'ok': True})
        if url.path == '/api/snap':
            name = urllib.parse.parse_qs(url.query).get('name', ['snap'])[0]
            name = ''.join(c for c in name if c.isalnum() or c in '-_.') or 'snap'
            STATE.snapdir.mkdir(parents=True, exist_ok=True)
            (STATE.snapdir / f'{name}.png').write_bytes(body)
            return self.send_json({'ok': True, 'path': str(STATE.snapdir / f'{name}.png')})
        self.send_error(404)

    def events(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        q = STATE.subscribe()
        try:
            while True:
                try:
                    message = q.get(timeout=15)
                    self.wfile.write(f'data: {message}\n\n'.encode())
                except queue.Empty:
                    self.wfile.write(b': ping\n\n')
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            STATE.unsubscribe(q)


def safe(base, rel):
    target = (base / rel).resolve()
    return target if target.is_relative_to(base.resolve()) else base / '__denied__'


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=default_port())
    parser.add_argument('--no-watch', action='store_true')
    parser.add_argument('--no-checks', action='store_true')
    parser.add_argument('--snapdir', default=None)
    args = parser.parse_args()
    if args.snapdir:
        STATE.snapdir = Path(args.snapdir)
    STATE.watch, STATE.auto_checks = not args.no_watch, not args.no_checks
    server = Server(('127.0.0.1', args.port), Handler)
    from viewer_common import atomic_json
    atomic_json(state_file(), {'pid': os.getpid(), 'port': args.port, 'root': str(ROOT), 'snapdir': str(STATE.snapdir)})
    need_build = not up_to_date()
    if not need_build:
        STATE.update('build', state='ready', seconds=0)
        from viewer_common import validation_current
        if validation_current(ROOT):
            from viewer_common import validation_hash
            STATE.update('checks', state='passed', summary='Current model verified', version=STATE.model_version(),
                         validation=validation_hash(ROOT))
        elif not args.no_checks:
            CHECKS.start()
    threading.Thread(target=watcher, args=(not args.no_watch, not args.no_checks), daemon=True).start()
    if need_build:
        STATE.rebuild.set()
    print(f'monarch viewer: http://127.0.0.1:{args.port}/  (root {ROOT})', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    sys.path.insert(0, str(SRC))
    main()
