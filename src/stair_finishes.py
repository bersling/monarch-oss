"""Optional stair finishes from data/stair_finishes.json (see the _doc there).

Without the file every flight is bare concrete, balustrades keep the default grey and
handrails are wood. With it, `timber_flights` get one IfcCovering tread per step on an
adhesive bed (the concrete core is lowered by that build-up), balustrades and interior
void guards take the `metal_railings` colour and handrails the `treads` colour. Props
come from the file, never from code.
"""
import json
from pathlib import Path
import ifcopenshell.api

ROOT = Path(__file__).resolve().parent.parent
_PATH = ROOT / 'data/stair_finishes.json'
SPEC = json.loads(_PATH.read_text()) if _PATH.is_file() else None


def build_up(stair):
    if not SPEC or stair['id'] not in SPEC.get('timber_flights', []):
        return 0.0
    return SPEC['treads']['wood_thickness'] + SPEC['treads']['adhesive_thickness']


def internal_railing(identifier):
    return bool(SPEC) and identifier not in SPEC.get('metal_railings', {}).get('excluded_railings', [])


class Finishes:
    def __init__(self, model):
        self.model = model
        self.styles = {}
        self.materials = {}
        if not SPEC:
            return
        for key in ('treads', 'painted_concrete', 'metal_railings'):
            self.styles[key] = self.style(SPEC[key].get('material', key), SPEC[key]['rgb'])
        self.styles['adhesive'] = self.style('Stair adhesive', [.57, .55, .50])

    def style(self, name, rgb):
        m = self.model
        style = ifcopenshell.api.run('style.add_style', m, name=name)
        r, g, b = rgb
        ifcopenshell.api.run('style.add_surface_style', m, style=style, ifc_class='IfcSurfaceStyleShading',
                             attributes={'SurfaceColour': {'Name': None, 'Red': r, 'Green': g, 'Blue': b}, 'Transparency': 0.0})
        return style

    def finish(self, entity, key, props):
        m = self.model
        rep = entity.Representation.Representations[0]
        ifcopenshell.api.run('style.assign_representation_styles', m, shape_representation=rep, styles=[self.styles[key]])
        if props:
            pset = ifcopenshell.api.run('pset.add_pset', m, product=entity, name='Pset_Product')
            ifcopenshell.api.run('pset.edit_pset', m, pset=pset, properties=props)
        material = SPEC[key].get('material', key)
        if material not in self.materials:
            self.materials[material] = ifcopenshell.api.run('material.add_material', m, name=material)
        ifcopenshell.api.run('material.assign_material', m, products=[entity], material=self.materials[material])

    def railing(self, entity, handrail=False):
        if not SPEC:
            return
        if handrail:
            self.finish(entity, 'treads', SPEC.get('handrail', {}).get('props', {}))
        else:
            self.finish(entity, 'metal_railings', SPEC['metal_railings'].get('props', {}))

    def flight(self, stair, flight, add_product, box_solid, elevation):
        if not build_up(stair):
            return
        m = self.model
        t = SPEC['treads']
        offset = build_up(stair)
        self.finish(flight, 'painted_concrete', {
            **SPEC['painted_concrete'].get('props', {}),
            'Dimensions': f"{stair['risers']} finished risers of {stair['riser_h']*100:g} cm; going {stair['tread']*100:g} cm",
            'Finish': SPEC['painted_concrete'].get('material', ''), 'FinishBuildUp': offset})
        flight.NumberOfRisers = stair['risers']
        flight.NumberOfTreads = stair['risers']
        flight.RiserHeight = stair['riser_h']
        flight.TreadLength = stair['tread']
        x0, x1 = stair['x_range']
        y0 = stair['y_start']
        direction = 1 if stair['y_end'] > y0 else -1
        covers = []
        for i in range(stair['risers']):
            front = y0 + i * stair['tread'] * direction
            back = front + stair['tread'] * direction
            ylo, yhi = sorted((front - t['nosing'] * direction, back))
            z = (i + 1) * stair['riser_h']
            wood = box_solid(x0, ylo, z - t['wood_thickness'], x1, yhi, z)
            a, b = sorted((front, back))
            adhesive = box_solid(x0, a, z - offset, x1, b, z - t['wood_thickness'])
            cover = add_product('IfcCovering', f"TREAD_{stair['id']}_{i+1:02}", [wood, adhesive], elevation)
            cover.PredefinedType = 'FLOORING'
            cover.ObjectType = 'TREAD_WITH_ADHESIVE'
            m.createIfcStyledItem(wood, [self.styles['treads']], None)
            m.createIfcStyledItem(adhesive, [self.styles['adhesive']], None)
            pset = ifcopenshell.api.run('pset.add_pset', m, product=cover, name='Pset_Product')
            ifcopenshell.api.run('pset.edit_pset', m, pset=pset, properties={
                **t.get('props', {}),
                'Dimensions': f'{(x1-x0)*100:g} x {(yhi-ylo)*100:g} x {offset*100:g} cm build-up; '
                              f'{t["wood_thickness"]*1000:g} mm wood + {t["adhesive_thickness"]*1000:g} mm adhesive',
                'Material': t.get('material', ''), 'WoodThickness': t['wood_thickness'],
                'AdhesiveThickness': t['adhesive_thickness'], 'Nosing': t['nosing'],
                'HostFlight': stair['id'], 'StepNumber': i + 1, 'FinishedHeight': elevation + z})
            covers.append(cover)
        # The finishes remain independently selectable, explicitly hosted by the flight.
        rel = ifcopenshell.api.run('root.create_entity', m, ifc_class='IfcRelCoversBldgElements')
        rel.RelatingBuildingElement = flight
        rel.RelatedCoverings = covers
