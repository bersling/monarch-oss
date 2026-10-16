"""Content fingerprints of the build inputs, build/validation stamps and IFC identity hashes."""
import hashlib
from functools import lru_cache
import json
import os
from pathlib import Path
import tempfile

from house import HOUSE

ROOT = Path(__file__).resolve().parents[1]


def model_path(root=ROOT):
    """The built IFC (data/house.json: ifc_file), for any checkout root."""
    return Path(root) / HOUSE.ifc_file


def state_dir(root):
    key = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / 'monarch-viewer' / key


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    os.replace(tmp, path)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def file_hash(path):
    path = Path(path).resolve()
    stat = path.stat()
    return _file_hash(str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=4096)
def _file_hash(path, device, inode, size, mtime, ctime):
    # ctime and inode catch replacements and edits whose mtime was restored.
    # Unchanged large OBJ/IFC/PDF files need not be reread on every wait poll.
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


# Python modules the build imports; with data/**/*.json and assets/** they are the build inputs.
BUILD_SOURCES = ('build_model.py', 'house.py', 'floor_finishes.py', 'stair_finishes.py', 'roof_finishes.py',
                 'light_switches.py', 'viewer_common.py', 'geometry_cache.py', 'export_glb.py',
                 'documents.py', 'products.py', 'model_lock.py', 'pipeline.py', 'image_textures.py')


def source_hash(root):
    """Content, not mtimes: switching branches and regenerating equal assets is free."""
    root = Path(root)
    paths = list((root / 'data').rglob('*.json')) + list((root / 'assets').rglob('*'))
    paths += [root / 'src' / name for name in BUILD_SOURCES]
    # Replacing a reference PDF must invalidate the model just like editing its
    # extracted geometry. Pin verification then stops an unreviewed rebuild.
    registry = read_json(root / 'data/plan_documents.json')
    paths += [root / d['path'] for d in registry.get('documents', []) if 'path' in d]
    digest = hashlib.sha256()
    for path in sorted(p for p in paths if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(bytes.fromhex(file_hash(path)))
    return digest.hexdigest()


def record_build(root, before):
    """Only bless a build whose input files stayed unchanged while it ran."""
    if before != source_hash(root):
        raise RuntimeError('Build inputs changed during generation; retry with stable inputs.')
    atomic_json(Path(root) / 'generated/viewer-build.json', {
        'sources': before, 'ifc': file_hash(model_path(root)), 'house': HOUSE.summary()})


def validation_hash(root):
    root = Path(root)
    digest = hashlib.sha256()
    # Include every checker and its local helpers; a partial hand-maintained list
    # previously let changed rules/checks keep an old successful validation.
    paths = [root / 'build.sh', root / 'check.sh', *sorted((root / 'src').glob('*.py'))]
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(bytes.fromhex(file_hash(path)))
    return digest.hexdigest()


def validation_current(root):
    root = Path(root)
    stamp = read_json(root / 'generated/viewer-build.json')
    return (stamp.get('sources') == source_hash(root)
            and stamp.get('validation') == validation_hash(root)
            and model_path(root).is_file()
            and stamp.get('ifc') == file_hash(model_path(root)))


def record_validation(root):
    path = Path(root) / 'generated/viewer-build.json'
    stamp = read_json(path)
    if stamp.get('sources') != source_hash(root) or stamp.get('ifc') != file_hash(model_path(root)):
        raise RuntimeError('Build inputs changed during validation; rebuild before opening.')
    stamp['validation'] = validation_hash(root)
    atomic_json(path, stamp)


class ModelIndex:
    """Stable semantic identity and visual hashes independent of generated STEP/GUID IDs."""
    def __init__(self, model):
        self.model = model
        self.memo = {}
        self.elements = {}
        for entity in model.by_type('IfcProduct') + model.by_type('IfcProject'):
            if entity.is_a('IfcFeatureElement'):
                continue  # Openings are included in their host's geometry hash.
            key = self.key(entity)
            if key in self.elements:
                raise ValueError(f'Ambiguous IFC identity: {key}')
            self.elements[key] = entity
        self.visual = {key: self.visual_hash(e) for key, e in self.elements.items()}

    def key(self, entity):
        parent = None
        if getattr(entity, 'ContainedInStructure', ()):
            parent = entity.ContainedInStructure[0].RelatingStructure
        elif getattr(entity, 'Decomposes', ()):
            parent = entity.Decomposes[0].RelatingObject
        return (self.key(parent) if parent else ()) + ((entity.is_a(), entity.Name),)

    def hash(self, value):
        if hasattr(value, 'is_a'):
            ident = value.id()
            if ident and ident in self.memo:
                return self.memo[ident]
            values = [value.is_a()]
            for i, child in enumerate(value):
                if value.attribute_name(i) not in ('GlobalId', 'OwnerHistory') and not (value.is_a('IfcStyledItem') and value.attribute_name(i) == 'Item'):
                    values.append(self.hash(child))
            # Colour-only changes must reload the affected mesh too.
            if value.is_a('IfcRepresentationItem'):
                values.extend(self.hash(s) for s in getattr(value, 'StyledByItem', ()))
            result = hashlib.sha256(repr(values).encode()).hexdigest()
            if ident:
                self.memo[ident] = result
            return result
        if isinstance(value, (list, tuple)):
            # Fast path for large triangulated point/index arrays.
            if not value or not hasattr(value[0], 'is_a'):
                if not any(hasattr(v, 'is_a') for v in value):
                    return hashlib.sha256(repr(value).encode()).hexdigest()
            return tuple(self.hash(v) for v in value)
        return value

    def visual_hash(self, entity):
        values = [self.hash(getattr(entity, attr, None)) for attr in ('Representation', 'ObjectPlacement')]
        for rel in getattr(entity, 'HasOpenings', ()):
            values.append(self.visual_hash(rel.RelatedOpeningElement))
        return hashlib.sha256(repr(values).encode()).hexdigest()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Record successful build.sh validation.')
    parser.add_argument('--validated', action='store_true', required=True)
    parser.parse_args()
    record_validation(Path(__file__).resolve().parent.parent)
