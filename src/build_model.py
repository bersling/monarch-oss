"""Build the clean Haus Nord IFC from per-storey JSON plan files.

Storeys share one local frame (origin = SW outer corner of the finished
envelope, world (687.68, -356.61)); each storey JSON in data/ holds walls in
plan coordinates and windows referenced to walls. Output goes to generated/.
"""
import json
import math
import os
import tempfile
from viewer_common import source_hash, record_build
import documents
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
from ifcopenshell.entity_instance import _method_dict as _SETTERS
import numpy as np
import shapely
from floor_finishes import slab_solids
from stair_finishes import Finishes, build_up, internal_railing
from roof_finishes import split_lining

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
GENERATED = ROOT / "generated"
from model_lock import Lease
MODEL_LEASE = Lease(ROOT)

BUILD_INPUTS = source_hash(ROOT)
source_errors = documents.verify(ROOT)
if source_errors:
    raise RuntimeError('\n'.join(source_errors))

SOURCES = json.loads((DATA / "sources.json").read_text())

STOREYS = [
    ("Untergeschoss", -2.85, DATA / "basement.json"),
    ("Erdgeschoss", 0.00, DATA / "ground_floor.json"),
    ("Obergeschoss", 2.85, DATA / "first_floor.json"),
    ("Dachgeschoss", 5.70, DATA / "second_floor.json"),
]
OUTPUT = GENERATED / "haus_nord_essentials.ifc"

model = ifcopenshell.api.run("project.create_file", version="IFC4")
project = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="Haus Nord Architecture")
ifcopenshell.api.run("unit.assign_unit", model, length={"is_metric": True, "raw": "METERS"})

model_context = ifcopenshell.api.run("context.add_context", model, context_type="Model")
body_context = ifcopenshell.api.run(
    "context.add_context",
    model,
    context_type="Model",
    context_identifier="Body",
    target_view="MODEL_VIEW",
    parent=model_context,
)

glass_style = ifcopenshell.api.run("style.add_style", model, name="Glass")
ifcopenshell.api.run(
    "style.add_surface_style",
    model,
    style=glass_style,
    ifc_class="IfcSurfaceStyleShading",
    attributes={
        "SurfaceColour": {"Name": None, "Red": 0.6, "Green": 0.75, "Blue": 0.85},
        "Transparency": 0.7,
    },
)

wood_style = ifcopenshell.api.run("style.add_style", model, name="DoorWood")
ifcopenshell.api.run(
    "style.add_surface_style",
    model,
    style=wood_style,
    ifc_class="IfcSurfaceStyleShading",
    attributes={
        "SurfaceColour": {"Name": None, "Red": 0.52, "Green": 0.35, "Blue": 0.20},
        "Transparency": 0.0,
    },
)

# Opaque milky shading preserves privacy in solid IFC viewers; exact glass spec open.
frosted_glass_style = ifcopenshell.api.run('style.add_style', model, name='FrostedGlass')
ifcopenshell.api.run('style.add_surface_style', model, style=frosted_glass_style,
    ifc_class='IfcSurfaceStyleShading', attributes={
        'SurfaceColour': {'Name': None, 'Red': .83, 'Green': .86, 'Blue': .84}, 'Transparency': 0.0})


def glazing_style(definition):
    return frosted_glass_style if definition.get('glazing')=='frosted' else glass_style


def finish_props(entity, definition):
    if definition.get('props'):
        pset=ifcopenshell.api.run('pset.add_pset', model, product=entity, name='Pset_Product')
        ifcopenshell.api.run('pset.edit_pset', model, pset=pset, properties=definition['props'])


def door_style(definition):
    if not definition.get('rgb'):return wood_style
    style=ifcopenshell.api.run('style.add_style', model, name=definition['id']+'_finish')
    r,g,b=definition['rgb']
    ifcopenshell.api.run('style.add_surface_style', model, style=style,
        ifc_class='IfcSurfaceStyleShading', attributes={
            'SurfaceColour': {'Name': None, 'Red': r, 'Green': g, 'Blue': b}, 'Transparency': 0.0})
    return style

stair_finishes = Finishes(model)

site = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite", name="Property Site")
building = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuilding", name="Haus Nord")
ifcopenshell.api.run("aggregate.assign_object", model, products=[site], relating_object=project)
ifcopenshell.api.run("aggregate.assign_object", model, products=[building], relating_object=site)


_OBJ_CACHE = {}


def read_obj(path):
    """OBJ (v/f only) -> (verts, fan-triangulated 0-based faces); parsed once per build."""
    path = ROOT / path
    key = (path, path.stat().st_mtime_ns)
    if key not in _OBJ_CACHE:
        verts, faces = [], []
        for line in path.read_text().splitlines():
            parts = line.split()
            if not parts:
                continue
            if parts[0] == 'v':
                verts.append(tuple(float(c) for c in parts[1:4]))
            elif parts[0] == 'f':
                idx = [int(p.split('/')[0]) - 1 for p in parts[1:]]
                for k in range(1, len(idx) - 1):  # fan-triangulate
                    faces.append((idx[0], idx[k], idx[k + 1]))
        _OBJ_CACHE[key] = (verts, faces)
    return _OBJ_CACHE[key]


def triangulated_face_set(points, triangles):
    """IfcTriangulatedFaceSet from float xyz points and 0-based triangles.

    The big aggregates go straight to the wrapper: ifcopenshell's generic setter
    walks every single float looking for entity references (3.6 s of a 10 s build).
    """
    pts = model.createIfcCartesianPointList3D()
    _SETTERS[pts.is_a(True)][0](pts.wrapped_data, 0, [[float(c) for c in p] for p in points])
    face_set = model.createIfcTriangulatedFaceSet(pts)
    _SETTERS[face_set.is_a(True)][3](face_set.wrapped_data, 3, [[a + 1, b + 1, c + 1] for a, b, c in triangles])
    return face_set


def mesh_item(path, at=(0.0, 0.0, 0.0), rotation_z=0.0, scale=1.0):
    """OBJ (v/f only) -> IfcTriangulatedFaceSet, placed at `at`, rotated about z."""
    verts, faces = read_obj(path)
    rad = math.radians(rotation_z)
    cr, sr = math.cos(rad), math.sin(rad)
    tx, ty, tz = at
    placed = [
        (
            (x * cr - y * sr) * scale + tx,
            (x * sr + y * cr) * scale + ty,
            z * scale + tz,
        )
        for x, y, z in verts
    ]
    return triangulated_face_set(placed, faces)


def build_storey(name, elevation, plan_file):
    with open(plan_file) as f:
        plan = json.load(f)
    from products import augment
    plan = augment(plan, {"Untergeschoss": "UG", "Erdgeschoss": "EG", "Obergeschoss": "OG", "Dachgeschoss": "DG"}[name], ROOT)
    storey_height = plan["building_dimensions"]["storey_height"]

    storey = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuildingStorey", name=name)
    storey.Elevation = elevation
    source_id = {"Untergeschoss": "floor-ug", "Erdgeschoss": "floor-eg",
                 "Obergeschoss": "floor-og", "Dachgeschoss": "floor-dg"}[name]
    model_source = documents.model_source(source_id, plan_file.relative_to(ROOT), plan.get('_source', ''), ROOT)
    source_pset = ifcopenshell.api.run('pset.add_pset', model, product=storey, name='Pset_ModelSource')
    ifcopenshell.api.run('pset.edit_pset', model, pset=source_pset, properties=model_source)
    ifcopenshell.api.run("aggregate.assign_object", model, products=[storey], relating_object=building)

    def closed_polyline(pts):
        return model.createIfcPolyline(
            [model.createIfcCartesianPoint((float(x), float(y))) for x, y in list(pts) + [pts[0]]]
        )
    def box_solid(x0, y0, z0, x1, y1, z1):
        prof = model.createIfcArbitraryClosedProfileDef(
            "AREA", None, closed_polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        )
        return model.createIfcExtrudedAreaSolid(
            prof,
            model.createIfcAxis2Placement3D(model.createIfcCartesianPoint((0.0, 0.0, float(z0))), None, None),
            model.createIfcDirection((0.0, 0.0, 1.0)),
            float(z1 - z0),
        )
    def add_product(ifc_class, name, items, z_offset):
        prod = ifcopenshell.api.run("root.create_entity", model, ifc_class=ifc_class, name=name)
        ifcopenshell.api.run("spatial.assign_container", model, products=[prod], relating_structure=storey)
        matrix = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, z_offset],
            [0.0, 0.0, 0.0, 1.0],
        ]
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=prod, matrix=matrix)
        rep_type = "Tessellation" if any(it.is_a("IfcTessellatedItem") for it in items) else "SweptSolid"
        rep = model.createIfcShapeRepresentation(body_context, "Body", rep_type, items)
        ifcopenshell.api.run("geometry.assign_representation", model, product=prod, representation=rep)
        return prod

    walls = {}
    for wd in plan["exterior_walls"] + plan["interior_walls"]:
        x1, y1 = wd["start"]
        x2, y2 = wd["end"]
        thickness = wd["thickness"]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        cos_t, sin_t = dx / length, dy / length

        wall = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcWallStandardCase", name=wd["id"])
        ifcopenshell.api.run("spatial.assign_container", model, products=[wall], relating_structure=storey)
        matrix = [
            [cos_t, -sin_t, 0.0, (x1 + x2) / 2.0],
            [sin_t, cos_t, 0.0, (y1 + y2) / 2.0],
            [0.0, 0.0, 1.0, elevation],
            [0.0, 0.0, 0.0, 1.0],
        ]
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=wall, matrix=matrix)
        profile = model.createIfcRectangleProfileDef(ProfileType="AREA", XDim=float(length), YDim=float(thickness))
        rep = ifcopenshell.api.run(
            "geometry.add_profile_representation", model, context=body_context, profile=profile,
            depth=float(wd.get("height", storey_height)),
        )
        ifcopenshell.api.run("geometry.assign_representation", model, product=wall, representation=rep)
        walls[wd["id"]] = {"entity": wall, "start": (x1, y1), "cos": cos_t, "sin": sin_t, "thickness": thickness}

    def xy_profile_solid(pts, z0, z1):
        """(x,y) polygon extruded in z — flat floor annotations (door swings)."""
        prof = model.createIfcArbitraryClosedProfileDef(
            "AREA", None,
            model.createIfcPolyline(
                [model.createIfcCartesianPoint((float(x), float(y))) for x, y in list(pts) + [pts[0]]]
            ),
        )
        pos = model.createIfcAxis2Placement3D(
            model.createIfcCartesianPoint((0.0, 0.0, float(z0))),
            model.createIfcDirection((0.0, 0.0, 1.0)),
            model.createIfcDirection((1.0, 0.0, 0.0)),
        )
        return model.createIfcExtrudedAreaSolid(prof, pos, model.createIfcDirection((0.0, 0.0, 1.0)), float(z1 - z0))

    def yz_profile_solid(x0, x1, pts):
        """(y,z) polygon extruded along x — used for gables, sloped partitions, roof planes."""
        prof = model.createIfcArbitraryClosedProfileDef(
            "AREA", None,
            model.createIfcPolyline(
                [model.createIfcCartesianPoint((float(y), float(z))) for y, z in list(pts) + [pts[0]]]
            ),
        )
        pos = model.createIfcAxis2Placement3D(
            model.createIfcCartesianPoint((float(x0), 0.0, 0.0)),
            model.createIfcDirection((1.0, 0.0, 0.0)),
            model.createIfcDirection((0.0, 1.0, 0.0)),
        )
        return model.createIfcExtrudedAreaSolid(prof, pos, model.createIfcDirection((0.0, 0.0, 1.0)), float(x1 - x0))

    for pw in plan.get("profile_walls", []):
        wall = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcWall", name=pw["id"])
        ifcopenshell.api.run("spatial.assign_container", model, products=[wall], relating_structure=storey)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=wall)
        solid = yz_profile_solid(pw["x0"], pw["x1"], pw["profile"])
        rep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", [solid])
        ifcopenshell.api.run("geometry.assign_representation", model, product=wall, representation=rep)
        finish_props(wall, pw)
        if pw.get("rgb"):
            r, g, b = pw["rgb"]
            pstyle = ifcopenshell.api.run("style.add_style", model, name=f"{pw['id']}_style")
            ifcopenshell.api.run(
                "style.add_surface_style", model, style=pstyle,
                ifc_class="IfcSurfaceStyleShading",
                attributes={"SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b}, "Transparency": 0.0},
            )
            ifcopenshell.api.run("style.assign_representation_styles", model, shape_representation=rep, styles=[pstyle])
        walls[pw["id"]] = {"entity": wall, "profile": True}

    roof_window = plan.get("roof_window")
    roof_entities = {}
    for rp in plan.get("roof_planes", []):
        if rp["id"].startswith("DACHFENSTER") and roof_window:
            roof = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcWindow", name=rp["id"])
            roof.PredefinedType = "WINDOW"
            roof.OverallWidth = roof_window["width_m"]
            roof.OverallHeight = roof_window["length_m"]
            pset = ifcopenshell.api.run("pset.add_pset", model, product=roof, name="Pset_RoofWindow")
            ifcopenshell.api.run(
                "pset.edit_pset", model, pset=pset,
                properties={
                    "Product": roof_window["product"],
                    "Glazing": roof_window["glazing"],
                    "Width": roof_window["width_m"],
                    "Length": roof_window["length_m"],
                    "Exterior": roof_window["exterior"],
                    "Interior": roof_window["interior"],
                    "SunProtection": roof_window["sun_protection"],
                    "Notes": roof_window["notes"],
                    "Source": roof_window["_source"],
                },
            )
        else:
            roof = ifcopenshell.api.run("root.create_entity", model, ifc_class=rp.get("ifc_class", "IfcSlab"), name=rp["id"])
            roof.PredefinedType = rp.get("predefined_type", "ROOF")
        ifcopenshell.api.run("spatial.assign_container", model, products=[roof], relating_structure=storey)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=roof)
        roof_entities[rp["id"]] = roof
        lining = plan["roof_finishes"][rp["interior_finish"]] if rp.get("interior_finish") else None
        roof_parts = []
        if rp.get('parts'):
            items = []
            for part in rp['parts']:
                part_items = [yz_profile_solid(p['x0'], p['x1'], p['profile']) for p in part['prisms']]
                for item in part_items:
                    item.SweptArea.ProfileName = part['name']
                items.extend(part_items)
                roof_parts.append((part, part_items))
        elif lining:
            lining_profile, core_profile = split_lining(rp["profile"], lining["thickness_m"])
            items = [yz_profile_solid(rp["x0"], rp["x1"], core_profile),
                     yz_profile_solid(rp["x0"], rp["x1"], lining_profile)]
        else:
            items = [yz_profile_solid(rp["x0"], rp["x1"], rp["profile"])]
        rep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", items)
        ifcopenshell.api.run("geometry.assign_representation", model, product=roof, representation=rep)
        if rp.get("props"):
            label = rp['props'].get('Product')
            if label:
                roof.Description = label
                roof.ObjectType = label
                roof.Tag = rp['id']
            pset = ifcopenshell.api.run("pset.add_pset", model, product=roof, name="Pset_Product")
            ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties=rp["props"])
        if rp.get("style") == "glass":
            ifcopenshell.api.run("style.assign_representation_styles", model, shape_representation=rep, styles=[glazing_style(rp)])
        elif rp.get("rgb"):
            r, g, b = rp["rgb"]
            rstyle = ifcopenshell.api.run("style.add_style", model, name=f"{rp['id']}_style")
            ifcopenshell.api.run(
                "style.add_surface_style", model, style=rstyle,
                ifc_class="IfcSurfaceStyleShading",
                attributes={"SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b}, "Transparency": 0.0},
            )
            ifcopenshell.api.run("style.assign_representation_styles", model, shape_representation=rep, styles=[rstyle])

        for part, part_items in roof_parts:
            if part.get('style') == 'glass':
                part_style = glazing_style(rp)
            else:
                part_style = ifcopenshell.api.run('style.add_style', model, name=f"{rp['id']}_{part['name']}")
                r, g, b = part['rgb']
                ifcopenshell.api.run('style.add_surface_style', model, style=part_style,
                    ifc_class='IfcSurfaceStyleShading', attributes={
                        'SurfaceColour': {'Name': None, 'Red': r, 'Green': g, 'Blue': b}, 'Transparency': 0.0})
            for item in part_items:
                ifcopenshell.api.run('style.assign_item_style', model, item=item, style=part_style)

        if lining:
            # Style the real lining solid, after any whole-roof default style.
            # A single host keeps existing rooflight voids cutting both layers.
            r, g, b = lining["rgb"]
            finish_style = ifcopenshell.api.run("style.add_style", model, name=lining["name"])
            ifcopenshell.api.run(
                "style.add_surface_style", model, style=finish_style,
                ifc_class="IfcSurfaceStyleShading",
                attributes={"SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b}, "Transparency": 0.0},
            )
            ifcopenshell.api.run("style.assign_item_style", model, item=items[1], style=finish_style)
            pset = ifcopenshell.api.run("pset.add_pset", model, product=roof, name="Pset_InteriorFinish")
            ifcopenshell.api.run("pset.edit_pset", model, pset=pset,
                                properties={"Product": lining["name"], "NormalThickness_m": lining["thickness_m"], **lining["props"]})

    # Roof windows need a real hole: a Velux modelled as a glass plate on top of an
    # uncut roof plane reads as a sticker. For every roof plane with "cuts", bore the
    # host by the same (y,z) profile extended through the host up and down so it fully penetrates
    # the roof build-up. Profiles here are absolute z, like the roof planes themselves.
    for rp in plan.get("roof_planes", []):
        host_id = rp.get("cuts")
        if not host_id:
            continue
        if host_id not in roof_entities:
            raise KeyError(f'{rp["id"]}: cuts host {host_id!r} is not a roof plane on this storey')
        if rp.get('cut_parts'):
            bore_items = [yz_profile_solid(p['x0'], p['x1'], p['profile']) for p in rp['cut_parts']]
        else:
            host = next(h for h in plan["roof_planes"] if h["id"] == host_id)
            host_zs = {}
            for y, z in host["profile"]:
                host_zs.setdefault(y, []).append(z)
            bore_depth = max(max(zs)-min(zs) for zs in host_zs.values()) + 0.15
            zs_by_y = {}
            for y, z in rp["profile"]:
                zs_by_y.setdefault(round(y, 6), []).append(z)
            bore = [(y, z - bore_depth if z <= min(zs_by_y[round(y, 6)]) else z + bore_depth)
                    for y, z in rp["profile"]]
            bore_items = [yz_profile_solid(rp['x0'], rp['x1'], bore)]
        opening = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcOpeningElement",
                                       name=f'Opening_{rp["id"]}')
        ifcopenshell.api.run("spatial.assign_container", model, products=[opening], relating_structure=storey)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=opening)
        orep = model.createIfcShapeRepresentation(
            body_context, "Body", "SweptSolid", bore_items)
        ifcopenshell.api.run("geometry.assign_representation", model, product=opening, representation=orep)
        ifcopenshell.api.run("feature.add_feature", model, feature=opening, element=roof_entities[host_id])

    pv = plan.get("pv")
    if pv:
        mod, mount, roofs = pv["module"], pv["mounting"], pv["roofs"]
        r, g, b = mount["rgb"]
        pv_style = ifcopenshell.api.run("style.add_style", model, name="PV_module_style")
        ifcopenshell.api.run(
            "style.add_surface_style", model, style=pv_style,
            ifc_class="IfcSurfaceStyleShading",
            attributes={"SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b}, "Transparency": 0.0},
        )
        skylights = [
            (rp["id"], rp["x0"], rp["x1"], min(pt[0] for pt in rp["profile"]), max(pt[0] for pt in rp["profile"]))
            for rp in plan.get("roof_planes", []) if rp["id"].startswith("DACHFENSTER_S")
        ]
        for p in pv["panels"]:
            roof = roofs[p["roof"]]
            slope = roof["dzdy"]
            cos_t = 1.0 / math.sqrt(1.0 + slope * slope)
            y0 = p["y0"]
            y1 = y0 + mod["width_m"] * cos_t          # on-slope module height, foreshortened
            t_z = mod["thickness_m"] / cos_t          # module + clearance measured perpendicular,
            off_z = mount["clearance_m"] / cos_t      # expressed as vertical offsets
            def surf(y):
                return roof["z0"] + (y - roof["y0"]) * slope + off_z
            z0, z1 = surf(y0), surf(y1)
            profile = [[y0, z0], [y1, z1], [y1, z1 + t_z], [y0, z0 + t_z]]
            for sid, sx0, sx1, sy0, sy1 in skylights:
                if p["x0"] < sx1 and p["x0"] + mod["length_m"] > sx0 and y0 < sy1 and y1 > sy0:
                    raise SystemExit(f"PV layout error: {p['id']} overlaps skylight {sid}")
            panel = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSolarDevice", name=p["id"])
            panel.PredefinedType = "SOLARPANEL"
            ifcopenshell.api.run("spatial.assign_container", model, products=[panel], relating_structure=storey)
            ifcopenshell.api.run("geometry.edit_object_placement", model, product=panel)
            solid = yz_profile_solid(p["x0"], p["x0"] + mod["length_m"], profile)
            rep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", [solid])
            ifcopenshell.api.run("geometry.assign_representation", model, product=panel, representation=rep)
            ifcopenshell.api.run("style.assign_representation_styles", model, shape_representation=rep, styles=[pv_style])
            pset = ifcopenshell.api.run("pset.add_pset", model, product=panel, name="Pset_SolarModule")
            ifcopenshell.api.run(
                "pset.edit_pset", model, pset=pset,
                properties={
                    "Module": mod["name"],
                    "Length": mod["length_m"],
                    "Width": mod["width_m"],
                    "Thickness": mod["thickness_m"],
                    "Weight_kg": mod["weight_kg"],
                    "NominalPower_Wp": mod["nominal_power_wp"],
                    "OpenCircuitVoltage_V": mod["voc_v"],
                    "TempCoeffVoc": mod["temp_coeff_voc"],
                    "Mounting": mount["system"],
                },
            )

    detailed_windows = [w for w in plan.get('windows', []) if 'parts' in w]
    for wa in plan.get("windows_abs", []) + detailed_windows:
        # Absolute coordinates: stepped reveals may use several connected boxes.
        # The nominal box remains the inside aperture for fixture/layout tools.
        def absolute_box(box):
            a, b, c, d, e, f = box
            if not all(math.isfinite(v) for v in box) or not (a < d and b < e and c < f):
                raise ValueError(f"{wa['id']}: invalid absolute opening/part box {box}")
            return box_solid(a, b, c-elevation, d, e, f-elevation)

        host = walls[wa["host"]]["entity"]
        x0, y0, z0, x1, y1, z1 = wa["box"]
        opening = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcOpeningElement", name=f"Opening_{wa['id']}")
        ifcopenshell.api.run("spatial.assign_container", model, products=[opening], relating_structure=storey)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=opening)
        # place absolute: shift by elevation via matrix
        mat = [[1.0,0.0,0.0,0.0],[0.0,1.0,0.0,0.0],[0.0,0.0,1.0,elevation],[0.0,0.0,0.0,1.0]]
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=opening, matrix=mat)
        if 'opening_boxes' in wa:
            bores = [absolute_box(b) for b in wa['opening_boxes']]
        else:
            bore = [x0-.05,y0,z0,x1+.05,y1,z1] if wa.get('cut_axis')=='x' else [x0,y0-.05,z0,x1,y1+.05,z1]
            bores = [absolute_box(bore)]
        if not bores:
            raise ValueError(f"{wa['id']}: opening_boxes cannot be empty")
        orep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", bores)
        ifcopenshell.api.run("geometry.assign_representation", model, product=opening, representation=orep)
        ifcopenshell.api.run("feature.add_feature", model, feature=opening, element=host)

        if wa.get("fill") is False:
            continue
        cls = "IfcDoor" if wa.get("kind") == "door" else "IfcWindow"
        fill = ifcopenshell.api.run("root.create_entity", model, ifc_class=cls, name=wa["id"])
        if wa.get('nominal_width') is not None:
            fill.OverallWidth = float(wa['nominal_width'])
            fill.OverallHeight = float(wa['nominal_height'])
        ifcopenshell.api.run("spatial.assign_container", model, products=[fill], relating_structure=storey)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=fill, matrix=mat)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        styled_parts = []
        if 'parts' in wa:
            pieces = []
            for part in wa['parts']:
                part_items = [absolute_box(b) for b in part['boxes']]
                for item in part_items:
                    item.SweptArea.ProfileName = part['name']
                if not part_items:
                    raise ValueError(f"{wa['id']}: empty window part {part['name']}")
                pieces.extend(part_items)
                styled_parts.append((part, part_items))
            if not pieces:
                raise ValueError(f"{wa['id']}: parts cannot be empty")
        elif wa.get("frame_width") and wa.get("kind") == "door":
            # Survey box spans the casing; the actual clear opening excludes both jambs.
            frame = wa["frame_width"]
            assert wa.get("cut_axis") == "x" and y1-y0 > 2*frame
            pieces = [box_solid(x0, y0, z0-elevation, x1, y0+frame, z1-elevation),
                      box_solid(x0, y1-frame, z0-elevation, x1, y1, z1-elevation),
                      box_solid(x0, y0+frame, z1-elevation-frame, x1, y1-frame, z1-elevation),
                      box_solid(cx-.02, y0+frame, z0-elevation, cx+.02, y1-frame, z1-elevation-frame)]
        elif wa.get("cut_axis") == "x":
            pieces = [box_solid(cx-.03, y0, z0-elevation, cx+.03, y1, z1-elevation)]
        else:
            pieces = [box_solid(x0, cy-.03, z0-elevation, x1, cy+.03, z1-elevation)]
        frep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", pieces)
        ifcopenshell.api.run("geometry.assign_representation", model, product=fill, representation=frep)
        if wa.get("rgb"):
            r, g, b = wa["rgb"]
            wstyle = ifcopenshell.api.run("style.add_style", model, name=f"{wa['id']}_style")
            ifcopenshell.api.run(
                "style.add_surface_style", model, style=wstyle,
                ifc_class="IfcSurfaceStyleShading",
                attributes={"SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b}, "Transparency": 0.0},
            )
            style = wstyle
        else:
            style = wood_style if wa.get("kind") == "door" else glazing_style(wa)
        if styled_parts:
            for part, part_items in styled_parts:
                if part.get('glazing'):
                    part_style = glazing_style(part)
                else:
                    part_style = ifcopenshell.api.run('style.add_style', model,
                        name=f"{wa['id']}_{part['name']}")
                    r, g, b = part['rgb']
                    ifcopenshell.api.run('style.add_surface_style', model, style=part_style,
                        ifc_class='IfcSurfaceStyleShading', attributes={
                            'SurfaceColour': {'Name': None, 'Red': r, 'Green': g, 'Blue': b},
                            'Transparency': 0.0})
                for item in part_items:
                    model.createIfcStyledItem(item, [part_style], None)
        else:
            ifcopenshell.api.run("style.assign_representation_styles", model, shape_representation=frep, styles=[style])
        label = wa.get('props', {}).get('ProductLabel') or wa.get('props', {}).get('Product')
        if label:
            fill.Description = fill.ObjectType = label
            fill.Tag = wa['id']
        finish_props(fill, wa)
        ifcopenshell.api.run("feature.add_filling", model, opening=opening, element=fill)

    for win in plan["windows"]:
        if 'parts' in win:
            continue  # Detailed absolute assembly above retains its regular source ID/offset.
        parent = walls[win["wall_id"]]
        x1, y1 = parent["start"]
        cos_t, sin_t = parent["cos"], parent["sin"]
        offset, width, height, sill = win["offset_along_wall"], win["width"], win["height"], win["sill_height"]
        d_center = offset + width / 2.0
        wx, wy = x1 + d_center * cos_t, y1 + d_center * sin_t
        win_matrix = [
            [cos_t, -sin_t, 0.0, wx],
            [sin_t, cos_t, 0.0, wy],
            [0.0, 0.0, 1.0, elevation + sill],
            [0.0, 0.0, 0.0, 1.0],
        ]

        opening = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcOpeningElement", name=f"Opening_{win['id']}")
        ifcopenshell.api.run("spatial.assign_container", model, products=[opening], relating_structure=storey)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=opening, matrix=win_matrix)
        op_profile = model.createIfcRectangleProfileDef(
            ProfileType="AREA", XDim=float(width), YDim=float(parent["thickness"] + 0.10)
        )
        op_rep = ifcopenshell.api.run(
            "geometry.add_profile_representation", model, context=body_context, profile=op_profile,
            depth=float(height),
        )
        ifcopenshell.api.run("geometry.assign_representation", model, product=opening, representation=op_rep)
        ifcopenshell.api.run("feature.add_feature", model, feature=opening, element=parent["entity"])

        window = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcWindow", name=win["id"])
        ifcopenshell.api.run("spatial.assign_container", model, products=[window], relating_structure=storey)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=window, matrix=win_matrix)
        w_profile = model.createIfcRectangleProfileDef(ProfileType="AREA", XDim=float(width), YDim=0.08)
        w_rep = ifcopenshell.api.run(
            "geometry.add_profile_representation", model, context=body_context, profile=w_profile,
            depth=float(height),
        )
        ifcopenshell.api.run("geometry.assign_representation", model, product=window, representation=w_rep)
        ifcopenshell.api.run("style.assign_representation_styles", model, shape_representation=w_rep, styles=[glazing_style(win)])
        label = win.get('props', {}).get('ProductLabel') or win.get('props', {}).get('Product')
        if label:
            window.Description = window.ObjectType = label
            window.Tag = win['id']
        finish_props(window, win)
        ifcopenshell.api.run("feature.add_filling", model, opening=opening, element=window)

    for door in plan.get("doors", []):
        parent = walls[door["wall_id"]]
        x1, y1 = parent["start"]
        cos_t, sin_t = parent["cos"], parent["sin"]
        offset, width, height, sill = door["offset_along_wall"], door["width"], door["height"], door["sill_height"]
        d_center = offset + width / 2.0
        dx_, dy_ = x1 + d_center * cos_t, y1 + d_center * sin_t
        door_matrix = [
            [cos_t, -sin_t, 0.0, dx_],
            [sin_t, cos_t, 0.0, dy_],
            [0.0, 0.0, 1.0, elevation + sill],
            [0.0, 0.0, 0.0, 1.0],
        ]
        opening = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcOpeningElement", name=f"Opening_{door['id']}")
        ifcopenshell.api.run("spatial.assign_container", model, products=[opening], relating_structure=storey)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=opening, matrix=door_matrix)
        op_profile = model.createIfcRectangleProfileDef(
            ProfileType="AREA", XDim=float(width), YDim=float(parent["thickness"] + 0.10)
        )
        op_rep = ifcopenshell.api.run(
            "geometry.add_profile_representation", model, context=body_context, profile=op_profile,
            depth=float(height),
        )
        ifcopenshell.api.run("geometry.assign_representation", model, product=opening, representation=op_rep)
        ifcopenshell.api.run("feature.add_feature", model, feature=opening, element=parent["entity"])

        ifc_door = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcDoor", name=door["id"])
        ifcopenshell.api.run("spatial.assign_container", model, products=[ifc_door], relating_structure=storey)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=ifc_door, matrix=door_matrix)
        d_profile = model.createIfcRectangleProfileDef(ProfileType="AREA", XDim=float(width), YDim=0.05)
        d_rep = ifcopenshell.api.run(
            "geometry.add_profile_representation", model, context=body_context, profile=d_profile,
            depth=float(height),
        )
        ifcopenshell.api.run("geometry.assign_representation", model, product=ifc_door, representation=d_rep)
        ifcopenshell.api.run("style.assign_representation_styles", model, shape_representation=d_rep, styles=[door_style(door)])
        finish_props(ifc_door, door)
        ifcopenshell.api.run("feature.add_filling", model, opening=opening, element=ifc_door)

    for door in plan.get("free_doors", []):
        cx, cy = door["center"]
        axis_x = door.get("axis", "x") == "x"
        cos_t, sin_t = (1.0, 0.0) if axis_x else (0.0, 1.0)
        door_matrix = [
            [cos_t, -sin_t, 0.0, cx],
            [sin_t, cos_t, 0.0, cy],
            [0.0, 0.0, 1.0, elevation + door.get("sill_height", 0.0)],
            [0.0, 0.0, 0.0, 1.0],
        ]
        ifc_door = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcDoor", name=door["id"])
        ifcopenshell.api.run("spatial.assign_container", model, products=[ifc_door], relating_structure=storey)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=ifc_door, matrix=door_matrix)
        d_profile = model.createIfcRectangleProfileDef(
            ProfileType="AREA", XDim=float(door["width"]), YDim=float(door.get("thickness", 0.06))
        )
        d_rep = ifcopenshell.api.run(
            "geometry.add_profile_representation", model, context=body_context, profile=d_profile,
            depth=float(door["height"]),
        )
        ifcopenshell.api.run("geometry.assign_representation", model, product=ifc_door, representation=d_rep)
        ifcopenshell.api.run("style.assign_representation_styles", model, shape_representation=d_rep, styles=[door_style(door)])
        finish_props(ifc_door, door)


    def recess_floor(product, rects, bottom, top):
        """Local installation pockets for full-height windows, absolute source boxes."""
        for window in plan.get('windows', []) + plan.get('windows_abs', []):
            cuts = []
            for a,b,c,d,e,f in window.get('floor_recess_boxes', []):
                if min(f, elevation+top)-max(c, elevation+bottom) <= 1e-8:
                    continue
                if not any(min(d,x1)-max(a,x0)>1e-8 and min(e,y1)-max(b,y0)>1e-8
                           for x0,y0,x1,y1 in rects):
                    continue
                cuts.append(box_solid(a,b,c,d,e,f))
            if not cuts:
                continue
            opening = ifcopenshell.api.run('root.create_entity', model, ifc_class='IfcOpeningElement',
                                          name=f"FloorPocket_{window['id']}_{product.Name}")
            ifcopenshell.api.run('geometry.edit_object_placement', model, product=opening)
            rep = model.createIfcShapeRepresentation(body_context, 'Body', 'SweptSolid', cuts)
            ifcopenshell.api.run('geometry.assign_representation', model, product=opening, representation=rep)
            ifcopenshell.api.run('feature.add_feature', model, feature=opening, element=product)

    for slab_def in plan.get("slabs", []):
        th = slab_def["thickness"]
        slab = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSlab", name=slab_def["id"])
        ifcopenshell.api.run("spatial.assign_container", model, products=[slab], relating_structure=storey)
        z0 = elevation + slab_def.get("z_top", 0.0) - th
        matrix = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=slab, matrix=matrix)
        items = []
        for (x0, y0, x1, y1), cell_bottom, cell_top in slab_solids(slab_def, plan.get("floorings", [])):
            z0 = elevation + cell_bottom
            prof = model.createIfcArbitraryClosedProfileDef(
                "AREA", None, closed_polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
            )
            items.append(model.createIfcExtrudedAreaSolid(
                prof,
                model.createIfcAxis2Placement3D(model.createIfcCartesianPoint((0.0, 0.0, float(z0))), None, None),
                model.createIfcDirection((0.0, 0.0, 1.0)),
                float(elevation + cell_top - z0),
            ))
        rep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", items)
        ifcopenshell.api.run("geometry.assign_representation", model, product=slab, representation=rep)
        recess_floor(slab, slab_def.get('rects', [slab_def.get('rect')]),
                     slab_def.get('z_top',0)-th, slab_def.get('z_top',0))

        if slab_def.get("props"):
            pset = ifcopenshell.api.run("pset.add_pset", model, product=slab, name="Pset_Product")
            ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties=slab_def["props"])

    for layer in plan.get("floor_build_up", []):
        items = [box_solid(x0,y0,layer["z_bottom"],x1,y1,layer["z_top"]) for x0,y0,x1,y1 in layer["rects"]]
        product = add_product("IfcCovering", layer["id"], items, elevation)
        product.PredefinedType = layer["predefined_type"]
        recess_floor(product, layer['rects'], layer['z_bottom'], layer['z_top'])
        pset = ifcopenshell.api.run("pset.add_pset", model, product=product, name="Pset_Product")
        ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties=layer["props"])
        style = ifcopenshell.api.run("style.add_style", model, name=layer["id"]+"_style")
        r,g,b = layer["rgb"]
        ifcopenshell.api.run("style.add_surface_style", model, style=style, ifc_class="IfcSurfaceStyleShading",
            attributes={"SurfaceColour":{"Name":None,"Red":r,"Green":g,"Blue":b},"Transparency":0.0})
        ifcopenshell.api.run("style.assign_representation_styles", model,
            shape_representation=product.Representation.Representations[0], styles=[style])

    for st in plan.get("stairs", []):
        # Precast sawtooth flight per shop drawing 25211 (Fertigteil Treppen):
        # 15 steps 19/27, 16cm Laufplatte (= 0.19 vertical waist under nosings).
        x0, x1 = st["x_range"]
        y0 = st["y_start"]
        n, rise, tread = st["risers"], st["riser_h"], st["tread"]
        waist_v = st["waist_vertical"]
        direction = 1.0 if st["y_end"] > y0 else -1.0
        # The existing heights are FINISHED step levels. Lower the complete
        # concrete core by the finish assembly, preserving its waist thickness.
        finish_depth = build_up(st)
        # profile polygon in (y, z), extruded along x
        pts = []
        for i in range(n):
            yy = y0 + i * tread * direction
            pts.append((yy, i * rise))
            pts.append((yy, (i + 1) * rise))
            pts.append((yy + tread * direction, (i + 1) * rise))
        y_top = y0 + n * tread * direction
        z_top = n * rise
        pts.append((y_top, z_top - waist_v))                      # vertical end cut
        pts.append((y0 + tread * direction, 0.0))                 # soffit, parallel to slope
        pts = [(y, z - finish_depth) for y, z in pts]
        prof = model.createIfcArbitraryClosedProfileDef(
            "AREA", None,
            model.createIfcPolyline(
                [model.createIfcCartesianPoint((float(y), float(z))) for y, z in pts + [pts[0]]]
            ),
        )
        # profile (y,z)-plane placed so local X=world Y, local Y=world Z, extrusion=world X
        pos = model.createIfcAxis2Placement3D(
            model.createIfcCartesianPoint((float(x0), 0.0, 0.0)),
            model.createIfcDirection((1.0, 0.0, 0.0)),
            model.createIfcDirection((0.0, 1.0, 0.0)),
        )
        solid = model.createIfcExtrudedAreaSolid(prof, pos, model.createIfcDirection((0.0, 0.0, 1.0)), float(x1 - x0))
        flight = add_product("IfcStairFlight", st["id"], [solid], elevation)
        stair_finishes.flight(st, flight, add_product, box_solid, elevation)

        # railing: sloped handrail (approximated per step) + posts, on requested sides
        # Balustrade per AF_D4_20_001: vertical rods standing on the storey floor
        # (full height through the void), max 12cm spacing, sloped WOOD handrail
        # 90cm above the nosing line.
        hr_h = 0.90
        def nosing_z(yy):
            t = (yy - y0) / (y_top - y0)
            return rise + t * (z_top - rise)
        ox0, ox1 = st.get("opening_x_range", [x0 - 0.06, x1 + 0.06])
        for side_def in st.get("railings", []):
            side = side_def if isinstance(side_def, str) else side_def["side"]
            rod_y_end = None if isinstance(side_def, str) else side_def.get("rod_y_end")
            mount = "flight" if isinstance(side_def, dict) and side_def.get("mount") == "flight" else (
                "flight" if isinstance(side_def, str) else side_def.get("mount", "flight"))
            # balustrade rides ON the flight: rods stand on the treads;
            # storey-edge protection is a separate floor-standing railing
            # floor screens stand at the slab-opening edge; flight rails on the flight
            rx = (ox0 + 0.012) if side == "low_x" else (ox1 - 0.012)
            if mount == "flight":
                rx = (x0 + 0.03) if side == "low_x" else (x1 - 0.03)
            rods = []
            rod_z_base = 0.0 if isinstance(side_def, str) else side_def.get("rod_z_base", 0.0)
            make_rods = True if isinstance(side_def, str) else side_def.get("rods", True)
            span = abs(y_top - y0)
            n_rods = int(span / 0.115) + 1
            for i in range(n_rods + 1):
                if not make_rods:
                    break
                yy = y0 + (span * i / n_rods) * direction
                if rod_y_end is not None and (yy - rod_y_end) * direction > 0:
                    continue   # wall takes over as barrier beyond this point
                if mount == "flight":
                    step_i = min(int(abs(yy - y0) / tread), n - 1)
                    z_bot = (step_i + 1) * rise
                else:
                    z_bot = rod_z_base
                z_topr = nosing_z(yy) + hr_h - 0.06
                if z_topr > z_bot:
                    rods.append(box_solid(rx - 0.008, yy - 0.008, z_bot, rx + 0.008, yy + 0.008, z_topr))
            if rods:
                balustrade = add_product("IfcRailing", f"{st['id']}_BALUSTRADE_{side}", rods, elevation)
                stair_finishes.railing(balustrade)

            def sloped_rail(ya, yb, x_center):
                pts_ = [
                    (ya, nosing_z(ya) + hr_h - 0.06),
                    (yb, nosing_z(yb) + hr_h - 0.06),
                    (yb, nosing_z(yb) + hr_h),
                    (ya, nosing_z(ya) + hr_h),
                ]
                prof_ = model.createIfcArbitraryClosedProfileDef(
                    "AREA", None,
                    model.createIfcPolyline(
                        [model.createIfcCartesianPoint((float(y), float(z))) for y, z in pts_ + [pts_[0]]]
                    ),
                )
                pos_ = model.createIfcAxis2Placement3D(
                    model.createIfcCartesianPoint((float(x_center - 0.025), 0.0, 0.0)),
                    model.createIfcDirection((1.0, 0.0, 0.0)),
                    model.createIfcDirection((0.0, 1.0, 0.0)),
                )
                return model.createIfcExtrudedAreaSolid(prof_, pos_, model.createIfcDirection((0.0, 0.0, 1.0)), 0.05)

            # handrail ends where the rods end — beyond that the wall is the barrier
            hr_end = y_top if rod_y_end is None else rod_y_end
            hr_items = [sloped_rail(y0, hr_end, rx)]
            handrail = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcRailing", name=f"{st['id']}_HANDRAIL_{side}")
            ifcopenshell.api.run("spatial.assign_container", model, products=[handrail], relating_structure=storey)
            hmat = [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, elevation],
                [0.0, 0.0, 0.0, 1.0],
            ]
            ifcopenshell.api.run("geometry.edit_object_placement", model, product=handrail, matrix=hmat)
            hrep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", hr_items)
            ifcopenshell.api.run("geometry.assign_representation", model, product=handrail, representation=hrep)
            stair_finishes.railing(handrail, handrail=True)

    def cylinder_solid(cx, cy, z0, r, z1):
        prof = model.createIfcCircleProfileDef(
            "AREA", None,
            model.createIfcAxis2Placement2D(model.createIfcCartesianPoint((float(cx), float(cy))), None),
            float(r),
        )
        return model.createIfcExtrudedAreaSolid(
            prof,
            model.createIfcAxis2Placement3D(model.createIfcCartesianPoint((0.0, 0.0, float(z0))), None, None),
            model.createIfcDirection((0.0, 0.0, 1.0)),
            float(z1 - z0),
        )

    def sphere_solid(cx, cy, cz, r):
        sph = model.createIfcSphere(
            model.createIfcAxis2Placement3D(
                model.createIfcCartesianPoint((float(cx), float(cy), float(cz))), None, None
            ),
            float(r),
        )
        return model.createIfcCsgSolid(sph)

    for fl in plan.get("floorings", []):
        top = fl.get("z_top", 0.01)
        bottom = top - fl.get("thickness", 0.01)
        items = [box_solid(x0, y0, bottom, x1, y1, top) for x0, y0, x1, y1 in fl["rects"]]
        cov = add_product("IfcCovering", fl["id"], items, elevation)
        cov.PredefinedType = "FLOORING"
        recess_floor(cov, fl['rects'], bottom, top)
        if fl.get("props"):
            pset = ifcopenshell.api.run("pset.add_pset", model, product=cov, name="Pset_Product")
            ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties=fl["props"])
        r, g, b = fl["rgb"]
        fstyle = ifcopenshell.api.run("style.add_style", model, name=f"{fl['id']}_style")
        ifcopenshell.api.run(
            "style.add_surface_style", model, style=fstyle,
            ifc_class="IfcSurfaceStyleShading",
            attributes={"SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b}, "Transparency": 0.0},
        )
        rep = cov.Representation.Representations[0]
        ifcopenshell.api.run("style.assign_representation_styles", model, shape_representation=rep, styles=[fstyle])

    for wc in plan.get("wall_coverings", []):
        # wall tiles etc.: absolute boxes, thin, glued to a wall face
        items = [box_solid(*b) for b in wc["boxes"]]
        cov = add_product("IfcCovering", wc["id"], items, elevation)
        cov.PredefinedType = "CLADDING"
        r, g, b = wc["rgb"]
        wstyle = ifcopenshell.api.run("style.add_style", model, name=f"{wc['id']}_style")
        ifcopenshell.api.run(
            "style.add_surface_style", model, style=wstyle,
            ifc_class="IfcSurfaceStyleShading",
            attributes={"SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b}, "Transparency": 0.0},
        )
        rep = cov.Representation.Representations[0]
        ifcopenshell.api.run("style.assign_representation_styles", model, shape_representation=rep, styles=[wstyle])
        if wc.get("props"):
            pset = ifcopenshell.api.run("pset.add_pset", model, product=cov, name="Pset_Product")
            ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties=wc["props"])

    # Named rooms ("GYM"): IfcSpace aggregated into the storey, polygon extruded to the
    # real clear height, faintly transparent so viewers show the label, not a block.
    for sp in plan.get("spaces", []):
        prof = model.createIfcArbitraryClosedProfileDef("AREA", None, closed_polyline([tuple(p) for p in sp["polygon"]]))
        solid = model.createIfcExtrudedAreaSolid(
            prof, model.createIfcAxis2Placement3D(model.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None),
            model.createIfcDirection((0.0, 0.0, 1.0)), float(sp["height"]))
        space = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSpace", name=sp["name"])
        space.LongName = sp.get("long_name")
        space.PredefinedType = "INTERNAL"
        ifcopenshell.api.run("aggregate.assign_object", model, products=[space], relating_object=storey)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=space,
                             matrix=[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, elevation], [0.0, 0.0, 0.0, 1.0]])
        rep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", [solid])
        ifcopenshell.api.run("geometry.assign_representation", model, product=space, representation=rep)
        sstyle = ifcopenshell.api.run("style.add_style", model, name=f"{sp['id']}_style")
        ifcopenshell.api.run("style.add_surface_style", model, style=sstyle, ifc_class="IfcSurfaceStyleShading",
                             attributes={"SurfaceColour": {"Name": None, "Red": 0.55, "Green": 0.75, "Blue": 0.95}, "Transparency": 0.85})
        ifcopenshell.api.run("style.assign_representation_styles", model, shape_representation=rep, styles=[sstyle])
        common = ifcopenshell.api.run("pset.add_pset", model, product=space, name="Pset_SpaceCommon")
        ifcopenshell.api.run("pset.edit_pset", model, pset=common, properties={"Reference": sp["props"].get("RoomNumber", sp["name"])})
        if sp.get("props"):
            pset = ifcopenshell.api.run("pset.add_pset", model, product=space, name="Pset_Room")
            ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties=sp["props"])

    for fu in plan.get("furniture", []) + plan.get("sanitary", []) + plan.get("door_hardware", []):
        items = [box_solid(*b) for b in fu.get("boxes", [])]
        items += [cylinder_solid(*c) for c in fu.get("cylinders", [])]
        items += [sphere_solid(*sp) for sp in fu.get("spheres", [])]
        items += [yz_profile_solid(p["x0"], p["x1"], p["profile"]) for p in fu.get("prisms", [])]
        # "parts": one product, several differently coloured pieces. A wardrobe is ONE
        # thing that happens to have a black carcass, white doors and black handles --
        # grouping by colour instead would model three half-objects.
        part_styled = []
        for part in fu.get("parts", []):
            if part.get('texture'):
                from image_textures import textured_box
                pit = [textured_box(model, b, part['texture'], ROOT) for b in part.get('boxes', [])]
            else:
                pit = [box_solid(*b) for b in part.get("boxes", [])]
            pit += [cylinder_solid(*c) for c in part.get("cylinders", [])]
            pit += [sphere_solid(*sp) for sp in part.get("spheres", [])]
            # Furniture profiles use storey-local Z, just like furniture boxes.
            pit += [yz_profile_solid(p["x0"], p["x1"], p["profile"]) for p in part.get("prisms", [])]
            if part.get("mesh"):
                pit.append(mesh_item(part["mesh"], part.get("at", (0.0, 0.0, 0.0)),
                                     part.get("rotation_z", 0.0), part.get("scale", 1.0)))
            items += pit
            if part.get("rgb") and not part.get('texture'):
                part_styled.append((pit, part["rgb"], part.get("name", "part"),
                                    float(part.get("transparency", 0.0))))
        if fu.get("mesh"):
            items.append(mesh_item(fu["mesh"], fu.get("at", (0.0, 0.0, 0.0)),
                                   fu.get("rotation_z", 0.0), fu.get("scale", 1.0)))
        piece = add_product(fu.get("ifc_class", "IfcFurnishingElement"), fu["id"], items, elevation)
        product_label = fu.get('props', {}).get('ProductLabel') or fu.get('props', {}).get('Product')
        if product_label:
            piece.Description = product_label
            piece.Tag = fu['id']
            piece.ObjectType = product_label
        if fu.get("predefined_type"):
            piece.PredefinedType = fu["predefined_type"]
        if fu.get("object_type"):                # required with PredefinedType USERDEFINED
            piece.ObjectType = fu["object_type"]
        if fu.get("props"):
            pset = ifcopenshell.api.run("pset.add_pset", model, product=piece, name="Pset_Product")
            ifcopenshell.api.run("pset.edit_pset", model, pset=pset,
                                 properties={**fu["props"], 'TechnicalID': fu['id']})
        for pit, (r, g, b), pname, transparency in part_styled:
            pstyle = ifcopenshell.api.run("style.add_style", model, name=f"{fu['id']}_{pname}_style")
            ifcopenshell.api.run(
                "style.add_surface_style", model, style=pstyle,
                ifc_class="IfcSurfaceStyleShading",
                attributes={"SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b},
                            "Transparency": transparency},
            )
            for it in pit:                       # style the individual solids, not the product
                model.createIfcStyledItem(it, [pstyle], None)
        if fu.get("rgb"):
            r, g, b = fu["rgb"]
            fstyle = ifcopenshell.api.run("style.add_style", model, name=f"{fu['id']}_style")
            ifcopenshell.api.run(
                "style.add_surface_style", model, style=fstyle,
                ifc_class="IfcSurfaceStyleShading",
                attributes={"SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b},
                            "Transparency": float(fu.get("transparency", 0.0))},
            )
            # Product colour is the default (e.g. a mesh frame). Explicitly
            # coloured parts, such as a tabletop, retain their own finishes.
            for it in items:
                if not it.StyledByItem:
                    model.createIfcStyledItem(it, [fstyle], None)

    # Door swings, flat on the floor: the swept quarter-disc plus a bar for the leaf
    # where it ends up when fully open. Geometry comes from data (see
    # src/extract_door_swings.py, which reads the arcs off the architect plans).
    SWING_RGB = {"door": (0.95, 0.45, 0.15), "balcony_door": (0.20, 0.55, 0.90)}
    for sw in plan.get("door_swings", []):
        hx, hy = sw["hinge"]
        cx, cy = sw["closed"]
        ox, oy = sw["open"]
        r = math.dist((hx, hy), (cx, cy))
        a0 = math.atan2(cy - hy, cx - hx)
        a1 = math.atan2(oy - hy, ox - hx)
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi     # shortest sweep, keeps 90 deg at 90 deg
        seg = 18
        sector = [(hx, hy)] + [(hx + r * math.cos(a0 + da * i / seg), hy + r * math.sin(a0 + da * i / seg))
                               for i in range(seg + 1)]
        ux, uy = (ox - hx) / r, (oy - hy) / r                  # leaf bar, 5 cm wide, at the open position
        px, py = -uy * 0.025, ux * 0.025
        leaf = [(hx + px, hy + py), (ox + px, oy + py), (ox - px, oy - py), (hx - px, hy - py)]
        items = [xy_profile_solid(sector, 0.002, 0.012), xy_profile_solid(leaf, 0.002, 0.030)]
        ann = add_product("IfcAnnotation", sw["id"], items, elevation)
        r_, g_, b_ = SWING_RGB.get(sw.get("kind"), SWING_RGB["door"])
        sstyle = ifcopenshell.api.run("style.add_style", model, name=f'{sw["id"]}_style')
        ifcopenshell.api.run(
            "style.add_surface_style", model, style=sstyle,
            ifc_class="IfcSurfaceStyleShading",
            attributes={"SurfaceColour": {"Name": None, "Red": r_, "Green": g_, "Blue": b_},
                        "Transparency": 0.45},
        )
        ifcopenshell.api.run("style.assign_representation_styles", model,
                             shape_representation=ann.Representation.Representations[0], styles=[sstyle])
        pset = ifcopenshell.api.run("pset.add_pset", model, product=ann, name="Pset_DoorSwing")
        ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={
            "Door": sw["door"], "LeafWidth": round(r, 3), "Kind": sw.get("kind", "door"),
            "Source": "AF_GRU_50 Grundrisse, Schwenkbogen vektoriell (src/extract_door_swings.py)",
        })

    # Steckdosen: a flat plate on the wall face it was snapped to. Position and
    # wall come from data (src/extract_outlets.py reads the symbols off the
    # Elektroplaene); 30 cm is the Achsmass over OK fertig Boden from the sheets.
    for o in plan.get("outlets", []):
        fx, fy = o["at"]
        nx, ny = o["normal"]
        gangs = int(o.get("gangs", 1))
        zc = o.get("sill", 0.30)
        # A Swiss 3 x T13 is ONE square box with three outlets in it, not three
        # plates side by side -- the cover stays 88 x 88 whatever the count.
        w = 0.088
        z0, z1 = zc - w / 2, zc + w / 2
        seats = [(0.0, 0.0)] if gangs == 1 else \
                [(0.0, 0.022), (-0.020, -0.016), (0.020, -0.016)][:gangs]
        if abs(nx) > 0.5:
            xa, xb = sorted((fx, fx + nx * 0.012))
            items = [box_solid(xa, fy - w / 2, z0, xb, fy + w / 2, z1)]
            for du, dv in seats:                      # T13 openings, 30 mm, slightly proud
                sa, sb = sorted((fx + nx * 0.012, fx + nx * 0.016))
                items.append(box_solid(sa, fy + du - 0.015, zc + dv - 0.015,
                                       sb, fy + du + 0.015, zc + dv + 0.015))
        else:
            ya, yb = sorted((fy, fy + ny * 0.012))
            items = [box_solid(fx - w / 2, ya, z0, fx + w / 2, yb, z1)]
            for du, dv in seats:
                sa, sb = sorted((fy + ny * 0.012, fy + ny * 0.016))
                items.append(box_solid(fx + du - 0.015, sa, zc + dv - 0.015,
                                       fx + du + 0.015, sb, zc + dv + 0.015))
        dose = add_product("IfcOutlet", o["id"], items, elevation)
        try:
            dose.PredefinedType = "POWEROUTLET"
        except Exception:
            pass
        ostyle = ifcopenshell.api.run("style.add_style", model, name=f'{o["id"]}_style')
        ifcopenshell.api.run(
            "style.add_surface_style", model, style=ostyle,
            ifc_class="IfcSurfaceStyleShading",
            attributes={"SurfaceColour": {"Name": None, "Red": 0.97, "Green": 0.97, "Blue": 0.96},
                        "Transparency": 0.0},
        )
        ifcopenshell.api.run("style.assign_representation_styles", model,
                             shape_representation=dose.Representation.Representations[0], styles=[ostyle])
        pset = ifcopenshell.api.run("pset.add_pset", model, product=dose, name="Pset_Product")
        ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={
            "Product": "Steckdose Typ 13 (Feller)",
            "Type": "T13",
            "Gangs": gangs,
            "Dimensions": f"8.8 x 8.8 cm Abdeckung, {gangs} x T13 im selben Gehäuse, Achse {round(zc*100)} cm ab OK fertig Boden",
            "MountingHeight": zc,
            "HostWall": o.get("wall", ""),
        })

    # Reviewed electrical-plan switch banks, mounted at 105 cm above this storey.
    from light_switches import boxes as switch_boxes
    for switch in plan.get("switches", []):
        items = [box_solid(*b) for b in switch_boxes(switch)]
        device = add_product("IfcSwitchingDevice", switch["id"], items, elevation)
        device.PredefinedType = "USERDEFINED"
        device.ObjectType = "LIGHTSWITCHBANK"
        style = ifcopenshell.api.run("style.add_style", model, name=f'{switch["id"]}_style')
        ifcopenshell.api.run("style.add_surface_style", model, style=style,
                            ifc_class="IfcSurfaceStyleShading", attributes={
                                "SurfaceColour": {"Name": None, "Red": .97, "Green": .97, "Blue": .96},
                                "Transparency": 0.0})
        ifcopenshell.api.run("style.assign_representation_styles", model,
                            shape_representation=device.Representation.Representations[0], styles=[style])
        pset = ifcopenshell.api.run("pset.add_pset", model, product=device, name="Pset_Product")
        ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties=switch["props"])
        source = ifcopenshell.api.run("pset.add_pset", model, product=device, name="Pset_ElectricalPlan")
        ifcopenshell.api.run("pset.edit_pset", model, pset=source, properties={
            "Controls": json.dumps(switch["controls"], ensure_ascii=False),
            "Columns": switch["columns"], "HostFace": switch.get("face", "side"),
        })

    for rl in plan.get("railings", []):
        # void-edge guard: same rod system, wood top rail
        (ax, ay), (bx, by) = rl["from"], rl["to"]
        h = rl.get("height", 1.0)
        rods = []
        if abs(bx - ax) >= abs(by - ay):   # runs in x
            lo, hi = min(ax, bx), max(ax, bx)
            n_rods = max(2, int((hi - lo) / 0.115) + 1)
            for i in range(n_rods + 1):
                px_ = lo + (hi - lo) * i / n_rods
                rods.append(box_solid(px_ - 0.008, ay - 0.008, 0.0, px_ + 0.008, ay + 0.008, h - 0.06))
            hr = box_solid(lo, ay - 0.025, h - 0.06, hi, ay + 0.025, h)
        else:
            lo, hi = min(ay, by), max(ay, by)
            n_rods = max(2, int((hi - lo) / 0.115) + 1)
            for i in range(n_rods + 1):
                py_ = lo + (hi - lo) * i / n_rods
                rods.append(box_solid(ax - 0.008, py_ - 0.008, 0.0, ax + 0.008, py_ + 0.008, h - 0.06))
            hr = box_solid(ax - 0.025, lo, h - 0.06, ax + 0.025, hi, h)
        balustrade = add_product("IfcRailing", rl["id"], rods, elevation)
        if internal_railing(rl["id"]):
            stair_finishes.railing(balustrade)
        handrail = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcRailing", name=f"{rl['id']}_HANDRAIL")
        ifcopenshell.api.run("spatial.assign_container", model, products=[handrail], relating_structure=storey)
        hmat = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, elevation],
            [0.0, 0.0, 0.0, 1.0],
        ]
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=handrail, matrix=hmat)
        hrep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", [hr])
        ifcopenshell.api.run("geometry.assign_representation", model, product=handrail, representation=hrep)
        if internal_railing(rl["id"]):
            stair_finishes.railing(handrail, handrail=True)
        else:
            ifcopenshell.api.run("style.assign_representation_styles", model, shape_representation=hrep, styles=[wood_style])

    if name == "Untergeschoss":
        from cellar_services import build as build_cellar_services
        build_cellar_services(model, body_context, storey, elevation)
    annotate_storey(storey, plan, model_source)
    return len(walls), len(plan["windows"])


def source_props(pid, storey=None):
    """Lieferant/Werkvertrag/Contract/Plan/Notes for a product id, from data/sources.json rules."""
    if pid.startswith("STAIR_") and ("_BALUSTRADE_" in pid or "_HANDRAIL_" in pid):
        pid = "RAIL_" + pid  # Metal/wood railing contract, not the concrete stair contract.
    for rule in SOURCES["rules"]:
        if pid.startswith(rule["prefix"]):
            props = {}
            wv = SOURCES["werkvertraege"].get(rule.get("wv", ""))
            if wv:
                props.update({"Lieferant": wv["lieferant"], "Werkvertrag": wv["werkvertrag"], "Contract": wv["contract"]})
            if rule.get("plan"):
                props["Plan"] = rule["plan"]
            if rule.get('plan_by_storey', {}).get(storey):
                props['Plan'] = rule['plan_by_storey'][storey]
            if rule.get("notes"):
                props["Notes"] = rule["notes"]
            return props
    return None


def dims_index(plan):
    """id -> dimension props, straight from the plan JSON."""
    idx = {}
    for w in plan.get("windows", []) + plan.get("doors", []):
        idx[w["id"]] = {"Width": w["width"], "Height": w["height"], "SillHeight": w.get("sill_height")}
    for fd in plan.get("free_doors", []):
        idx[fd["id"]] = {"Width": fd["width"], "Height": fd["height"], "Thickness": fd.get("thickness")}
    for wa in plan.get("windows_abs", []) + [w for w in plan.get('windows', []) if 'parts' in w]:
        x0, y0, z0, x1, y1, z1 = wa["box"]
        idx[wa["id"]] = {"Width": round(max(x1 - x0, y1 - y0), 3), "Height": round(z1 - z0, 3),
                         "Depth": round(min(x1 - x0, y1 - y0), 3)}
        if wa.get('opening_boxes'):
            idx[wa['id']]['DimensionMeaning'] = 'Inside aperture envelope; raw/exterior/frame/glazing dimensions are separate in Pset_Product'
        if wa.get("frame_width"):
            idx[wa["id"]].update(FrameFaceWidth=wa["frame_width"], ClearOpeningWidth=round(y1-y0-2*wa["frame_width"],3))
        if wa.get("_source"):
            idx[wa["id"]]["SourceInterpretation"] = wa["_source"]
    for w in plan.get("exterior_walls", []) + plan.get("interior_walls", []):
        (ax, ay), (bx, by) = w["start"], w["end"]
        idx[w["id"]] = {"Length": round(math.dist((ax, ay), (bx, by)), 3), "Thickness": w["thickness"],
                        "Height": w.get("height")}
    for pw in plan.get("profile_walls", []) + plan.get("roof_planes", []):
        ys = [p[0] for p in pw["profile"]]
        zs = [p[1] for p in pw["profile"]]
        idx[pw["id"]] = {"SpanX": round(pw["x1"] - pw["x0"], 3), "ExtentY": round(max(ys) - min(ys), 3),
                         "ExtentZ": round(max(zs) - min(zs), 3)}
    for s in plan.get("slabs", []):
        idx[s["id"]] = {"Thickness": s["thickness"]}
        if s.get('underside_zones'):
            idx[s['id']]['MaximumThickness'] = s.get('z_top', 0.) - min(z['z_bottom'] for z in s['underside_zones'])
            idx[s['id']]['ThicknessMeaning'] = 'Normal assembly thickness; MaximumThickness includes the local downstand'
    for r in plan.get("railings", []):
        idx[r["id"]] = {"Length": round(math.dist(r["from"], r["to"]), 3), "Height": r.get("height", 1.0)}
    for st in plan.get("stairs", []):
        idx[st["id"]] = {"Risers": st["risers"], "RiserHeight": st["riser_h"], "Tread": st["tread"],
                         "TotalRise": st["rise_total"]}
    for f in plan.get("furniture", []) + plan.get("sanitary", []):
        wants_extents = str(f["id"]).startswith("PERGOLA") or f.get("ifc_class")
        if wants_extents and f.get("boxes"):
            xs, ys, zs = [], [], []
            for x0, y0, z0, x1, y1, z1 in f["boxes"]:
                xs += [x0, x1]; ys += [y0, y1]; zs += [z0, z1]
            idx[f["id"]] = {"ExtentX": round(max(xs) - min(xs), 3), "ExtentY": round(max(ys) - min(ys), 3),
                            "ExtentZ": round(max(zs) - min(zs), 3)}
    for wc in plan.get("wall_coverings", []):
        zs = [z for b in wc["boxes"] for z in (b[2], b[5])]
        idx[wc["id"]] = {"Panels": len(wc["boxes"]), "TopHeight": round(max(zs), 3)}
    return idx


def annotate_storey(storey, plan, model_source):
    dims = dims_index(plan)
    source_overrides = {item['id']: documents.model_source(
        item['source_document'], model_source['SourceData'], item.get('_source', item.get('_comment', '')), ROOT)
        for items in plan.values() if isinstance(items, list)
        for item in items if isinstance(item, dict) and item.get('source_document')}
    seen = set()
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        if rel.RelatingStructure != storey:
            continue
        for e in rel.RelatedElements:
            if e.id() in seen:
                continue
            seen.add(e.id())
            pid = e.Name or ""
            if e.is_a() in ('IfcWall', 'IfcWallStandardCase', 'IfcSlab', 'IfcRoof', 'IfcDoor', 'IfcWindow', 'IfcStairFlight', 'IfcBeam', 'IfcColumn', 'IfcMember'):
                pset = ifcopenshell.api.run('pset.add_pset', model, product=e, name='Pset_ModelSource')
                ifcopenshell.api.run('pset.edit_pset', model, pset=pset, properties=source_overrides.get(pid, model_source))
            code = {"Untergeschoss": "UG", "Erdgeschoss": "EG", "Obergeschoss": "OG", "Dachgeschoss": "DG"}[storey.Name]
            src = source_props(pid, code)
            if src:
                pset = ifcopenshell.api.run("pset.add_pset", model, product=e, name="Pset_Source")
                ifcopenshell.api.run("pset.edit_pset", model, pset=pset,
                                     properties={k: v for k, v in src.items() if v})
            d = dims.get(pid)
            if d:
                d = {k: v for k, v in d.items() if v is not None}
                if d:
                    pset = ifcopenshell.api.run("pset.add_pset", model, product=e, name="Pset_Dimensions")
                    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties=d)


def build_site(site_file):
    """Terrain sketch, parcel boundaries and the Haus Süd foundation from data/site.json.

    Everything hangs off the IfcSite (not off a storey) and lives in the same local
    frame as the storeys: origin = SW outer corner of Haus Nord, z 0 = 462.00 m ue. M.
    """
    plan = json.loads(Path(site_file).read_text())
    ring = plan["property_boundary"]["ring"]
    terrain = plan["terrain"]

    def styled(name, rgb, transparency=0.0):
        st = ifcopenshell.api.run("style.add_style", model, name=name)
        r, g, b = rgb
        ifcopenshell.api.run(
            "style.add_surface_style", model, style=st, ifc_class="IfcSurfaceStyleShading",
            attributes={"SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b},
                        "Transparency": transparency},
        )
        return st

    def add(ifc_class, name, items, style=None, predefined=None):
        prod = ifcopenshell.api.run("root.create_entity", model, ifc_class=ifc_class, name=name)
        if predefined:
            prod.PredefinedType = predefined
        ifcopenshell.api.run("spatial.assign_container", model, products=[prod], relating_structure=site)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=prod)
        rep_type = "Tessellation" if any(it.is_a("IfcTessellatedItem") for it in items) else "SweptSolid"
        rep = model.createIfcShapeRepresentation(body_context, "Body", rep_type, items)
        ifcopenshell.api.run("geometry.assign_representation", model, product=prod, representation=rep)
        if style:
            ifcopenshell.api.run("style.assign_representation_styles", model,
                                 shape_representation=rep, styles=[style])
        return prod

    def face_set(verts, faces):
        return triangulated_face_set(verts, faces)

    def prism(poly, z_top_fn, z_base):
        """Closed triangle mesh over a convex polygon: planar top, flat bottom, side walls."""
        n = len(poly)
        verts = [(x, y, z_top_fn(x, y)) for x, y in poly] + [(x, y, z_base) for x, y in poly]
        faces = [(0, k, k + 1) for k in range(1, n - 1)]                    # top fan
        faces += [(n, n + k + 1, n + k) for k in range(1, n - 1)]           # bottom fan
        for i in range(n):                                                  # side quads
            j = (i + 1) % n
            faces += [(i, n + i, n + j), (i, n + j, j)]
        return face_set(verts, faces)

    # --- terrain height: two flat plateaus joined by one Böschung ---------------
    # boeschung_crest is the centreline of the plan's hatched Böschung band: three
    # parts west->east (straight, short diagonal rise, straight), running clear across
    # the parcel. It is single-valued in x, so "north of it" == the high plateau.
    crest_pts = terrain["boeschung_crest"]
    crest = shapely.LineString(crest_pts)
    crest_x = [p[0] for p in crest_pts]
    crest_y = [p[1] for p in crest_pts]
    z_hi, z_lo = terrain["plateau_north_z"], terrain["plateau_south_z"]
    half_width = terrain["boeschung_width"] / 2.0

    def terrain_z(x, y):
        side = 1.0 if y > np.interp(x, crest_x, crest_y) else -1.0
        d = crest.distance(shapely.Point(x, y))
        t = min(1.0, max(0.0, 0.5 + side * d / (2.0 * half_width)))
        return z_lo + t * (z_hi - z_lo)

    def extrude(poly, z0, height):
        prof = model.createIfcArbitraryClosedProfileDef(
            "AREA", None,
            model.createIfcPolyline([model.createIfcCartesianPoint((float(px), float(py)))
                                     for px, py in list(poly) + [poly[0]]]),
        )
        return model.createIfcExtrudedAreaSolid(
            prof,
            model.createIfcAxis2Placement3D(model.createIfcCartesianPoint((0.0, 0.0, float(z0))), None, None),
            model.createIfcDirection((0.0, 0.0, 1.0)), float(height),
        )

    # --- terrain: gridded surface over the parcel, skirt down to base_z ----------
    # The parcel ring is convex, so a Delaunay of (ring vertices + interior grid points)
    # tiles it exactly -- no clipping needed and the boundary stays true to the plan.
    ccw = ring if sum((ring[(i + 1) % len(ring)][0] - p[0]) * (ring[(i + 1) % len(ring)][1] + p[1])
                      for i, p in enumerate(ring)) < 0 else ring[::-1]
    parcel = shapely.Polygon(ccw)
    step = terrain["grid_step"]
    x0, y0, x1, y1 = parcel.bounds
    inner = parcel.buffer(-step * 0.5)
    # seed nodes along the crest and along both slope edges, so the Böschung reads as
    # three clean creases rather than whatever the grid happens to sample
    creases = [crest] + [shapely.offset_curve(crest, o) for o in (half_width, -half_width)]
    breaks = []
    for line in creases:
        for ln in shapely.get_parts(line):
            for s in np.arange(0.0, ln.length, step):
                pt = ln.interpolate(float(s))
                if inner.contains(pt):
                    breaks.append((round(pt.x, 4), round(pt.y, 4)))
    guard = shapely.MultiPoint(breaks).buffer(step * 0.5) if breaks else None
    grid = [(float(gx), float(gy))
            for gx in np.arange(x0 + step, x1, step)
            for gy in np.arange(y0 + step, y1, step)
            if inner.contains(shapely.Point(gx, gy))
            and not (guard is not None and guard.contains(shapely.Point(gx, gy)))]
    nodes = list(dict.fromkeys([tuple(p) for p in ccw] + breaks + grid))
    index = {p: i for i, p in enumerate(nodes)}
    verts = [(x, y, terrain_z(x, y)) for x, y in nodes]
    top = []
    for tri in shapely.get_parts(shapely.delaunay_triangles(shapely.MultiPoint(nodes))):
        ijk = [index.get((round(px, 10), round(py, 10)), index.get((px, py)))
               for px, py in tri.exterior.coords[:3]]
        if any(i is None for i in ijk):
            continue
        a, b_, c = (np.array(nodes[i]) for i in ijk)
        if (b_[0] - a[0]) * (c[1] - a[1]) - (b_[1] - a[1]) * (c[0] - a[0]) < 0:
            ijk = [ijk[0], ijk[2], ijk[1]]                       # keep tops facing up
        top.append(tuple(ijk))

    # Lawn/earth must not cross the house: cut the actual basement outer footprint.
    # Keep earth below its structural foundation rather than a hole to infinity.
    from terrain_cutouts import excavate
    basement_plan = json.loads((DATA / "basement.json").read_text())
    foundation = next(s for s in basement_plan["slabs"] if s["id"] == terrain["building_excavation"]["footprint_from"])
    house_footprint = shapely.union_all([shapely.box(*r) for r in foundation["rects"]])
    pit_z = -2.85 + foundation.get("z_top",0) - foundation["thickness"]
    verts, faces = excavate(verts, top, house_footprint, pit_z, ccw, terrain["base_z"])
    terrain_style = styled("Terrain", terrain["rgb"])
    add("IfcGeographicElement", terrain["id"], [face_set(verts, faces)],
        terrain_style, predefined="TERRAIN")
    n_tris = len(faces)

    # --- parcel boundaries: flat marker lines, nothing is built there -------------
    # A survey line, not a fence: one zero-thickness strip lying in a single
    # horizontal plane, set just above the highest terrain the line crosses so it
    # stays visible without following the ground and looking like a kerb.
    mk = plan["boundary_marker"]
    half = mk["width"] / 2.0

    def ribbon(pts, closed):
        chain = list(pts) + ([pts[0]] if closed else [])
        z = max(terrain_z(x, y) for x, y in chain) + mk["lift"]
        verts, faces = [], []
        for i in range(len(chain) - 1):
            (x0, y0), (x1, y1) = chain[i], chain[i + 1]
            seg = math.hypot(x1 - x0, y1 - y0)
            nx, ny = -(y1 - y0) / seg * half, (x1 - x0) / seg * half
            b = len(verts)
            verts += [(x0 + nx, y0 + ny, z), (x0 - nx, y0 - ny, z),
                      (x1 + nx, y1 + ny, z), (x1 - nx, y1 - ny, z)]
            faces += [(b, b + 1, b + 3), (b, b + 3, b + 2)]
        return [face_set(verts, faces)]

    pb, ps = plan["property_boundary"], plan["parcel_split"]
    add("IfcAnnotation", pb["id"], ribbon(pb["ring"], True), styled("ParcelLine", mk["rgb_outer"]))
    add("IfcAnnotation", ps["id"], ribbon(ps["line"], False), styled("ParcelSplit", mk["rgb_split"]))

    # --- Haus Süd: foundation slab + perimeter wall ring (Grundriss only) -------
    for f in plan["foundations"]:
        fp = f["footprint"]
        fstyle = styled(f["id"], f["rgb"])
        add("IfcFooting", f["id"] + "_PLATTE",
            [prism(fp, lambda _x, _y, z=f["slab_top"]: z, f["slab_top"] - f["slab_thickness"])],
            fstyle, predefined="PAD_FOOTING")
        # footprint is listed clockwise, so the inward normal is to the left of travel
        t = f["wall_thickness"]
        walls = []
        for i, (x0, y0) in enumerate(fp):
            x1, y1 = fp[(i + 1) % len(fp)]
            seg = math.hypot(x1 - x0, y1 - y0)
            nx, ny = -(y1 - y0) / seg * t, (x1 - x0) / seg * t
            walls.append(extrude([(x0, y0), (x1, y1), (x1 + nx, y1 + ny), (x0 + nx, y0 + ny)],
                                 f["slab_top"], f["wall_top"] - f["slab_top"]))
        add("IfcWall", f["id"] + "_WAND", walls, fstyle)
        # the foundation is flush with the 460.46 pad, so it would vanish under the
        # terrain -- trace the Grundriss on the surface so the footprint always reads
        add("IfcAnnotation", f["id"] + "_GRUNDRISS", ribbon(fp, True),
            styled(f["id"] + "_outline", f["outline_rgb"]))

    # --- garden: paved stand areas, and the products standing on them -------------
    # A pad is a plain box bedded into the terrain: its top is the plan's spot height,
    # its underside sits below the plateau so no gap opens along the edges.
    pads = {}
    for gd in plan.get("garden", []):
        # either "rect": [x0, y0, x1, y1] or "polygon": [[x, y], ...] (ccw, closed by the builder)
        if "polygon" in gd:
            ring = [tuple(pt) for pt in gd["polygon"]]
        else:
            gx0, gy0, gx1, gy1 = gd["rect"]
            ring = [(gx0, gy0), (gx1, gy0), (gx1, gy1), (gx0, gy1)]
        top = gd["surface_z"]
        pad = add("IfcSlab", gd["id"],
                  [extrude(ring, top - gd["thickness"], gd["thickness"])],
                  styled(gd["id"], gd["rgb"]), predefined="BASESLAB")
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        pads[gd["id"]] = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0, top)
        gpset = ifcopenshell.api.run("pset.add_pset", model, product=pad, name="Pset_Product")
        ifcopenshell.api.run("pset.edit_pset", model, pset=gpset,
                             properties={**gd.get("props", {}), "Notes": gd["_source"]})

    for go in plan.get("garden_objects", []):
        # meshes are exported x/y centred with z_min = 0, so `at` is the pad centre
        at = go.get("at") or pads[go.get("on", "POOLPLATZ")]
        obj = add(go.get("ifc_class", "IfcFurnishingElement"), go["id"],
                  [mesh_item(go["mesh"], at, go.get("rotation_z", 0.0), go.get("scale", 1.0))],
                  styled(go["id"], go["rgb"], go.get("transparency", 0.0)) if go.get("rgb") else None)
        opset = ifcopenshell.api.run("pset.add_pset", model, product=obj, name="Pset_Product")
        ifcopenshell.api.run("pset.edit_pset", model, pset=opset,
                             properties={**go.get("props", {}), "Notes": go["_source"]})

    # --- provenance psets --------------------------------------------------------
    named = {pb["id"]: pb["_source"], ps["id"]: ps["_source"]}
    named[terrain["id"]] = terrain["_source"]
    for f in plan["foundations"]:
        for suffix in ("_PLATTE", "_WAND", "_GRUNDRISS"):
            named[f["id"] + suffix] = f["_source"]
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        if rel.RelatingStructure != site:
            continue
        for e in rel.RelatedElements:
            src = named.get(e.Name)
            if not src:
                continue
            pset = ifcopenshell.api.run("pset.add_pset", model, product=e, name="Pset_Source")
            ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={
                "Plan": "plans/2208_01_Architektur aktuell/AF_GR_200 Grundrisse Situation/"
                        "AF_GR_200_001 Katasterplan.pdf; plans/2208_07_Umgebungsplanung aktuell/"
                        "25-04-29 Umgebungsplan Felsenhofstrasse 12, Adliswil BE Abaenderung.pdf",
                "Lieferant": "Berger Gartenbau AG, Kilchberg (Umgebungsplanung)",
                "Notes": src,
            })

    pset = ifcopenshell.api.run("pset.add_pset", model, product=site, name="Pset_SiteCommon")
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={
        "Reference": "Felsenhofstrasse 12, 8134 Adliswil - Parzellen 8513 + 8514",
    })
    return n_tris, len(pads), len(plan.get("garden_objects", []))


totals = []
for name, elev, plan_file in STOREYS:
    nw, nwin = build_storey(name, elev, plan_file)
    totals.append(f"{name}: {nw} walls, {nwin} windows")

n_tris, n_pads, n_objs = build_site(DATA / "site.json")
totals.append(f"Site: terrain {n_tris} tris, parcel lines, Haus Süd foundation, "
              f"{n_pads} garden pads, {n_objs} garden objects")

GENERATED.mkdir(exist_ok=True)
# Readers always see a complete IFC, including during a background rebuild.
fd, temporary = tempfile.mkstemp(prefix='.model-', suffix='.ifc', dir=GENERATED)
os.close(fd)
try:
    model.write(temporary)
    os.replace(temporary, OUTPUT)
finally:
    Path(temporary).unlink(missing_ok=True)
record_build(ROOT, BUILD_INPUTS)
print(f"Wrote {OUTPUT} — " + "; ".join(totals))
