"""Tessellate the built IFC once into generated/geometry.npz + generated/elements.json.

    uv run python src/geometry_cache.py          (build.sh runs this after the build)

Everything downstream reads this cache instead of re-tessellating the 45 MB IFC:
the browser viewer (via export_glb.py), the query CLI (src/q.py), the generic
rules (src/rules.py) and the per-room checks (run_checks.py seeds create_shape).

Kernel: ifcopenshell's hybrid CGAL/OpenCASCADE mesher, ~3x faster than plain
OpenCASCADE on this model (face-set meshes are 15x faster). Measured 26 Sep 2026
over all 1003 products: every bounding box within 1 mm of the OpenCASCADE result.

Coordinates stay IFC world coordinates (metres, z up). Element keys are the IFC
Name (= the JSON "id"); a Name used on several storeys gets "@UG/EG/OG/DG".
"""
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import tempfile
import time

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as util
import numpy as np
from viewer_common import file_hash

ROOT = Path(__file__).resolve().parent.parent
GENERATED = ROOT / 'generated'
MODEL = GENERATED / 'haus_nord_essentials.ifc'
GEOMETRY = GENERATED / 'geometry.npz'
ELEMENTS = GENERATED / 'elements.json'
KERNEL = 'hybrid-cgal-simple-opencascade'
STOREY_CODE = {'Untergeschoss': 'UG', 'Erdgeschoss': 'EG', 'Obergeschoss': 'OG', 'Dachgeschoss': 'DG'}
SKIP = ('IfcOpeningElement',)
CACHE_VERSION = 3       # explicit IFC image maps retained through cache reuse


def settings():
    s = ifcopenshell.geom.settings()
    s.set('use-world-coords', True)
    return s


def colour(material):
    d = material.diffuse
    rgb = [float(d.r()), float(d.g()), float(d.b())] if callable(getattr(d, 'r', None)) else [float(d.r), float(d.g), float(d.b)]
    t = float(material.transparency) if material.has_transparency else 0.0
    if not np.isfinite(t):          # IfcOpenShell returns NaN for "unspecified"
        t = 0.0
    return [round(c, 4) for c in rgb], round(t, 4)


def plain(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return str(value)


def mixed_items(entity):
    """Representations mixing solid kinds (extrusion + swept disk): the iterator drops some."""
    rep = entity.Representation
    return rep is not None and any(len({i.is_a() for i in r.Items}) > 1 for r in rep.Representations)


def unpack(entity, shape):
    from image_textures import extract
    g = shape.geometry
    return (entity, np.array(g.verts, dtype=np.float64).reshape(-1, 3),
            np.array(g.faces, dtype=np.int64).reshape(-1, 3),
            np.array(g.material_ids, dtype=np.int64), [colour(m) for m in g.materials], extract(entity, shape))


def tessellate(model, threads=None, include=None):
    """Yield (entity, verts[n,3] float64, faces[m,3] int, material_ids[m], materials) per product.

    Multi-threaded iterator for the bulk; per-element create_shape for representations the
    iterator mis-meshes (verified 26 Sep 2026: every bbox then equals create_shape exactly).
    """
    extra = {'include': include} if include is not None else {}
    it = ifcopenshell.geom.iterator(settings(), model, threads or multiprocessing.cpu_count(),
                                    geometry_library=KERNEL, **extra)
    if not it.initialize():
        return
    while True:
        shape = it.get()
        entity = model.by_id(shape.id)
        if not entity.is_a(SKIP[0]):
            if mixed_items(entity):
                yield unpack(entity, ifcopenshell.geom.create_shape(settings(), entity, geometry_library=KERNEL))
            else:
                yield unpack(entity, shape)
        if not it.next():
            break


def geometry_keys(model):
    """{entity id: hash} of everything that shapes a product's mesh, free of STEP ids and GUIDs:
    representation, placement chain, openings, and the styles/materials that colour it.
    Unchanged key -> the previous cache's mesh is reused (1.3 s instead of re-meshing)."""
    ref = re.compile(r'#(\d+)')
    keys = {}
    for e in model.by_type('IfcProduct'):
        if not e.Representation or e.is_a(SKIP[0]):
            continue
        ents, seen = [], set()

        def visit(root):
            if root is None:
                return
            for x in model.traverse(root):
                if x.id() in seen:
                    continue
                seen.add(x.id())
                ents.append(x)
                for styled in getattr(x, 'StyledByItem', None) or ():        # colours point at items
                    visit(styled)
                for rep in getattr(x, 'HasRepresentation', None) or ():      # material styles
                    visit(rep)
                for mapping in getattr(x, 'HasTextures', None) or ():
                    visit(mapping)

        visit(e.Representation)
        visit(e.ObjectPlacement)
        for rel in getattr(e, 'HasOpenings', None) or ():
            visit(rel.RelatedOpeningElement.Representation)
            visit(rel.RelatedOpeningElement.ObjectPlacement)
        for rel in getattr(e, 'HasAssociations', None) or ():
            if rel.is_a('IfcRelAssociatesMaterial'):
                visit(rel.RelatingMaterial)
        local = {x.id(): i for i, x in enumerate(ents)}
        digest = hashlib.sha1(f'{CACHE_VERSION}/{KERNEL}'.encode())
        for x in ents:
            digest.update(ref.sub(lambda m: '#' + str(local.get(int(m.group(1)), '?')), str(x).split('=', 1)[1]).encode())
        keys[e.id()] = digest.hexdigest()
    return keys


def previous_meshes(out_dir):
    """{geometry key: (verts, faces, material_ids, materials)} from the last cache, if compatible."""
    try:
        old = Geometry(out_dir)
    except (OSError, ValueError, KeyError):
        return {}
    if old.meta.get('version') != CACHE_VERSION or old.meta.get('kernel') != KERNEL:
        return {}
    palette = old.meta['palette']
    out = {}
    for e in old.elements:
        if 'gkey' not in e:
            continue
        v, f = old.mesh(e)
        fm = old.face_material[e['faces'][0]:e['faces'][1]]
        used, mids = np.unique(fm, return_inverse=True)
        out[e['gkey']] = (v.copy(), f.copy(), mids, [(palette[i]['rgb'], palette[i]['transparency']) for i in used], e.get('textures', []))
    return out


def build(model_path=MODEL, out_dir=GENERATED, incremental=True):
    t0 = time.time()
    model_digest = file_hash(model_path)
    out_dir = Path(out_dir)
    model = ifcopenshell.open(str(model_path))
    names = {}
    for e in model.by_type('IfcProduct'):
        names.setdefault(e.Name, []).append(e)
    gkeys = geometry_keys(model)
    reuse = previous_meshes(out_dir) if incremental else {}
    todo = [model.by_id(i) for i, k in gkeys.items() if k not in reuse]
    fresh = {}
    if todo:
        for entity, v, f, mids, mats, textures in tessellate(model, include=todo):
            fresh[entity.id()] = (v, f, mids, mats, textures)
    elements, verts, faces, face_mat, palette, pindex = [], [], [], [], [], {}
    voff = foff = 0
    for entity_id, gkey in gkeys.items():
        entity = model.by_id(entity_id)
        if entity_id in fresh:
            v, f, mids, mats, textures = fresh[entity_id]
        elif gkey in reuse:
            v, f, mids, mats, textures = reuse[gkey]
        else:
            continue                        # the kernel produced no shape
        if not len(f):
            continue
        container = util.get_container(entity) or util.get_aggregate(entity)   # spaces are aggregated
        storey = STOREY_CODE.get(getattr(container, 'Name', None), getattr(container, 'Name', None) or 'SITE')
        name = entity.Name or f'#{entity.id()}'
        key = name if len(names.get(entity.Name, [])) <= 1 else f'{name}@{storey}'
        # Per-face palette index; faces without a material get the default grey.
        local = []
        for m in (mats or [([0.75, 0.75, 0.75], 0.0)]):
            k = (tuple(m[0]), m[1])
            if k not in pindex:
                pindex[k] = len(palette)
                palette.append({'rgb': list(k[0]), 'transparency': k[1]})
            local.append(pindex[k])
        local = np.array(local)
        fm = local[np.clip(mids, 0, len(local) - 1)] if len(mids) == len(f) else np.full(len(f), local[0])
        psets = {pset: {k: plain(v) for k, v in props.items() if k != 'id'}
                 for pset, props in util.get_psets(entity).items()}
        elements.append({
            'key': key, 'name': name, 'class': entity.is_a(), 'guid': entity.GlobalId, 'storey': storey,
            'bbox': [round(float(x), 4) for x in (*v.min(0), *v.max(0))],
            'verts': [voff, voff + len(v)], 'faces': [foff, foff + len(f)], 'gkey': gkey,
            'label': ((psets.get('Pset_Product', {}) or {}).get('Product') or getattr(entity, 'LongName', None)
                      or entity.Description or None),
            'psets': psets,
            'textures': textures,
        })
        verts.append(v)
        faces.append(f + voff)
        face_mat.append(fm)
        voff += len(v)
        foff += len(f)
    out_dir.mkdir(exist_ok=True)
    arrays = dict(verts=np.concatenate(verts), faces=np.concatenate(faces).astype(np.uint32),
                  face_material=np.concatenate(face_mat).astype(np.uint16))
    if file_hash(model_path) != model_digest:
        raise RuntimeError('IFC changed during tessellation; retry before publishing geometry.')
    meta = {'version': CACHE_VERSION, 'ifc': str(Path(model_path).name), 'ifc_mtime': os.path.getmtime(model_path),
            'build_id': model_digest,
            'kernel': KERNEL, 'palette': palette, 'elements': elements}
    atomic_write(out_dir / GEOMETRY.name, lambda p: np.savez(p, build_id=model_digest, **arrays))
    atomic_write(out_dir / ELEMENTS.name, lambda p: Path(p).write_text(json.dumps(meta, ensure_ascii=False)))
    print(f'geometry cache: {len(elements)} elements ({len(todo)} re-meshed, {len(elements) - len(todo)} reused), '
          f'{voff} verts, {foff} tris, {time.time() - t0:.1f}s')
    return meta, arrays


def atomic_write(path, writer):
    fd, tmp = tempfile.mkstemp(prefix='.' + path.stem, suffix=path.suffix, dir=path.parent)
    os.close(fd)
    try:
        writer(tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


class Geometry:
    """Loaded cache: elements by key, and each element's triangle mesh on demand."""

    def __init__(self, out_dir=GENERATED):
        out_dir = Path(out_dir)
        self.out_dir = out_dir
        self.meta = json.loads((out_dir / ELEMENTS.name).read_text())
        with np.load(out_dir / GEOMETRY.name) as data:
            if 'build_id' not in data or str(data['build_id']) != self.meta.get('build_id'):
                raise ValueError('Geometry and metadata belong to different builds')
            self.verts, self.faces, self.face_material = data['verts'], data['faces'], data['face_material']
        self.elements = self.meta['elements']
        self.by_key = {e['key']: e for e in self.elements}
        self.by_guid = {e['guid']: e for e in self.elements}
        self.by_name = {}
        for e in self.elements:
            self.by_name.setdefault(e['name'], []).append(e)

    def stale(self):
        return not MODEL.exists() or file_hash(MODEL) != self.meta.get('build_id')

    def mesh(self, element):
        v0, v1 = element['verts']
        f0, f1 = element['faces']
        return self.verts[v0:v1], self.faces[f0:f1].astype(np.int64) - v0

    def get(self, ref):
        if ref in self.by_key:
            return [self.by_key[ref]]
        if '@' in ref:
            name, storey = ref.rsplit('@', 1)
            return [e for e in self.by_name.get(name, []) if e['storey'] == storey]
        return self.by_name.get(ref, [])


def load():
    """The cache for the current IFC, refreshed incrementally first if the IFC is newer."""
    try:
        g = Geometry()
        if not g.stale():
            return g
    except (OSError, ValueError, KeyError):
        pass
    import contextlib
    import io
    from model_lock import Lease
    with Lease(ROOT):
        # Another writer may have completed while we waited for the lease.
        try:
            g = Geometry()
            if not g.stale():
                return g
        except (OSError, ValueError, KeyError):
            pass
        if not MODEL.exists():
            raise SystemExit('no generated IFC yet: run ./build.sh')
        print('(geometry cache behind the IFC: refreshing incrementally)', flush=True)
        with contextlib.redirect_stdout(io.StringIO()):
            build()
        return Geometry()


if __name__ == '__main__':
    import sys
    from model_lock import Lease
    with Lease(ROOT):
        build(incremental='--full' not in sys.argv)
