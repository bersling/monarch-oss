"""Geometry cache -> generated/model.glb for the browser viewer (web/).

    uv run python src/export_glb.py

One glTF node per IFC product, named by its element key, with extras
{key, class, storey}. Full properties stay in generated/elements.json (the viewer
fetches it for the product panel). glTF is y-up: IFC (x, y, z) -> glTF (x, z, -y).
Colours: the IFC/JSON rgb values are authored as sRGB; glTF baseColorFactor is
linear, so they are converted here (else everything renders washed out).
"""
import json
from pathlib import Path
import struct
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geometry_cache import GENERATED, Geometry, atomic_write  # noqa: E402

OUTPUT = GENERATED / 'model.glb'


def srgb_to_linear(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def build(geometry=None, output=OUTPUT):
    t0 = time.time()
    g = geometry or Geometry()
    palette = g.meta['palette']
    materials = []
    for p in palette:
        alpha = 1.0 - float(p['transparency'])
        m = {'pbrMetallicRoughness': {'baseColorFactor': [*map(float, srgb_to_linear(p['rgb'])), alpha],
                                      'metallicFactor': 0.0, 'roughnessFactor': 0.9},
             'doubleSided': True}
        if alpha < 0.999:
            m['alphaMode'] = 'BLEND'
        materials.append(m)
    blobs, views, accessors, meshes, nodes = [], [], [], [], []
    offset = 0

    def add_view(data, target=None):
        nonlocal offset
        raw = data if isinstance(data, bytes) else data.tobytes()
        pad = (-len(raw)) % 4
        blobs.append(raw + b'\0' * pad)
        views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(raw)})
        if target is not None:
            views[-1]['target'] = target
        offset += len(raw) + pad
        return len(views) - 1

    images, textures, texture_materials = [], [], {}

    def image_material(uri):
        if uri not in texture_materials:
            path = (g.out_dir / uri).resolve()
            if not path.is_relative_to(g.out_dir.resolve()) or path.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
                raise ValueError(f'Unsupported local IFC texture URI: {uri}')
            images.append({'bufferView': add_view(path.read_bytes()),
                           'mimeType': 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg'})
            textures.append({'source': len(images)-1, 'sampler': 0})
            texture_materials[uri] = len(materials)
            materials.append({'pbrMetallicRoughness': {'baseColorFactor':[1,1,1,1],
                              'baseColorTexture':{'index':len(textures)-1}, 'metallicFactor':0, 'roughnessFactor':.9},
                              'doubleSided':True})
            if path.suffix.lower() == '.png':
                materials[-1]['alphaMode'] = 'BLEND'
        return texture_materials[uri]

    for e in g.elements:
        v, f = g.mesh(e)
        f0, f1 = e['faces']
        mats = g.face_material[f0:f1]
        smooth = e.get('psets', {}).get('Pset_Product', {}).get('SurfaceShading') == 'smooth'
        prims = []
        def primitive(tris, mid, uv=None):
            if uv is None and not smooth:
                used, inverse = np.unique(tris.reshape(-1), return_inverse=True)
            else:
                # Per-corner attributes preserve UV seams and sharp sculpted edges.
                used, inverse = tris.reshape(-1), np.arange(tris.size)
            pos = v[used][:, [0, 2, 1]].astype(np.float32)
            pos[:, 2] *= -1
            idx = inverse.astype(np.uint32)
            vp = add_view(pos, 34962)
            accessors.append({'bufferView': vp, 'componentType': 5126, 'count': len(pos), 'type': 'VEC3',
                              'min': pos.min(0).tolist(), 'max': pos.max(0).tolist()})
            vi = add_view(idx, 34963)
            accessors.append({'bufferView': vi, 'componentType': 5125, 'count': len(idx), 'type': 'SCALAR'})
            attributes = {'POSITION': len(accessors) - 2}
            indices = len(accessors) - 1
            if smooth:
                # Explicit opt-in for sculpted objects; architecture retains flat shading.
                face_normals = np.cross(v[tris[:, 1]] - v[tris[:, 0]], v[tris[:, 2]] - v[tris[:, 0]])
                unit = face_normals / np.maximum(np.linalg.norm(face_normals, axis=1, keepdims=True), 1e-20)
                corners = tris.reshape(-1)
                counts = np.bincount(corners, minlength=len(v))
                starts = np.cumsum(counts) - counts
                adjacent = np.argsort(corners, kind='stable') // 3
                own_faces = np.repeat(np.arange(len(tris)), 3)
                normals = np.zeros((len(corners), 3))
                for rank in range(int(counts.max(initial=0))):
                    selected = np.flatnonzero(counts[corners] > rank)
                    neighbours = adjacent[starts[corners[selected]] + rank]
                    # A 45-degree crease keeps the base rim and tail edges crisp.
                    keep = np.einsum('ij,ij->i', unit[own_faces[selected]], unit[neighbours]) >= np.cos(np.pi/4)
                    normals[selected[keep]] += face_normals[neighbours[keep]]
                normals = normals[:, [0, 2, 1]]
                normals[:, 2] *= -1
                lengths = np.linalg.norm(normals, axis=1, keepdims=True)
                normals = (normals / np.maximum(lengths, 1e-20)).astype(np.float32)
                vn = add_view(normals, 34962)
                accessors.append({'bufferView': vn, 'componentType': 5126, 'count': len(normals), 'type': 'VEC3'})
                attributes['NORMAL'] = len(accessors)-1
            if uv is not None:
                coords = np.array(uv, dtype=np.float32).reshape(-1,2)
                coords[:,1] = 1 - coords[:,1]  # IFC bottom-left -> glTF top-left image origin
                vu = add_view(coords, 34962)
                accessors.append({'bufferView':vu,'componentType':5126,'count':len(coords),'type':'VEC2'})
                attributes['TEXCOORD_0'] = len(accessors)-1
            prims.append({'attributes': attributes, 'indices': indices, 'material':int(mid)})

        textured = np.zeros(len(f),bool)
        for mapping in e.get('textures', []):
            indices = np.asarray(mapping['faces'])
            if textured[indices].any():
                raise ValueError(f"Overlapping image maps on {e['key']}")
            textured[indices] = True
            primitive(f[indices], image_material(mapping['image']), mapping['uv'])
        for mid in np.unique(mats[~textured]):
            primitive(f[(mats == mid) & ~textured], mid)
        meshes.append({'name': e['key'], 'primitives': prims})
        nodes.append({'name': e['key'], 'mesh': len(meshes) - 1,
                      'extras': {'key': e['key'], 'class': e['class'], 'storey': e['storey']}})
    gltf = {'asset': {'version': '2.0', 'generator': 'monarch export_glb.py'},
            'extras': {'build_id': g.meta.get('build_id')},
            'scene': 0, 'scenes': [{'nodes': list(range(len(nodes)))}], 'nodes': nodes, 'meshes': meshes,
            'materials': materials, 'accessors': accessors, 'bufferViews': views,
            'buffers': [{'byteLength': offset}]}
    if images:
        gltf.update(images=images, textures=textures, samplers=[{'magFilter':9729,'minFilter':9987,'wrapS':33071,'wrapT':33071}])
    body = json.dumps(gltf, separators=(',', ':')).encode()
    body += b' ' * ((-len(body)) % 4)
    binary = b''.join(blobs)
    out = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(body) + 8 + len(binary))
    out += struct.pack('<II', len(body), 0x4E4F534A) + body + struct.pack('<II', len(binary), 0x004E4942) + binary
    atomic_write(Path(output), lambda p: Path(p).write_bytes(out))
    print(f'model.glb: {len(nodes)} nodes, {len(out) / 1e6:.1f} MB, {time.time() - t0:.1f}s')


if __name__ == '__main__':
    from model_lock import Lease
    with Lease(GENERATED.parent):
        build()
