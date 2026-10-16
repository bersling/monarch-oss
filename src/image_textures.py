"""Explicit IFC image maps, read back from IFC for browser/OBJ export.

The initial authoring primitive is a box with a planar image projection. IFC
stores the triangles, image URI and UV coordinates; exporters do not consult room
JSON or match magic colours to guess the artwork.
"""
from pathlib import Path
import shutil

import ifcopenshell.util.placement
import numpy as np
from viewer_common import file_hash


def textured_box(model, bounds, spec, root):
    root = Path(root)
    source = (root / spec['image']).resolve()
    if not source.is_relative_to((root / 'assets').resolve()) or source.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
        raise ValueError('Image textures must be JPEG/PNG files under assets/')
    uri = 'textures/' + file_hash(source) + source.suffix.lower()
    destination = root / 'generated' / uri
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copyfile(source, destination)
    x0,y0,z0,x1,y1,z1 = bounds
    points = [(float(x),float(y),float(z)) for x in (x0,x1) for y in (y0,y1) for z in (z0,z1)]
    quads = [(0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)]
    indices = [[a+1,b+1,c+1] for a,b,c,d in quads for a,b,c in ((a,b,c),(a,c,d))]
    item = model.createIfcTriangulatedFaceSet(model.createIfcCartesianPointList3D(points), None, True, indices, None)
    uv = []
    for point in points:
        coords = []
        for axis in (spec['u_axis'], spec['v_axis']):
            if axis not in ('+x','-x','+y','-y','+z','-z'):
                raise ValueError(f'Unknown texture axis: {axis}')
            k = 'xyz'.index(axis[1]); extent = bounds[k+3] - bounds[k]
            if extent <= 0:
                raise ValueError('Texture projection needs a positive extent')
            value = (point[k]-bounds[k])/extent
            coords.append(float(value if axis[0]=='+' else 1-value))
        uv.append(coords)
    texture = model.createIfcImageTexture(False, False, 'DIFFUSE', None, None, uri)
    model.createIfcIndexedTriangleTextureMap([texture], item, model.createIfcTextureVertexList(uv), indices)
    style = model.createIfcSurfaceStyle('Image: '+spec['image'], 'BOTH', [
        model.createIfcSurfaceStyleRendering(model.createIfcColourRgb(None,1.,1.,1.),0.,None,None,None,None,None,None,'NOTDEFINED'),
        model.createIfcSurfaceStyleWithTextures([texture])])
    model.createIfcStyledItem(item,[style],None)
    return item


def extract(entity, shape):
    """Project explicit planar IFC UV maps onto the mesher's world-space vertices.

    The mesher can reorder triangles and vertices; item IDs retain ownership.
    Reject non-planar UV layouts rather than silently exporting a wrong image.
    """
    maps = []
    for representation in entity.Representation.Representations:
        for item in representation.Items:
            for mapping in getattr(item, 'HasTextures', ()):
                if not mapping.is_a('IfcIndexedTriangleTextureMap'):
                    raise ValueError('Unsupported IFC texture map type: '+mapping.is_a())
                maps.append((item, mapping))
    if not maps:
        return []
    geometry = shape.geometry
    verts = np.asarray(geometry.verts).reshape(-1,3)
    faces = np.asarray(geometry.faces).reshape(-1,3)
    ids = np.asarray(geometry.item_ids)
    placement = ifcopenshell.util.placement.get_local_placement(entity.ObjectPlacement)
    result = []
    for item, mapping in maps:
        if len(mapping.Maps)!=1 or not mapping.Maps[0].is_a('IfcImageTexture'):
            raise ValueError('Expected one IFC image texture per mapped item')
        coord = np.asarray(item.Coordinates.CoordList)
        world = np.c_[coord,np.ones(len(coord))] @ placement.T
        uv = np.asarray(mapping.TexCoords.TexCoordsList)
        xyz_samples, uv_samples = [], []
        for triangle, uv_indices in zip(item.CoordIndex,mapping.TexCoordIndex,strict=True):
            for vertex, uv_index in zip(triangle,uv_indices,strict=True):
                xyz_samples.append(world[vertex-1]);uv_samples.append(uv[uv_index-1])
        coeff,_,_,_ = np.linalg.lstsq(xyz_samples,uv_samples,rcond=None)
        if not np.allclose(np.asarray(xyz_samples)@coeff,uv_samples,atol=1e-6):
            raise ValueError('Only planar IFC UV mappings are currently supported')
        selected = np.flatnonzero(ids==item.id())
        if not len(selected):
            raise ValueError('Mesher omitted textured IFC item')
        points = verts[faces[selected]].reshape(-1,3)
        mapped = (np.c_[points,np.ones(len(points))] @ coeff).reshape(-1,3,2)
        result.append({'image':mapping.Maps[0].URLReference,'faces':selected.tolist(),
                       'uv':mapped.round(8).tolist()})
    return result
