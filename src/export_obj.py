"""Export the actual IFC cache to OBJ/MTL, including explicit IFC image textures."""
from pathlib import Path
import numpy as np
from geometry_cache import load, GENERATED, MODEL, atomic_write
from model_lock import Lease


def build(geometry=None, output=None):
    g = geometry or load()
    output = Path(output or MODEL.with_suffix('.obj')).resolve()
    lines = [f'mtllib {output.with_suffix(".mtl").name}']
    materials = {}
    voff, uoff = 1, 1
    for e in g.elements:
        if e['class'] == 'IfcSpace':
            continue
        verts, faces = g.mesh(e)
        ids = g.face_material[slice(*e['faces'])]
        lines.append(f"o {e['class']}_{e['name'].replace(' ', '_')}")
        lines.extend(f'v {x:.6f} {y:.6f} {z:.6f}' for x,y,z in verts)
        mapped = {face:(mapping,uv) for mapping in e.get('textures',[]) for face,uv in zip(mapping['faces'],mapping['uv'],strict=True)}
        previous = None
        for i, tri in enumerate(faces):
            palette = g.meta['palette'][int(ids[i])]
            mapping, uv = mapped.get(i,(None,None))
            key = (tuple(palette['rgb']),palette['transparency'],mapping['image'] if mapping else None)
            if key not in materials:
                materials[key] = f'm_{len(materials)}'
            name = materials[key]
            if name != previous:
                lines.append('usemtl '+name);previous=name
            if uv is None:
                lines.append('f '+' '.join(str(int(v)+voff) for v in tri))
            else:
                lines.extend(f'vt {u:.8f} {v:.8f}' for u,v in uv)
                lines.append('f '+' '.join(f'{int(v)+voff}/{uoff+j}' for j,v in enumerate(tri)))
                uoff += 3
        voff += len(verts)
    mtl=[]
    for (rgb,trans,image),name in materials.items():
        mtl.extend([f'newmtl {name}',f'Kd {rgb[0]:.6f} {rgb[1]:.6f} {rgb[2]:.6f}',f'd {1-trans:.6f}'])
        if image:
            path=(g.out_dir/image).resolve()
            if not path.is_relative_to(g.out_dir.resolve()):
                raise ValueError('Texture must be local to the IFC output directory')
            # OBJ and MTL normally sit beside the IFC and its textures folder.
            import os
            mtl.append('map_Kd '+os.path.relpath(path,output.parent))
        mtl.append('')
    atomic_write(output.with_suffix('.mtl'),lambda p:Path(p).write_text('\n'.join(mtl)+'\n'))
    atomic_write(output,lambda p:Path(p).write_text('\n'.join(lines)+'\n'))
    print(f'{output}: {len(materials)} materials, {uoff-1} UV corners')


if __name__ == '__main__':
    with Lease(GENERATED.parent):
        build()
