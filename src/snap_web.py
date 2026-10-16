"""Screenshots of the browser viewer (web/) with headless Chrome, for AI verification.

    .venv/bin/python src/snap_web.py VIEW [VIEW ...] [--size 1600x1000] [--out DIR] [--timeout 120]

A VIEW starts with a name and takes options until the next name. Coordinates are IFC
(x east, y north, z up, metres):

    exterior=-9,-16,12,7,4,1                 NAME=cam_x,cam_y,cam_z,target_x,target_y,target_z
    exterior=-9,-16,12,7,4,1 hide=SITE       options: storeys=GF,UF  hide=SITE  cut=GF@1.2  sel=KEY
    gf plan=GF                               top-down storey plan, north up (span=18 target=x,y)
    gallery room=GAL_                        frame an element (or every key with that prefix) from
                                             the south-east, storeys above cut away (cut= overrides)
    plan=GF   room=GAL_SHELF                 without a name: auto-named plan_GF / room_GAL_SHELF

All views render in ONE Chrome launch. The script reuses this checkout's compatible viewer server
or starts one on the checkout's default port. Obsolete server code is replaced;
capture waits for the model to include current source edits. The server writes PNGs to its snap dir (generated/snaps by
default); --out copies them from there into DIR afterwards. Prints the PNG paths, plus
any JavaScript errors Chrome logged. The same view as an interactive URL: --urls.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
OPTIONS = {'storeys', 'hide', 'cut', 'plan', 'sel', 'room', 'frame', 'span', 'target', 'cam', 'edges', 'spaces', 'look', 'fov'}
FATAL = r'snap failed|viewer boot failed|Uncaught'
NUMS = re.compile(r'^[-+\d.,\s]+$')


def parse_views(tokens):
    views = []
    for token in tokens:
        key, eq, value = token.partition('=')
        # A common view name is "room". Six coordinates are a camera even
        # when its name also spells a framing/plan option.
        if eq and NUMS.match(value) and (key not in OPTIONS or (key in ('room', 'plan', 'frame') and ',' in value)):
            nums = [float(n) for n in value.split(',') if n.strip()]
            if len(nums) != 6:
                raise SystemExit(f'{token}: expected NAME=cam_x,cam_y,cam_z,target_x,target_y,target_z')
            views.append({'name': key, 'cam': nums[:3], 'target': nums[3:]})
        elif eq and key in OPTIONS:
            fresh = key in ('plan', 'room') and (not views or camera_set(views[-1]))
            if fresh or not views:
                views.append({'name': f'{key}_{value}'.strip('_') if key in ('plan', 'room') else 'view'})
            view = views[-1]
            view['frame' if key == 'room' else key] = value
        elif not eq:
            views.append({'name': token})
        else:
            raise SystemExit(f'unknown option {key!r} in {token!r} (known: {", ".join(sorted(OPTIONS | {"room"}))})')
    for view in views:
        view['name'] = re.sub(r'[^A-Za-z0-9_.-]', '_', view['name']) or 'view'
    names = [v['name'] for v in views]
    if len(set(names)) != len(names):
        raise SystemExit(f'duplicate view names: {names}')
    return views


def camera_set(view):
    return any(k in view for k in ('cam', 'plan', 'frame'))


def ensure_server():
    """Reuse the launcher's ownership, upgrade and build-freshness checks."""
    import open_viewer
    port = open_viewer.ensure_running(watch=False, checks=False)
    if open_viewer.wait(port, build_only=True):
        raise SystemExit('Cannot capture an out-of-date model')
    state = open_viewer.status(port)
    return port, Path(state.get('server', {}).get('snapdir') or ROOT / 'generated' / 'snaps')


def view_url(port, view, size=None):
    """Interactive URL (size None) or snapshot URL for one view."""
    parts = []
    for key, value in view.items():
        if key == 'name':
            continue
        if isinstance(value, list):
            value = ','.join(f'{v:g}' for v in value)
        parts.append(f'{key}={urllib.parse.quote(str(value), safe=",@_.-")}')
    return f'http://127.0.0.1:{port}/#' + '&'.join(parts)


def snaps_url(port, views, w, h):
    payload = json.dumps(views, separators=(',', ':'))
    return f'http://127.0.0.1:{port}/#w={w}&h={h}&snaps={urllib.parse.quote(payload, safe="")}'


def run_chrome(url, w, h, expected, timeout):
    """Launch headless Chrome once and wait until every expected PNG is complete."""
    if not Path(CHROME).exists():
        raise SystemExit(f'Chrome not found at {CHROME}')
    profile = tempfile.mkdtemp(prefix='monarch-snap-')
    log_path = Path(profile) / 'chrome.log'
    args = [CHROME, '--headless=new', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
            f'--window-size={w},{h}', f'--user-data-dir={profile}', '--no-first-run', '--no-default-browser-check',
            '--disable-extensions', '--hide-scrollbars', '--mute-audio', '--enable-logging=stderr', '--v=0', url]
    t0 = time.time()
    with open(log_path, 'wb') as log:
        chrome = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        sizes = {}
        try:
            while True:
                done = True
                for p in expected:
                    size = p.stat().st_size if p.exists() else 0
                    if not size or sizes.get(p) != size:
                        done = False
                    sizes[p] = size
                if done:
                    break
                fatal = [e for e in js_errors(log_path) if re.search(FATAL, e)]
                if fatal:
                    raise SystemExit('viewer error:\n' + '\n'.join(fatal))
                if chrome.poll() is not None:
                    raise SystemExit(f'Chrome exited early (code {chrome.returncode}); log:\n{tail(log_path)}')
                if time.time() - t0 > timeout:
                    missing = [p.name for p in expected if not p.exists()]
                    raise SystemExit(f'timeout after {timeout:.0f} s waiting for {missing}.\n'
                                     f'JS errors: {js_errors(log_path) or "none logged"}\nurl: {url[:300]}')
                time.sleep(0.25)
        finally:
            chrome.terminate()
            try:
                chrome.wait(5)
            except subprocess.TimeoutExpired:
                chrome.kill()
    errors = js_errors(log_path)
    shutil.rmtree(profile, ignore_errors=True)
    return time.time() - t0, errors


def js_errors(log_path):
    lines = log_path.read_text(errors='replace').splitlines() if log_path.exists() else []
    return [ln.split('] ', 1)[-1].strip() for ln in lines
            if 'Uncaught' in ln or ('CONSOLE' in ln and re.search(r'error|fail|monarch warning', ln, re.I))]


def tail(path, n=20):
    return '\n'.join(path.read_text(errors='replace').splitlines()[-n:]) if path.exists() else ''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('views', nargs='+', help='NAME=cx,cy,cz,tx,ty,tz | NAME | option=value ...')
    parser.add_argument('--size', default='1600x1000', help='WxH in pixels (default 1600x1000)')
    parser.add_argument('--out', help='copy the PNGs into this directory')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--urls', action='store_true', help='also print the interactive viewer URL of each view')
    args = parser.parse_args()
    w, h = (int(n) for n in args.size.lower().split('x'))
    views = parse_views(args.views)
    port, snapdir = ensure_server()
    expected = [snapdir / f'{v["name"]}.png' for v in views]
    for p in expected:
        p.unlink(missing_ok=True)
    seconds, errors = run_chrome(snaps_url(port, views, w, h), w, h, expected, args.timeout)
    paths = expected
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        paths = [Path(shutil.copy2(p, out / p.name)) for p in expected]
    for p, v in zip(paths, views):
        print(p)
        if args.urls:
            print('   ', view_url(port, v))
    print(f'{len(paths)} view(s) in {seconds:.1f} s', file=sys.stderr)
    for e in errors:
        print(f'JS: {e}', file=sys.stderr)


if __name__ == '__main__':
    main()
