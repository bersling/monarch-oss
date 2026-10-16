"""Run every model check in one Python process that opens and tessellates the IFC once.

    uv run python src/run_checks.py                 all checks (check.sh calls this)
    uv run python src/run_checks.py rules           only checks whose script name contains a word
    uv run python src/run_checks.py --list

ifcopenshell.open() returns one shared instance per file and create_shape() is memoised
and served from the geometry cache (same bounding boxes within 1 mm, see geometry_cache.py).

Checks stay ordinary scripts: each still runs standalone. New constraints belong in
declarative rules in data/rules/ (src/rules.py), not in a new check_*.py file.
"""
import contextlib
import io
import os
from pathlib import Path
import runpy
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'src'

# (script, argv, why), in run order.
CHECKS = [
    ('variant.py', ['check'], 'active variants match the placed storey data'),
    ('products.py', ['check'], 'catalogue products and placements are consistent'),
    ('check_model.py', [], 'invariants over data/: unique ids, resolving references, mounted fixtures, dimensions'),
    ('rules.py', [], 'declarative data/rules/*.json on the actual IFC geometry'),
]
if (ROOT / 'data/plan_documents.json').is_file():
    CHECKS.insert(0, ('documents.py', [], 'pinned source documents have not changed without model review'))


def install_caches():
    """Share one parsed IFC per file and memoise tessellation across all checks."""
    import ifcopenshell
    import ifcopenshell.geom
    from geometry_cache import KERNEL
    from viewer_common import model_path

    opened, shapes = {}, {}
    real_open, real_shape = ifcopenshell.open, ifcopenshell.geom.create_shape

    def shared_open(path, *args, **kwargs):
        p = Path(path).resolve()
        if args or kwargs or not p.is_file():
            return real_open(path, *args, **kwargs)
        key = (str(p), p.stat().st_mtime_ns)
        if key not in opened:
            opened[key] = real_open(str(p))
        return opened[key]

    def signature(s):
        out = []
        for name in s.setting_names():
            try:
                out.append((name, str(s.get(name))))
            except Exception:
                pass
        return tuple(out)

    cache = cached_geometry()
    world = signature(world_settings()) if cache else None

    def shared_shape(settings, inst, repr=None, geometry_library=None):
        if cache and repr is None and geometry_library is None and signature(settings) == world:
            element = cache.by_guid.get(getattr(inst, 'GlobalId', None))
            if element is not None and inst.wrapped_data.file is main_file():
                return CachedShape(*cache.mesh(element), lambda: live(settings, inst))
        return live(settings, inst, repr, geometry_library)

    def live(settings, inst, repr=None, geometry_library=None):
        if repr is not None or not hasattr(inst, 'id'):
            return real_shape(settings, inst, repr, geometry_library=geometry_library or KERNEL)
        lib = geometry_library or KERNEL
        key = (id(inst.wrapped_data.file), inst.id(), signature(settings), lib)
        if key not in shapes:
            shapes[key] = real_shape(settings, inst, geometry_library=lib)
        return shapes[key]

    def main_file():
        return shared_open(model_path(ROOT))

    class Tree(ifcopenshell.geom.tree):
        """The clash BVH needs real shapes, not cached meshes."""
        def add_element(self, shape):
            return super().add_element(shape._live() if isinstance(shape, CachedShape) else shape)

    ifcopenshell.open = shared_open
    ifcopenshell.geom.create_shape = shared_shape
    ifcopenshell.geom.tree = Tree
    return opened, shapes


class CachedShape:
    """World-space verts/faces from the geometry cache; anything else (materials, ...)
    falls through to a live tessellation of the same product."""
    def __init__(self, verts, faces, live):
        self.geometry = self
        self.verts = verts.reshape(-1)
        self.faces = faces.reshape(-1)
        self._live = live

    def __getattr__(self, name):
        return getattr(self._live().geometry, name)


def world_settings():
    import ifcopenshell.geom
    s = ifcopenshell.geom.settings()
    s.set(s.USE_WORLD_COORDS, True)
    return s


def cached_geometry():
    """generated/geometry.npz when it belongs to the current IFC, else None (tessellate live)."""
    from geometry_cache import Geometry
    try:
        cache = Geometry()
    except (OSError, ValueError, KeyError):
        return None
    return None if cache.stale() else cache


def run(script, argv):
    """Run one check as __main__; return (returncode, output)."""
    buffer = io.StringIO()
    code = 0
    saved_argv = sys.argv
    sys.argv = [str(SRC / script), *argv]
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            runpy.run_path(str(SRC / script), run_name='__main__')
    except SystemExit as exit_:
        code = exit_.code if isinstance(exit_.code, int) else (0 if exit_.code is None else 1)
        if exit_.code not in (None, 0) and not isinstance(exit_.code, int):
            buffer.write(f'{exit_.code}\n')
    except Exception:
        code = 1
        buffer.write(traceback.format_exc())
    finally:
        sys.argv = saved_argv
    return code, buffer.getvalue()


def main(args):
    if '--list' in args:
        for script, argv, why in CHECKS:
            print(f'{script:32} {" ".join(argv):6} {why}')
        return 0
    verbose = '-v' in args
    exact = [a[len('--exact='):] for a in args if a.startswith('--exact=')]
    words = [a for a in args if not a.startswith('-')]
    if exact:
        selected = [c for c in CHECKS if c[0] in exact]
    else:
        selected = [c for c in CHECKS if not words or any(w in c[0] for w in words)]
    if not selected or (exact and set(exact) - {c[0] for c in selected}):
        print('No matching checks (or unknown --exact name); use --list.', file=sys.stderr)
        return 2
    os.chdir(ROOT)
    sys.path.insert(0, str(SRC))
    install_caches()
    failed, t_all = [], time.time()
    for script, argv, _ in selected:
        t = time.time()
        code, output = run(script, argv)
        took = time.time() - t
        if os.environ.get('MONARCH_CHECK_OUT'):
            Path(os.environ['MONARCH_CHECK_OUT'], script + '.out').write_text(output)
        status = 'ok  ' if code == 0 else 'FAIL'
        print(f'{status} {took:5.1f}s  {script} {" ".join(argv)}'.rstrip(), flush=True)
        lines = output.rstrip().splitlines()
        if code != 0:
            failed.append(script)
            print('\n'.join('     | ' + line for line in lines[-40:]), flush=True)
        elif verbose:
            print('\n'.join('     | ' + line for line in lines), flush=True)
        else:
            # Warnings stay visible; they are design conflicts, not silence.
            for line in lines:
                if line.lstrip().lower().startswith(('warn', 'warning')):
                    print('     | ' + line.strip())
    print(f'{len(selected) - len(failed)}/{len(selected)} checks passed in {time.time() - t_all:.1f}s'
          + (f'; FAILED: {", ".join(failed)}' if failed else ''))
    return 1 if failed else 0


def locked(args):
    """generated/.checks.lock tells the viewer server not to build while checks run."""
    lock = ROOT / 'generated/.checks.lock'
    from model_lock import Lease
    with Lease(ROOT):
        lock.parent.mkdir(exist_ok=True)
        lock.write_text(str(os.getpid()))
        try:
            return main(args)
        finally:
            try:
                if lock.read_text().strip() == str(os.getpid()):
                    lock.unlink()
            except OSError:
                pass


if __name__ == '__main__':
    sys.exit(locked(sys.argv[1:]))
