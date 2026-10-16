"""Plan-derived stair finishes, kept separate from structural layout and floor levels."""
import json
from pathlib import Path
import ifcopenshell.api

ROOT=Path(__file__).resolve().parent.parent
SPEC=json.loads((ROOT/'data/stair_finishes.json').read_text())


def build_up(stair):
    if stair['id'] not in SPEC['timber_flights']:return 0.0
    return SPEC['treads']['wood_thickness']+SPEC['treads']['adhesive_thickness']


def internal_railing(identifier):
    return identifier not in SPEC['metal_railings']['excluded_railings']


class Finishes:
    def __init__(self,model):
        self.model=model
        self.styles={}
        self.materials={}
        for key,title in [('treads','Oiled oak stair treads and handrails'),
                          ('painted_concrete','White painted concrete stairs'),
                          ('metal_railings','RAL 7021 Schwarzgrau stair metalwork')]:
            self.styles[key]=self.style(title,SPEC[key]['rgb'])
        self.styles['adhesive']=self.style('Stair adhesive',[.57,.55,.50])

    def style(self,name,rgb):
        m=self.model;style=ifcopenshell.api.run('style.add_style',m,name=name)
        r,g,b=rgb
        ifcopenshell.api.run('style.add_surface_style',m,style=style,ifc_class='IfcSurfaceStyleShading',
            attributes={'SurfaceColour':{'Name':None,'Red':r,'Green':g,'Blue':b},'Transparency':0.0})
        return style

    def finish(self,entity,key,props):
        m=self.model;rep=entity.Representation.Representations[0]
        ifcopenshell.api.run('style.assign_representation_styles',m,shape_representation=rep,styles=[self.styles[key]])
        pset=ifcopenshell.api.run('pset.add_pset',m,product=entity,name='Pset_Product')
        ifcopenshell.api.run('pset.edit_pset',m,pset=pset,properties=props)
        material=SPEC[key].get('material',SPEC[key].get('name',key))
        if material not in self.materials:
            self.materials[material]=ifcopenshell.api.run('material.add_material',m,name=material)
        ifcopenshell.api.run('material.assign_material',m,products=[entity],material=self.materials[material])

    def railing(self,entity,handrail=False):
        key='treads' if handrail else 'metal_railings'
        self.finish(entity,key,{
            'Product':'Handlauf Eiche, geölt' if handrail else 'Stahlgeländer, einbrennlackiert',
            'Material':'Eiche blockverleimt, geölt' if handrail else 'Stahl',
            'Colour':'Eiche natur' if handrail else 'RAL 7021 Schwarzgrau',
            'ColourSource':'AF_D4_20_001 / AF_D4_05_112' if handrail else SPEC[key]['colour_source'],
            'Lieferant':'Gemäss Pset_Source, BKP 2722; bestehende Projektzuordnung',
            'Contract':'Gemäss Pset_Source; keine neue Bestellung',
            'Plan':SPEC['plans']['railing'],
            'Dimensions':'Bestehende Geländergeometrie und Höhen beibehalten; Stab-/Befestigungsdetails schematisch',
            'DisplayColour':'RGB ist eine Bildschirmannäherung; RAL 7021 ist verbindlich' if not handrail else 'Holzfarbton schematisch'})

    def flight(self,stair,flight,add_product,box_solid,elevation):
        if not build_up(stair):return
        m=self.model;t=SPEC['treads'];offset=build_up(stair)
        self.finish(flight,'painted_concrete',{
            'Product':'Betonfertigteiltreppe mit Holzauflagen',
            'Lieferant':'Gemäss Pset_Source, Betonfertigteil; Holzlieferant noch nicht zugeordnet',
            'Contract':'Gemäss Pset_Source für Beton; Holzauftrag nicht belegt',
            'Plan':SPEC['plans']['connections'],
            'Dimensions':f"{stair['risers']} fertige Steigungen à {stair['riser_h']*100:g}cm; Auftritt {stair['tread']*100:g}cm",
            'Finish':SPEC['painted_concrete']['material'],
            'FinishBuildUp':offset,'Geometry':t['geometry_note']})
        flight.NumberOfRisers=stair['risers'];flight.NumberOfTreads=stair['risers']
        flight.RiserHeight=stair['riser_h'];flight.TreadLength=stair['tread']
        x0,x1=stair['x_range'];y0=stair['y_start'];direction=1 if stair['y_end']>y0 else -1
        covers=[]
        for i in range(stair['risers']):
            front=y0+i*stair['tread']*direction;back=front+stair['tread']*direction
            ylo,yhi=sorted((front-t['nosing']*direction,back));z=(i+1)*stair['riser_h']
            wood=box_solid(x0,ylo,z-t['wood_thickness'],x1,yhi,z)
            a,b=sorted((front,back))
            adhesive=box_solid(x0,a,z-offset,x1,b,z-t['wood_thickness'])
            cover=add_product('IfcCovering',f"TREAD_OAK_{stair['id']}_{i+1:02}",[wood,adhesive],elevation)
            cover.PredefinedType='FLOORING';cover.ObjectType='OAK_TREAD_WITH_ADHESIVE'
            m.createIfcStyledItem(wood,[self.styles['treads']],None)
            m.createIfcStyledItem(adhesive,[self.styles['adhesive']],None)
            pset=ifcopenshell.api.run('pset.add_pset',m,product=cover,name='Pset_Product')
            ifcopenshell.api.run('pset.edit_pset',m,pset=pset,properties={
                'Product':'Trittstufe Eiche blockverleimt, geölt, auf Kleberbett',
                'Lieferant':'Noch nicht aus Werkvertrag zugeordnet',
                'Contract':'Holzstufenauftrag nicht belegt; Ausführung gemäss Detailplan',
                'Plan':SPEC['plans']['railing'],'ConnectionPlan':SPEC['plans']['connections'],
                'Dimensions':f'{(x1-x0)*100:g} x {(yhi-ylo)*100:g} x {offset*100:g} cm Aufbau; davon 37mm Eiche + 3mm Kleber',
                'Material':t['material'],'WoodThickness':t['wood_thickness'],'AdhesiveThickness':t['adhesive_thickness'],
                'Nosing':t['nosing'],'HostFlight':stair['id'],'StepNumber':i+1,
                'FinishedHeight':elevation+z,'SourceNote':t['source_note']})
            covers.append(cover)
        # The finishes remain independently selectable, explicitly hosted by the flight.
        rel=ifcopenshell.api.run('root.create_entity',m,ifc_class='IfcRelCoversBldgElements')
        rel.RelatingBuildingElement=flight;rel.RelatedCoverings=covers
