"""Convert a downloaded 3D model (glb/gltf/dae/fbx/stl/obj) into a normalized
OBJ asset for assets/, via headless Blender.

Usage:
    /Applications/Blender.app/Contents/MacOS/Blender -b --python src/convert_asset.py -- \
        <infile> <outfile.obj> [key=value ...]

Options (key=value):
    rot_z=90          rotate about Z after import (degrees), to face the model +Y
    scale=0.01        explicit uniform scale (e.g. cm -> m)
    fit_x=0.38        scale so the X extent becomes this (meters)
    fit_y= / fit_z=   same for the other axes. One fit_* scales uniformly; give
                      several to scale non-uniformly (each named axis hits its
                      target, unnamed axes follow the first named one) — for a
                      mesh with the right plan proportions but the wrong height
                      for the product it stands in for.
    exclude=A,B       drop imported objects by name before joining. glTF scene
                      downloaded from asset sites routinely ship the prop plus a
                      backdrop plane, ground and trees, which would otherwise
                      dominate the bounding box that fit_* measures.
    decimate=20000    collapse to at most this many triangles
    split=material    write one OBJ per source material plus <out>_parts.json
                      (file/bounds/rgb/faces per colour group), instead of one
                      flat OBJ -- for a multi-coloured product that must stay a
                      single IFC object with styled parts
    origin=wall       'wall'   -> x centered, y_min = 0, z_min = 0  (default)
                      'center' -> x,y centered, z_min = 0
    up=Y              source is Y-up (OBJ only; glTF/DAE handled by importer)

Prints the final bounding box in meters so the caller can sanity-check against
the real product dimensions.
"""
import bpy, sys, math, os, json
from mathutils import Vector

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
infile, outfile = argv[0], argv[1]
opts = dict(kv.split('=', 1) for kv in argv[2:] if '=' in kv)

bpy.ops.wm.read_factory_settings(use_empty=True)
ext = os.path.splitext(infile)[1].lower()
if ext in ('.glb', '.gltf'):
    bpy.ops.import_scene.gltf(filepath=infile)
elif ext == '.dae':
    bpy.ops.wm.collada_import(filepath=infile)
elif ext == '.fbx':
    bpy.ops.import_scene.fbx(filepath=infile)
elif ext == '.stl':
    bpy.ops.wm.stl_import(filepath=infile)
elif ext == '.obj':
    up = opts.get('up', 'Y').upper()
    kw = {'up_axis': 'Y', 'forward_axis': 'NEGATIVE_Z'} if up == 'Y' else {'up_axis': 'Z', 'forward_axis': 'Y'}
    bpy.ops.wm.obj_import(filepath=infile, **kw)
else:
    sys.exit(f'unsupported input: {ext}')

imported = [o for o in bpy.data.objects if o.type == 'MESH']
if len(imported) > 1:
    # scene exports: print the inventory so the extras can be named in exclude=
    print(f'{len(imported)} objects imported:')
    for o in sorted(imported, key=lambda o: o.name):
        vs = [o.matrix_world @ v.co for v in o.data.vertices]
        if not vs:
            print(f'  {o.name:24s} (empty)')
            continue
        size = [round(max(v[i] for v in vs) - min(v[i] for v in vs), 3) for i in range(3)]
        print(f'  {o.name:24s} tris={len(o.data.polygons):6d} size={size}')

dropped = {n.strip() for n in opts.get('exclude', '').split(',') if n.strip()}
for o in [o for o in bpy.data.objects if o.name in dropped]:
    bpy.data.objects.remove(o, do_unlink=True)
if dropped:
    print('excluded:', sorted(dropped))

meshes = [o for o in bpy.data.objects if o.type == 'MESH']
if not meshes:
    sys.exit('no meshes imported')

# join everything into one object
bpy.ops.object.select_all(action='DESELECT')
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
ob = bpy.context.view_layer.objects.active
# glTF/FBX importers parent meshes to empties; drop the parent so local space
# equals world space and transform_apply below actually moves the geometry.
if ob.parent:
    bpy.ops.object.parent_clear(type='CLEAR_KEEP_TRANSFORM')
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def bbox():
    vs = [ob.matrix_world @ v.co for v in ob.data.vertices]
    lo = Vector((min(v.x for v in vs), min(v.y for v in vs), min(v.z for v in vs)))
    hi = Vector((max(v.x for v in vs), max(v.y for v in vs), max(v.z for v in vs)))
    return lo, hi


lo, hi = bbox()
print('imported bbox:', [round(c, 3) for c in lo], [round(c, 3) for c in hi],
      'size', [round(c, 3) for c in (hi - lo)])

if 'rot_z' in opts:
    # glTF/FBX importers leave rotation_mode='QUATERNION', where assigning
    # rotation_euler is silently ignored — force euler before rotating.
    ob.rotation_mode = 'XYZ'
    ob.rotation_euler = (0, 0, math.radians(float(opts['rot_z'])))
    bpy.ops.object.transform_apply(rotation=True)

# scale: explicit, or fit one axis to a target size
s = float(opts.get('scale', 1.0))
lo, hi = bbox()
size = hi - lo
# each named fit_* axis hits its target; unnamed axes follow the first named
# one, so a single fit_* stays uniform and several give a non-uniform scale.
fitted = [float(opts[a]) / size[i] if a in opts and size[i] > 1e-9 else None
          for a, i in (('fit_x', 0), ('fit_y', 1), ('fit_z', 2))]
if any(f is not None for f in fitted):
    default = next(f for f in fitted if f is not None)
    scales = tuple(f if f is not None else default for f in fitted)
else:
    scales = (s, s, s)
if scales != (1.0, 1.0, 1.0):
    ob.scale = scales
    bpy.ops.object.transform_apply(scale=True)

if 'decimate' in opts:
    limit = int(opts['decimate'])
    ntris = sum(len(p.vertices) - 2 for p in ob.data.polygons)
    if ntris > limit:
        m = ob.modifiers.new('dec', 'DECIMATE')
        m.ratio = limit / ntris
        bpy.ops.object.modifier_apply(modifier=m.name)
        print(f'decimated {ntris} -> ~{limit} tris')

# origin convention
lo, hi = bbox()
mode = opts.get('origin', 'wall')
dx = -(lo.x + hi.x) / 2
dy = -lo.y if mode == 'wall' else -(lo.y + hi.y) / 2
dz = -lo.z
ob.location = (dx, dy, dz)
bpy.ops.object.transform_apply(location=True)

lo, hi = bbox()
print('final bbox:', [round(c, 3) for c in lo], [round(c, 3) for c in hi],
      'size', [round(c, 3) for c in (hi - lo)])

os.makedirs(os.path.dirname(os.path.abspath(outfile)), exist_ok=True)


def write_obj(obj, path):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True, export_materials=False,
                          export_uv=False, export_normals=False, export_triangulated_mesh=True,
                          up_axis='Z', forward_axis='Y')


def srgb(c):
    """Blender shader colours are linear; this project authors IFC colours as sRGB."""
    c = max(0.0, min(1.0, c))
    return round(12.92 * c if c <= .0031308 else 1.055 * c ** (1 / 2.4) - .055, 4)


def slug(name):
    keep = ''.join(ch.lower() if ch.isalnum() else '_' for ch in name)
    return '_'.join(part for part in keep.split('_') if part) or 'part'


if opts.get('split') == 'material':
    # One OBJ per source material plus a <stem>_parts.json of file/bounds/rgb, the format
    # gen_*.py feeds to artpiece_geometry.meshpart: a multi-coloured product stays ONE IFC
    # object with differently styled parts instead of collapsing to a single flat colour.
    stem = os.path.splitext(outfile)[0]
    bpy.ops.object.select_all(action='DESELECT')
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.separate(type='MATERIAL')
    bpy.ops.object.mode_set(mode='OBJECT')
    pieces = [o for o in bpy.context.selected_objects if o.type == 'MESH' and o.data.polygons]
    parts, used = {}, set()
    for piece in sorted(pieces, key=lambda o: -len(o.data.polygons)):
        material = piece.data.materials[0] if piece.data.materials else None
        name = slug(material.name if material else 'part')
        while name in used:
            name += '_b'
        used.add(name)
        rgb = [.7, .7, .7]
        if material and material.use_nodes:
            for node in material.node_tree.nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    rgb = [srgb(c) for c in node.inputs['Base Color'].default_value[:3]]
                    break
        vs = [piece.matrix_world @ v.co for v in piece.data.vertices]
        bounds = [round(min(v[i] for v in vs), 6) for i in range(3)] + [round(max(v[i] for v in vs), 6) for i in range(3)]
        path = f'{stem}_{name}.obj'
        write_obj(piece, path)
        parts[name] = {'file': os.path.relpath(path, os.getcwd()), 'bounds': bounds,
                       'rgb': rgb, 'faces': len(piece.data.polygons)}
        print(f'  {name:20s} faces={len(piece.data.polygons):6d} rgb={rgb} bounds={bounds}')
    with open(f'{stem}_parts.json', 'w') as f:
        json.dump(parts, f, indent=1)
    print('wrote', f'{stem}_parts.json', f'({len(parts)} colour groups)')
else:
    write_obj(ob, outfile)
    print('wrote', outfile)
