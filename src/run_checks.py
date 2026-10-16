"""Run every model check in one Python process that opens and tessellates the IFC once.

    uv run python src/run_checks.py                 all checks (check.sh calls this)
    uv run python src/run_checks.py gym kids_og     only checks whose name contains a word
    uv run python src/run_checks.py --list

Before (26 Sep 2026): 16 processes, each re-opening the 45 MB IFC and re-meshing the
products it needs with OpenCASCADE -> ~85 s. Now ifcopenshell.open() returns one
shared instance per file and create_shape() is memoised and uses the hybrid CGAL
kernel (same bounding boxes within 1 mm, see geometry_cache.py).

Checks stay ordinary scripts: each still runs standalone. A new room should prefer
declarative rules in data/rules/ (src/rules.py) over a new check_*.py file.
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

# (script, argv, why) -- the order check.sh used to run them in.
CHECKS = [
    ('documents.py', [], 'authoritative plan content has not changed without model source review'),
    ('variant.py', ['check'], 'active room variants match saved geometry and product properties'),
    ('check_model.py', [], 'invariants over data/: floating geometry, dimensions; design conflicts warn'),
    ('rules.py', [], 'declarative data/rules/*.json on the actual IFC geometry'),
    ('check_cellar_services.py', [], 'cellar services: source registration, IFC envelopes, coordination (slow: BVH clash tree)'),
    ('check_stair_finishes.py', [], 'oak treads, finished rises, interior railing colour'),
    ('check_dg_site_measurements.py', [], 'site-measured DG room and knee-wall storage'),
    ('check_dg_foyer.py', [], 'DG foyer'),
    ('check_dg_floor_build_up.py', [], 'physical DG floor build-up'),
    ('check_terrain_cutouts.py', [], 'terrain excavation'),
    ('check_entrance_wardrobe.py', [], 'entrance joinery'),
    ('check_kids_dg.py', [], 'room catalogue, privacy glazing, joinery colours'),
    ('check_dg_southeast.py', [], 'DG southeast office active variant'),
    ('check_furnishing_finishes.py', [], 'furnishing finishes'),
    ('check_bastelraum.py', [], 'Bastelraum UG 01.99.03'),
    ('check_gym.py', [], 'GYM UG 01.99.02'),
    ('check_kids_og.py', ['all'], 'OG Spielzimmer 01.01.03, both variants'),
    ('check_master_inga.py', [], 'master bedroom Inga bed, roof, access'),
    # Passing room checks that existed but were never wired into check.sh (added 26 Sep 2026).
    ('check_master_malm.py', [], 'master MALM chest (owned), plant, roof/socket/desk clearance'),
    ('check_eg_bath_pax.py', [], 'EG bathroom PAX: product, variant, actual IFC size, finishes, front'),
    ('check_balcony.py', [], 'balcony furnishing: facade route, chair pullback, bench access'),
    ('check_cellar.py', [], 'cellar assemblies / active layout'),
    ('check_cellar_festbank.py', [], 'folded Festbank set, actual IFC and all variants'),
    ('check_cellar_keller06.py', [], 'Keller 06 shelving layouts'),
    ('check_dg_sections.py', [], 'DG section datums (+20 cm regression rejected)'),
    ('check_upper_floor_finishes.py', [], 'upper floor knee datums and finished floor heights'),
]


def install_caches():
    """Share one parsed IFC per file and memoise tessellation across all checks."""
    import ifcopenshell
    import ifcopenshell.geom
    from geometry_cache import KERNEL

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
        return shared_open(ROOT / 'generated/haus_nord_essentials.ifc')

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


# Checks that need live OpenCASCADE shapes run in a second process, overlapping the rest.
PARALLEL = {'check_cellar_services.py'}


def main(args):
    if '--list' in args:
        for script, argv, why in CHECKS:
            print(f'{script:32} {" ".join(argv):4} {why}')
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
    side = {}
    if len(selected) > 1:
        import subprocess
        for script, argv, _ in selected:
            if script in PARALLEL:
                side[script] = (time.time(), subprocess.Popen(
                    [sys.executable, str(SRC / 'run_checks.py'), f'--exact={script}', '--raw'],
                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
    results = {}
    for script, argv, _ in selected:            # in-process checks first, side ones overlap
        if script not in side:
            t = time.time()
            code, output = run(script, argv)
            results[script] = (code, output, time.time() - t)
    for script, (started, process) in side.items():
        output = process.communicate()[0]
        results[script] = (process.returncode, output.split('\x00', 1)[-1], time.time() - started)
    if '--raw' in args:                         # worker mode: hand the raw output to the parent
        code, output, _ = results[selected[0][0]]
        sys.stdout.write('\x00' + output)
        return code
    for script, argv, _ in selected:
        code, output, took = results[script]
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
    if '--raw' in args:
        return main(args)
    from model_lock import Lease
    with Lease(ROOT):
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
