"""Overlay actual IFC horizontal sections on pinned architectural floor plans.

    ./q compare EG
    ./q compare all --height 1.2
    ./q compare roof

Produces source and overlay PNGs, a vector PDF and an evidence manifest under
 generated/plan-audit/. This is visual comparison evidence, not automatic proof
of agreement. Roof profiles, elevations and objects outside the selected slice
require their own comparisons. Coordinates come from data/plan_registration.json.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pymupdf
from shapely import Polygon, union_all

from documents import catalogue, resolve, verify
from geometry_cache import load
from model_lock import Lease
from viewer_common import atomic_json, read_json, source_hash

ROOT=Path(__file__).resolve().parents[1]
FLOORS={'UG':-2.85,'EG':0.,'OG':2.85,'DG':5.7}
CLASSES={'walls':({'IfcWall','IfcWallStandardCase'},(.85,0.,.65)),
         'windows':({'IfcWindow'},(0.,.4,.85)), 'doors':({'IfcDoor'},(0.,.6,.2)),
         'roofs':({'IfcSlab','IfcRoof'},(.85,0.,.65)),
         'skylights':({'IfcWindow'},(0.,.4,.85)), 'solar':({'IfcSolarDevice'},(0.,.6,.2))}


def section_segments(verts,faces,z):
    """Triangle-plane crossings, with vertices on the plane counted only once."""
    triangles=verts[faces]
    triangles=triangles[(triangles[:,:,2].min(axis=1)<=z)&(triangles[:,:,2].max(axis=1)>z)]
    segments=[]
    for tri in triangles:
        hits=[]
        for a,b in ((tri[0],tri[1]),(tri[1],tri[2]),(tri[2],tri[0])):
            if (a[2]<=z<b[2]) or (b[2]<=z<a[2]):
                hits.append((a+(b-a)*(z-a[2])/(b[2]-a[2]))[:2])
        if len(hits)==2 and np.linalg.norm(hits[1]-hits[0])>1e-9:
            segments.append(hits)
    return np.asarray(segments).reshape(-1,2,2)


def to_pdf(points,registration):
    points=np.asarray(points,float)
    origin=np.asarray(registration['anchor_model_xy'])
    return (points-origin)*[registration['points_per_metre'],-registration['points_per_metre']]+registration['anchor_pdf_pt']


def projected_segments(verts,faces):
    """Exact XY union of mesh triangles, preserving concavities and open holes."""
    triangles=verts[faces][:,:,:2]
    areas=(triangles[:,1,0]-triangles[:,0,0])*(triangles[:,2,1]-triangles[:,0,1])-(triangles[:,1,1]-triangles[:,0,1])*(triangles[:,2,0]-triangles[:,0,0])
    footprint=union_all([Polygon(t) for t in triangles[np.abs(areas)>1e-12]],grid_size=1e-7)
    polygons=[footprint] if footprint.geom_type=='Polygon' else list(footprint.geoms)
    segments=[]
    for polygon in polygons:
        for ring in [polygon.exterior,*polygon.interiors]:
            points=np.asarray(ring.coords)
            segments.extend(zip(points[:-1],points[1:]))
    return np.asarray(segments).reshape(-1,2,2)


def matches(element,label):
    if element['class'] not in CLASSES[label][0]:return False
    if label=='roofs':
        return element['name'].startswith(('ROOF_','SHED_')) and not element['name'].endswith('INNER_RETURN')
    if label=='skylights':return element['name'].startswith('DACHFENSTER')
    return True


def main(args):
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('storey',choices=[*FLOORS,'roof','all'],nargs='?',default='all')
    parser.add_argument('--height',type=float,default=1.2,help='section height above finished storey floor, metres')
    parser.add_argument('--classes',nargs='+',choices=CLASSES,help='default: walls/windows/doors; roof: roofs/skylights')
    parser.add_argument('--out',type=Path,default=ROOT/'generated/plan-audit')
    options=parser.parse_args(args)
    if options.classes is None:options.classes=['roofs','skylights'] if options.storey=='roof' else ['walls','windows','doors']
    if not np.isfinite(options.height):parser.error('height must be finite')
    with Lease(ROOT):
        errors=verify(ROOT)
        if errors:raise SystemExit('\n'.join(errors))
        fingerprint=source_hash(ROOT)
        stamp=read_json(ROOT/'generated/viewer-build.json')
        if stamp.get('sources')!=fingerprint:
            raise SystemExit('IFC sources changed: run ./open.sh --no-browser --wait-build or ./build.sh first.')
        geometry=load()
        registration=json.loads((ROOT/'data/plan_registration.json').read_text())
        documents={d['id']:d for d in catalogue(ROOT)}
        options.out.mkdir(parents=True,exist_ok=True)
        selected=FLOORS if options.storey=='all' else [options.storey]
        for code in selected:
            document=documents[registration['documents'][code]]
            with pymupdf.open(resolve(document['path'],ROOT)) as original:
                clip=pymupdf.Rect(registration['clip_pdf_pt'])
                review=pymupdf.open();page=review.new_page(width=clip.width,height=clip.height)
                page.show_pdf_page(page.rect,original,0,clip=clip)
                page.get_pixmap(matrix=pymupdf.Matrix(2,2)).save(options.out/f'{code}-source.png')
                z=None if code=='roof' else FLOORS[code]+options.height;entries=[]
                for label in options.classes:
                    _,colour=CLASSES[label];shape=page.new_shape()
                    for element in geometry.elements:
                        if element['storey']!=('DG' if code=='roof' else code) or not matches(element,label):continue
                        segments=projected_segments(*geometry.mesh(element)) if z is None else section_segments(*geometry.mesh(element),z)
                        projected=to_pdf(segments,registration)-[clip.x0,clip.y0]
                        for a,b in projected:shape.draw_line(pymupdf.Point(a),pymupdf.Point(b))
                        entries.append({'key':element['key'],'class':element['class'],
                                        'segments':len(segments),'bbox':element['bbox']})
                    shape.finish(color=colour,width=.55,stroke_opacity=.8);shape.commit()
                page.get_pixmap(matrix=pymupdf.Matrix(2,2)).save(options.out/f'{code}-overlay.png')
                review.save(options.out/f'{code}-overlay.pdf',garbage=4,deflate=True);review.close()
                evidence={'storey':code,'section_z_m':z,'classes':options.classes,
                          'ifc_sha256':geometry.meta['build_id'],'source_fingerprint':fingerprint,
                          'plan':document['path'],'plan_sha256':document['sha256'],
                          'plan_printed_date':document['printed_date'],'registration':registration,
                          'scope':('Actual IFC XY projections, including holes; lower surfaces may be visible in projection.' if z is None else 'Actual IFC horizontal sections of selected element classes.')+' Visual review required; no automatic agreement claim.',
                          'elements':entries}
                atomic_json(options.out/f'{code}-evidence.json',evidence)
                cut='XY projection' if z is None else f'section {z:+.3f} m'
                print(f'{code}: {len(entries)} elements, {cut}; {options.out / (code+"-overlay.png")}')
        if fingerprint!=source_hash(ROOT) or geometry.stale():
            raise SystemExit('Sources or IFC changed during comparison; rerun before using this evidence.')
    return 0


if __name__=='__main__':
    import sys
    sys.exit(main(sys.argv[1:]))
