"""Image/UV identity survives IFC placement, tessellation, cache reuse and exports."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import json
import zlib

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.validate
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from test_geometry_cache import make_ifc
import geometry_cache
import export_glb
import export_obj
from image_textures import textured_box


def png():
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(bytes([0,255,0,0,0,255,0,0,0,0,255,255,255,255])))+chunk(b'IEND',b'')


class ImageTextureTest(unittest.TestCase):
    def test_ifc_to_cached_glb_and_obj_with_rotated_placement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'assets').mkdir();out=root/'generated';out.mkdir()
            source=root/'assets/chart.png';source.write_bytes(png())
            path=out/'model.ifc';make_ifc(path);model=ifcopenshell.open(path)
            shelf=next(e for e in model.by_type('IfcFurnishingElement') if e.Name=='SHELF')
            item=textured_box(model,[1,2,0,1.1,3,2],{'image':'assets/chart.png','u_axis':'+y','v_axis':'+z'},root)
            shelf.Representation.Representations[0].Items=[item]
            transform=np.array([[0,-1,0,10],[1,0,0,20],[0,0,1,3],[0,0,0,1]],float)
            ifcopenshell.api.run('geometry.edit_object_placement',model,product=shelf,matrix=transform)
            model.write(path)
            reopened=ifcopenshell.open(path)
            logger=ifcopenshell.validate.json_logger()
            ifcopenshell.validate.validate(reopened,logger)
            self.assertEqual(logger.statements, [], 'Texture export must remain valid IFC4')
            mapping,=reopened.by_type('IfcIndexedTriangleTextureMap')
            self.assertEqual(len(mapping.TexCoordIndex),12)
            self.assertEqual((out/mapping.Maps[0].URLReference).read_bytes(),source.read_bytes())
            geometry_cache.build(path,out,incremental=False)
            first=geometry_cache.Geometry(out)
            expected=next(e['textures'] for e in first.elements if e['name']=='SHELF')
            geometry_cache.build(path,out)
            cached=geometry_cache.Geometry(out)
            self.assertEqual(next(e['textures'] for e in cached.elements if e['name']=='SHELF'),expected)
            export_glb.build(cached,out/'model.glb')
            raw=(out/'model.glb').read_bytes();length=struct.unpack_from('<I',raw,12)[0]
            doc=json.loads(raw[20:20+length]);binary=raw[28+length:]
            image=doc['bufferViews'][doc['images'][0]['bufferView']]
            self.assertEqual(binary[image['byteOffset']:image['byteOffset']+image['byteLength']],source.read_bytes())
            shelf_node=next(n for n in doc['nodes'] if n['name']=='SHELF')
            primitive,=doc['meshes'][shelf_node['mesh']]['primitives']
            def read(attribute,columns):
                a=doc['accessors'][primitive['attributes'][attribute]];view=doc['bufferViews'][a['bufferView']]
                return np.frombuffer(binary,dtype='<f4',count=a['count']*columns,offset=view['byteOffset']).reshape(-1,columns)
            positions=read('POSITION',3);uv=read('TEXCOORD_0',2)
            np.testing.assert_allclose(uv[:,0],8-positions[:,0],atol=1e-6)
            np.testing.assert_allclose(uv[:,1],1-(positions[:,1]-3)/2,atol=1e-6)
            material=doc['materials'][primitive['material']]['pbrMetallicRoughness']
            self.assertEqual(material['baseColorFactor'],[1,1,1,1])
            self.assertIn('baseColorTexture',material)
            export_obj.build(cached,out/'model.obj')
            self.assertIn('map_Kd textures/',(out/'model.mtl').read_text())
            self.assertEqual(sum(l.startswith('vt ') for l in (out/'model.obj').read_text().splitlines()),36)
            # UV-only edits must invalidate incremental geometry, even though the shape is unchanged.
            before=geometry_cache.geometry_keys(reopened)
            mapping.TexCoords.TexCoordsList=[[1-u,v] for u,v in mapping.TexCoords.TexCoordsList]
            self.assertNotEqual(before,geometry_cache.geometry_keys(reopened))
