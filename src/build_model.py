"""Build the IFC from data/house.json and its per-storey plan JSON files.

All storeys share one local frame (x east, y north, z up, metres; see house.json
`origin_note`). Each storey JSON holds walls in plan coordinates, openings referenced
to walls or given as absolute boxes, slabs, stairs, roofs, finishes and furniture.
Furniture coordinates are storey-local (z above the finished storey floor); roof
planes, profile walls and `windows_abs` use absolute z. Output goes to the IFC path
named in house.json. Section reference: docs/data-format.md.
"""
import json
import math
import os
import tempfile
from pathlib import Path

import ifcopenshell
import ifcopenshell.api
from ifcopenshell.entity_instance import _method_dict as _SETTERS
import numpy as np
import shapely

from viewer_common import source_hash, record_build
import documents
from house import HOUSE
from floor_finishes import slab_solids
from stair_finishes import Finishes, build_up, internal_railing
from roof_finishes import split_lining
from model_lock import Lease

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MODEL_LEASE = Lease(ROOT)

BUILD_INPUTS = source_hash(ROOT)
source_errors = documents.verify(ROOT)
if source_errors:
    raise RuntimeError('\n'.join(source_errors))

# Lamp photometry edited in the viewer's lamp editor (data/lamp_overrides.json, written by
# src/serve.py POST /api/lamp) wins over the light block in the storey JSON.
_LAMP_OVERRIDES = DATA / "lamp_overrides.json"
LAMP_OVERRIDES = json.loads(_LAMP_OVERRIDES.read_text()).get("lamps", {}) if _LAMP_OVERRIDES.is_file() else {}
SOURCES = json.loads((DATA / "sources.json").read_text()) if (DATA / "sources.json").is_file() else {"rules": []}
OUTPUT = HOUSE.ifc_path

model = ifcopenshell.api.run("project.create_file", version="IFC4")
project = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name=HOUSE.name)
ifcopenshell.api.run("unit.assign_unit", model, length={"is_metric": True, "raw": "METERS"})

model_context = ifcopenshell.api.run("context.add_context", model, context_type="Model")
body_context = ifcopenshell.api.run(
    "context.add_context", model, context_type="Model", context_identifier="Body",
    target_view="MODEL_VIEW", parent=model_context)


def run(*args, **kwargs):
    return ifcopenshell.api.run(*args, **kwargs)


def shading_style(name, rgb, transparency=0.0):
    style = run("style.add_style", model, name=name)
    r, g, b = rgb
    run("style.add_surface_style", model, style=style, ifc_class="IfcSurfaceStyleShading",
        attributes={"SurfaceColour": {"Name": None, "Red": float(r), "Green": float(g), "Blue": float(b)},
                    "Transparency": float(transparency)})
    return style


glass_style = shading_style("Glass", (0.6, 0.75, 0.85), 0.7)
wood_style = shading_style("DoorWood", (0.52, 0.35, 0.20))
# Opaque milky shading keeps privacy glazing readable in solid IFC viewers.
frosted_glass_style = shading_style("FrostedGlass", (.83, .86, .84))


def glazing_style(definition):
    return frosted_glass_style if definition.get('glazing') == 'frosted' else glass_style


def style_representation(rep, style):
    run("style.assign_representation_styles", model, shape_representation=rep, styles=[style])


def add_pset(entity, name, props):
    props = {k: v for k, v in props.items() if v is not None and v != ''}
    if not props:
        return
    pset = run('pset.add_pset', model, product=entity, name=name)
    run('pset.edit_pset', model, pset=pset, properties=props)


def product_props(entity, definition, extra=None):
    """Pset_Product from `props`, plus the readable label on the IFC entity itself."""
    props = {**definition.get('props', {}), **(extra or {})}
    label = props.get('ProductLabel') or props.get('Product')
    if label:
        entity.Description = label
        entity.ObjectType = label
        entity.Tag = definition['id']
    if props:
        add_pset(entity, 'Pset_Product', props)
    gallery = definition.get('gallery')
    if gallery:
        add_pset(entity, 'Pset_Gallery', gallery)


def door_style(definition):
    if not definition.get('rgb'):
        return wood_style
    return shading_style(definition['id'] + '_finish', definition['rgb'])


stair_finishes = Finishes(model)

site = run("root.create_entity", model, ifc_class="IfcSite", name=HOUSE.data.get('site_name', 'Site'))
building = run("root.create_entity", model, ifc_class="IfcBuilding", name=HOUSE.name)
run("aggregate.assign_object", model, products=[site], relating_object=project)
run("aggregate.assign_object", model, products=[building], relating_object=site)


# ---------------------------------------------------------------- geometry helpers
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
    walks every single float looking for entity references.
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
    placed = [((x * cr - y * sr) * scale + tx, (x * sr + y * cr) * scale + ty, z * scale + tz)
              for x, y, z in verts]
    return triangulated_face_set(placed, faces)


def closed_polyline(pts):
    return model.createIfcPolyline(
        [model.createIfcCartesianPoint((float(x), float(y))) for x, y in list(pts) + [pts[0]]])


def box_solid(x0, y0, z0, x1, y1, z1):
    prof = model.createIfcArbitraryClosedProfileDef(
        "AREA", None, closed_polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]))
    return model.createIfcExtrudedAreaSolid(
        prof, model.createIfcAxis2Placement3D(model.createIfcCartesianPoint((0.0, 0.0, float(z0))), None, None),
        model.createIfcDirection((0.0, 0.0, 1.0)), float(z1 - z0))


def xy_profile_solid(pts, z0, z1):
    """(x,y) polygon extruded in z: flat floor annotations, terrace pads, site slabs."""
    prof = model.createIfcArbitraryClosedProfileDef("AREA", None, closed_polyline(pts))
    pos = model.createIfcAxis2Placement3D(
        model.createIfcCartesianPoint((0.0, 0.0, float(z0))),
        model.createIfcDirection((0.0, 0.0, 1.0)), model.createIfcDirection((1.0, 0.0, 0.0)))
    return model.createIfcExtrudedAreaSolid(prof, pos, model.createIfcDirection((0.0, 0.0, 1.0)), float(z1 - z0))


def yz_profile_solid(x0, x1, pts):
    """(y,z) polygon extruded along x: gables, sloped partitions, roof planes, stair flights."""
    prof = model.createIfcArbitraryClosedProfileDef(
        "AREA", None,
        model.createIfcPolyline([model.createIfcCartesianPoint((float(y), float(z))) for y, z in list(pts) + [pts[0]]]))
    pos = model.createIfcAxis2Placement3D(
        model.createIfcCartesianPoint((float(x0), 0.0, 0.0)),
        model.createIfcDirection((1.0, 0.0, 0.0)), model.createIfcDirection((0.0, 1.0, 0.0)))
    return model.createIfcExtrudedAreaSolid(prof, pos, model.createIfcDirection((0.0, 0.0, 1.0)), float(x1 - x0))


def cylinder_solid(cx, cy, z0, r, z1):
    prof = model.createIfcCircleProfileDef(
        "AREA", None, model.createIfcAxis2Placement2D(model.createIfcCartesianPoint((float(cx), float(cy))), None), float(r))
    return model.createIfcExtrudedAreaSolid(
        prof, model.createIfcAxis2Placement3D(model.createIfcCartesianPoint((0.0, 0.0, float(z0))), None, None),
        model.createIfcDirection((0.0, 0.0, 1.0)), float(z1 - z0))


def sphere_solid(cx, cy, cz, r):
    sph = model.createIfcSphere(
        model.createIfcAxis2Placement3D(model.createIfcCartesianPoint((float(cx), float(cy), float(cz))), None, None), float(r))
    return model.createIfcCsgSolid(sph)


def z_matrix(z):
    return [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, float(z)], [0.0, 0.0, 0.0, 1.0]]


def add_product(container, ifc_class, name, items, z_offset=0.0):
    """One IfcProduct with a Body representation of `items`, placed z_offset above the origin."""
    prod = run("root.create_entity", model, ifc_class=ifc_class, name=name)
    run("spatial.assign_container", model, products=[prod], relating_structure=container)
    run("geometry.edit_object_placement", model, product=prod, matrix=z_matrix(z_offset))
    rep_type = "Tessellation" if any(it.is_a("IfcTessellatedItem") for it in items) else "SweptSolid"
    rep = model.createIfcShapeRepresentation(body_context, "Body", rep_type, items)
    run("geometry.assign_representation", model, product=prod, representation=rep)
    return prod


def furnishing(container, fu, elevation):
    """One furniture/sanitary/object entry -> one IfcProduct with styled parts.

    "parts": one product, several differently coloured pieces. A wardrobe is ONE thing
    that happens to have a black carcass, white doors and black handles; grouping by
    colour instead would model three half-objects. Part keys: boxes, cylinders, spheres,
    prisms, mesh (+at/rotation_z/scale), texture (image projected on the boxes), rgb,
    transparency, name.
    """
    items = [box_solid(*b) for b in fu.get("boxes", [])]
    items += [cylinder_solid(*c) for c in fu.get("cylinders", [])]
    items += [sphere_solid(*sp) for sp in fu.get("spheres", [])]
    items += [yz_profile_solid(p["x0"], p["x1"], p["profile"]) for p in fu.get("prisms", [])]
    part_styled = []
    for part in fu.get("parts", []):
        if part.get('texture'):
            from image_textures import textured_box
            pit = [textured_box(model, b, part['texture'], ROOT) for b in part.get('boxes', [])]
        else:
            pit = [box_solid(*b) for b in part.get("boxes", [])]
        pit += [cylinder_solid(*c) for c in part.get("cylinders", [])]
        pit += [sphere_solid(*sp) for sp in part.get("spheres", [])]
        pit += [yz_profile_solid(p["x0"], p["x1"], p["profile"]) for p in part.get("prisms", [])]
        if part.get("mesh"):
            pit.append(mesh_item(part["mesh"], part.get("at", (0.0, 0.0, 0.0)),
                                 part.get("rotation_z", 0.0), part.get("scale", 1.0)))
        items += pit
        if part.get("rgb") and not part.get('texture'):
            part_styled.append((pit, part["rgb"], part.get("name", "part"), float(part.get("transparency", 0.0))))
    if fu.get("mesh"):
        items.append(mesh_item(fu["mesh"], fu.get("at", (0.0, 0.0, 0.0)), fu.get("rotation_z", 0.0), fu.get("scale", 1.0)))
    if not items:
        raise ValueError(f"{fu['id']}: no geometry")
    piece = add_product(container, fu.get("ifc_class", "IfcFurnishingElement"), fu["id"], items, elevation)
    if fu.get("predefined_type"):
        piece.PredefinedType = fu["predefined_type"]
    if fu.get("object_type"):                # required with PredefinedType USERDEFINED
        piece.ObjectType = fu["object_type"]
    product_props(piece, fu, {'TechnicalID': fu['id']} if fu.get('props') else None)
    if fu.get("light"):
        light_pset(piece, fu, elevation)
    for pit, rgb, pname, transparency in part_styled:
        pstyle = shading_style(f"{fu['id']}_{pname}_style", rgb, transparency)
        for it in pit:                       # style the individual solids, not the product
            model.createIfcStyledItem(it, [pstyle], None)
    if fu.get("rgb"):
        fstyle = shading_style(f"{fu['id']}_style", fu["rgb"], float(fu.get("transparency", 0.0)))
        # Product colour is the default (e.g. a mesh frame). Explicitly coloured parts keep their own.
        for it in items:
            if not it.StyledByItem:
                model.createIfcStyledItem(it, [fstyle], None)
    return piece


def light_pset(piece, fu, elevation):
    """The photometric side of a luminaire ("light" block) -> Pset_Light, read by the Night look
    of both viewers (web/viewer.js, site/app.js). at = light source in the entry's own frame
    (storey-local, or above the terrain for site objects); kind point (all round) or spot
    (direction + angle_deg = full beam); emissive = authored rgb colours of the parts that glow
    (diffuser, bulb), matched by colour. Viewer edits (data/lamp_overrides.json) win."""
    over = LAMP_OVERRIDES.get(fu["id"], {})
    L = {**fu["light"], **{k: v for k, v in over.items() if k in ("lumen", "kelvin", "kind", "angle_deg")}}
    sx, sy, sz = L["at"]
    add_pset(piece, "Pset_Light", {
        "LuminousFlux_lm": float(L["lumen"]), "ColourTemperature_K": float(L["kelvin"]),
        "Distribution": L.get("kind", "point"), "BeamAngle_deg": float(L.get("angle_deg", 360)),
        "Direction": ",".join(f"{float(c):g}" for c in L.get("direction", (0, 0, -1))),
        "SourceX": round(float(sx), 4), "SourceY": round(float(sy), 4), "SourceZ": round(float(sz) + elevation, 4),
        "EmissiveRGB": ";".join(",".join(f"{float(c):g}" for c in rgb) for rgb in L.get("emissive", [])),
        "SwitchGroup": L.get("switch", ""), "LightSource": L.get("source", ""),
        "Edited": f"viewer {over.get('edited', '')}" if over else ""})


# ---------------------------------------------------------------- storeys
def build_storey(entry):
    code, name, elevation = entry['code'], entry['name'], float(entry['elevation'])
    plan_file = DATA / entry['file']
    plan = json.loads(plan_file.read_text())
    from products import augment
    plan = augment(plan, code, ROOT)
    storey_height = plan["building_dimensions"]["storey_height"]

    storey = run("root.create_entity", model, ifc_class="IfcBuildingStorey", name=name)
    storey.Elevation = elevation
    storey.LongName = code
    model_source = documents.model_source(entry.get('source_document'), plan_file.relative_to(ROOT),
                                          plan.get('_source', ''), ROOT)
    add_pset(storey, 'Pset_ModelSource', {**model_source, 'StoreyCode': code})
    run("aggregate.assign_object", model, products=[storey], relating_object=building)

    def place(ifc_class, name, items, z_offset):
        return add_product(storey, ifc_class, name, items, z_offset)

    walls = {}
    for wd in plan["exterior_walls"] + plan["interior_walls"]:
        x1, y1 = wd["start"]
        x2, y2 = wd["end"]
        thickness = wd["thickness"]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        cos_t, sin_t = dx / length, dy / length
        wall = run("root.create_entity", model, ifc_class="IfcWallStandardCase", name=wd["id"])
        run("spatial.assign_container", model, products=[wall], relating_structure=storey)
        matrix = [[cos_t, -sin_t, 0.0, (x1 + x2) / 2.0], [sin_t, cos_t, 0.0, (y1 + y2) / 2.0],
                  [0.0, 0.0, 1.0, elevation], [0.0, 0.0, 0.0, 1.0]]
        run("geometry.edit_object_placement", model, product=wall, matrix=matrix)
        profile = model.createIfcRectangleProfileDef(ProfileType="AREA", XDim=float(length), YDim=float(thickness))
        rep = run("geometry.add_profile_representation", model, context=body_context, profile=profile,
                  depth=float(wd.get("height", storey_height)))
        run("geometry.assign_representation", model, product=wall, representation=rep)
        if wd.get("rgb"):
            style_representation(rep, shading_style(f"{wd['id']}_style", wd["rgb"]))
        if wd.get('props'):
            add_pset(wall, 'Pset_Product', wd['props'])
        walls[wd["id"]] = {"entity": wall, "start": (x1, y1), "cos": cos_t, "sin": sin_t, "thickness": thickness}

    for pw in plan.get("profile_walls", []):
        wall = run("root.create_entity", model, ifc_class="IfcWall", name=pw["id"])
        run("spatial.assign_container", model, products=[wall], relating_structure=storey)
        run("geometry.edit_object_placement", model, product=wall)
        solid = yz_profile_solid(pw["x0"], pw["x1"], pw["profile"])
        rep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", [solid])
        run("geometry.assign_representation", model, product=wall, representation=rep)
        if pw.get('props'):
            add_pset(wall, 'Pset_Product', pw['props'])
        if pw.get("rgb"):
            style_representation(rep, shading_style(f"{pw['id']}_style", pw["rgb"]))
        walls[pw["id"]] = {"entity": wall, "profile": True}

    # Roof planes: (y,z) profiles in absolute z extruded along x. "ifc_class": "IfcWindow"
    # makes a roof window; "cuts": <host id> bores the host so the window is a real hole.
    roof_entities = {}
    for rp in plan.get("roof_planes", []):
        if rp.get("ifc_class") == "IfcWindow":
            roof = run("root.create_entity", model, ifc_class="IfcWindow", name=rp["id"])
            roof.PredefinedType = "SKYLIGHT"
            ys = [p[0] for p in rp["profile"]]
            roof.OverallWidth = float(rp["x1"] - rp["x0"])
            roof.OverallHeight = float(max(ys) - min(ys))
        else:
            roof = run("root.create_entity", model, ifc_class=rp.get("ifc_class", "IfcSlab"), name=rp["id"])
            roof.PredefinedType = rp.get("predefined_type", "ROOF")
        run("spatial.assign_container", model, products=[roof], relating_structure=storey)
        run("geometry.edit_object_placement", model, product=roof)
        roof_entities[rp["id"]] = roof
        lining = plan.get("roof_finishes", {})[rp["interior_finish"]] if rp.get("interior_finish") else None
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
        run("geometry.assign_representation", model, product=roof, representation=rep)
        product_props(roof, rp)
        if rp.get("style") == "glass":
            style_representation(rep, glazing_style(rp))
        elif rp.get("rgb"):
            style_representation(rep, shading_style(f"{rp['id']}_style", rp["rgb"]))
        for part, part_items in roof_parts:
            part_style = glazing_style(rp) if part.get('style') == 'glass' else \
                shading_style(f"{rp['id']}_{part['name']}", part['rgb'])
            for item in part_items:
                run('style.assign_item_style', model, item=item, style=part_style)
        if lining:
            # Style the real lining solid after any whole-roof default style. One host
            # keeps roof-window voids cutting both layers.
            run("style.assign_item_style", model, item=items[1], style=shading_style(lining["name"], lining["rgb"]))
            add_pset(roof, "Pset_InteriorFinish",
                     {"Product": lining["name"], "NormalThickness_m": lining["thickness_m"], **lining.get("props", {})})

    # Roof windows need a real hole. For every roof plane with "cuts", bore the host by
    # the same (y,z) profile extended through the host up and down.
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
            bore_depth = max(max(zs) - min(zs) for zs in host_zs.values()) + 0.15
            zs_by_y = {}
            for y, z in rp["profile"]:
                zs_by_y.setdefault(round(y, 6), []).append(z)
            bore = [(y, z - bore_depth if z <= min(zs_by_y[round(y, 6)]) else z + bore_depth)
                    for y, z in rp["profile"]]
            bore_items = [yz_profile_solid(rp['x0'], rp['x1'], bore)]
        opening = run("root.create_entity", model, ifc_class="IfcOpeningElement", name=f'Opening_{rp["id"]}')
        run("spatial.assign_container", model, products=[opening], relating_structure=storey)
        run("geometry.edit_object_placement", model, product=opening)
        orep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", bore_items)
        run("geometry.assign_representation", model, product=opening, representation=orep)
        run("feature.add_feature", model, feature=opening, element=roof_entities[host_id])

    # Absolute openings: `box` is the inside aperture, `opening_boxes` the solids removed
    # from the wall (stepped reveals), `parts` the frame/sash/glass pieces, all absolute.
    detailed_windows = [w for w in plan.get('windows', []) if 'parts' in w]
    for wa in plan.get("windows_abs", []) + detailed_windows:
        def absolute_box(box):
            a, b, c, d, e, f = box
            if not all(math.isfinite(v) for v in box) or not (a < d and b < e and c < f):
                raise ValueError(f"{wa['id']}: invalid absolute opening/part box {box}")
            return box_solid(a, b, c - elevation, d, e, f - elevation)

        host = walls[wa.get("host", wa.get("wall_id"))]["entity"]
        x0, y0, z0, x1, y1, z1 = wa["box"]
        opening = run("root.create_entity", model, ifc_class="IfcOpeningElement", name=f"Opening_{wa['id']}")
        run("spatial.assign_container", model, products=[opening], relating_structure=storey)
        mat = z_matrix(elevation)
        run("geometry.edit_object_placement", model, product=opening, matrix=mat)
        if 'opening_boxes' in wa:
            bores = [absolute_box(b) for b in wa['opening_boxes']]
        else:
            bore = [x0 - .05, y0, z0, x1 + .05, y1, z1] if wa.get('cut_axis') == 'x' else [x0, y0 - .05, z0, x1, y1 + .05, z1]
            bores = [absolute_box(bore)]
        if not bores:
            raise ValueError(f"{wa['id']}: opening_boxes cannot be empty")
        orep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", bores)
        run("geometry.assign_representation", model, product=opening, representation=orep)
        run("feature.add_feature", model, feature=opening, element=host)

        if wa.get("fill") is False:
            continue
        cls = "IfcDoor" if wa.get("kind") == "door" else "IfcWindow"
        fill = run("root.create_entity", model, ifc_class=cls, name=wa["id"])
        if wa.get('nominal_width') is not None:
            fill.OverallWidth = float(wa['nominal_width'])
            fill.OverallHeight = float(wa['nominal_height'])
        run("spatial.assign_container", model, products=[fill], relating_structure=storey)
        run("geometry.edit_object_placement", model, product=fill, matrix=mat)
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
        elif wa.get("frame_width") and wa.get("kind") == "door":
            # The box spans the casing; the clear opening excludes both jambs.
            frame = wa["frame_width"]
            assert wa.get("cut_axis") == "x" and y1 - y0 > 2 * frame
            pieces = [box_solid(x0, y0, z0 - elevation, x1, y0 + frame, z1 - elevation),
                      box_solid(x0, y1 - frame, z0 - elevation, x1, y1, z1 - elevation),
                      box_solid(x0, y0 + frame, z1 - elevation - frame, x1, y1 - frame, z1 - elevation),
                      box_solid(cx - .02, y0 + frame, z0 - elevation, cx + .02, y1 - frame, z1 - elevation - frame)]
        elif wa.get("cut_axis") == "x":
            pieces = [box_solid(cx - .03, y0, z0 - elevation, cx + .03, y1, z1 - elevation)]
        else:
            pieces = [box_solid(x0, cy - .03, z0 - elevation, x1, cy + .03, z1 - elevation)]
        frep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", pieces)
        run("geometry.assign_representation", model, product=fill, representation=frep)
        if wa.get("rgb"):
            style = shading_style(f"{wa['id']}_style", wa["rgb"])
        else:
            style = wood_style if wa.get("kind") == "door" else glazing_style(wa)
        if styled_parts:
            for part, part_items in styled_parts:
                part_style = glazing_style(part) if part.get('glazing') else \
                    shading_style(f"{wa['id']}_{part['name']}", part['rgb'])
                for item in part_items:
                    model.createIfcStyledItem(item, [part_style], None)
        else:
            style_representation(frep, style)
        product_props(fill, wa)
        run("feature.add_filling", model, opening=opening, element=fill)

    def wall_opening(parent, item, width, height, sill):
        """Opening in a wall at offset_along_wall; returns (opening, placement matrix)."""
        x1, y1 = parent["start"]
        cos_t, sin_t = parent["cos"], parent["sin"]
        d_center = item["offset_along_wall"] + width / 2.0
        matrix = [[cos_t, -sin_t, 0.0, x1 + d_center * cos_t], [sin_t, cos_t, 0.0, y1 + d_center * sin_t],
                  [0.0, 0.0, 1.0, elevation + sill], [0.0, 0.0, 0.0, 1.0]]
        opening = run("root.create_entity", model, ifc_class="IfcOpeningElement", name=f"Opening_{item['id']}")
        run("spatial.assign_container", model, products=[opening], relating_structure=storey)
        run("geometry.edit_object_placement", model, product=opening, matrix=matrix)
        op_profile = model.createIfcRectangleProfileDef(ProfileType="AREA", XDim=float(width), YDim=float(parent["thickness"] + 0.10))
        op_rep = run("geometry.add_profile_representation", model, context=body_context, profile=op_profile, depth=float(height))
        run("geometry.assign_representation", model, product=opening, representation=op_rep)
        run("feature.add_feature", model, feature=opening, element=parent["entity"])
        return opening, matrix

    for win in plan["windows"]:
        if 'parts' in win:
            continue  # built above as an absolute assembly
        parent = walls[win["wall_id"]]
        width, height = win["width"], win["height"]
        opening, win_matrix = wall_opening(parent, win, width, height, win["sill_height"])
        window = run("root.create_entity", model, ifc_class="IfcWindow", name=win["id"])
        window.OverallWidth, window.OverallHeight = float(width), float(height)
        run("spatial.assign_container", model, products=[window], relating_structure=storey)
        run("geometry.edit_object_placement", model, product=window, matrix=win_matrix)
        w_profile = model.createIfcRectangleProfileDef(ProfileType="AREA", XDim=float(width), YDim=0.08)
        w_rep = run("geometry.add_profile_representation", model, context=body_context, profile=w_profile, depth=float(height))
        run("geometry.assign_representation", model, product=window, representation=w_rep)
        style_representation(w_rep, glazing_style(win))
        product_props(window, win)
        run("feature.add_filling", model, opening=opening, element=window)

    for door in plan.get("doors", []):
        parent = walls[door["wall_id"]]
        width, height = door["width"], door["height"]
        opening, door_matrix = wall_opening(parent, door, width, height, door.get("sill_height", 0.0))
        ifc_door = run("root.create_entity", model, ifc_class="IfcDoor", name=door["id"])
        ifc_door.OverallWidth, ifc_door.OverallHeight = float(width), float(height)
        run("spatial.assign_container", model, products=[ifc_door], relating_structure=storey)
        run("geometry.edit_object_placement", model, product=ifc_door, matrix=door_matrix)
        d_profile = model.createIfcRectangleProfileDef(ProfileType="AREA", XDim=float(width), YDim=0.05)
        d_rep = run("geometry.add_profile_representation", model, context=body_context, profile=d_profile, depth=float(height))
        run("geometry.assign_representation", model, product=ifc_door, representation=d_rep)
        style_representation(d_rep, door_style(door))
        product_props(ifc_door, door)
        run("feature.add_filling", model, opening=opening, element=ifc_door)

    for door in plan.get("free_doors", []):
        cx, cy = door["center"]
        axis_x = door.get("axis", "x") == "x"
        cos_t, sin_t = (1.0, 0.0) if axis_x else (0.0, 1.0)
        door_matrix = [[cos_t, -sin_t, 0.0, cx], [sin_t, cos_t, 0.0, cy],
                       [0.0, 0.0, 1.0, elevation + door.get("sill_height", 0.0)], [0.0, 0.0, 0.0, 1.0]]
        ifc_door = run("root.create_entity", model, ifc_class="IfcDoor", name=door["id"])
        ifc_door.OverallWidth, ifc_door.OverallHeight = float(door["width"]), float(door["height"])
        run("spatial.assign_container", model, products=[ifc_door], relating_structure=storey)
        run("geometry.edit_object_placement", model, product=ifc_door, matrix=door_matrix)
        d_profile = model.createIfcRectangleProfileDef(ProfileType="AREA", XDim=float(door["width"]), YDim=float(door.get("thickness", 0.06)))
        d_rep = run("geometry.add_profile_representation", model, context=body_context, profile=d_profile, depth=float(door["height"]))
        run("geometry.assign_representation", model, product=ifc_door, representation=d_rep)
        style_representation(d_rep, door_style(door))
        product_props(ifc_door, door)

    def recess_floor(product, rects, bottom, top):
        """Local installation pockets for full-height windows (absolute `floor_recess_boxes`)."""
        for window in plan.get('windows', []) + plan.get('windows_abs', []):
            cuts = []
            for a, b, c, d, e, f in window.get('floor_recess_boxes', []):
                if min(f, elevation + top) - max(c, elevation + bottom) <= 1e-8:
                    continue
                if not any(min(d, x1) - max(a, x0) > 1e-8 and min(e, y1) - max(b, y0) > 1e-8
                           for x0, y0, x1, y1 in rects):
                    continue
                cuts.append(box_solid(a, b, c, d, e, f))
            if not cuts:
                continue
            opening = run('root.create_entity', model, ifc_class='IfcOpeningElement',
                          name=f"FloorPocket_{window['id']}_{product.Name}")
            run('geometry.edit_object_placement', model, product=opening)
            rep = model.createIfcShapeRepresentation(body_context, 'Body', 'SweptSolid', cuts)
            run('geometry.assign_representation', model, product=opening, representation=rep)
            run('feature.add_feature', model, feature=opening, element=product)

    for slab_def in plan.get("slabs", []):
        th = slab_def["thickness"]
        slab = run("root.create_entity", model, ifc_class="IfcSlab", name=slab_def["id"])
        slab.PredefinedType = slab_def.get("predefined_type", "FLOOR")
        run("spatial.assign_container", model, products=[slab], relating_structure=storey)
        run("geometry.edit_object_placement", model, product=slab, matrix=z_matrix(0.0))
        items = []
        for (x0, y0, x1, y1), cell_bottom, cell_top in slab_solids(slab_def, plan.get("floorings", [])):
            items.append(box_solid(x0, y0, elevation + cell_bottom, x1, y1, elevation + cell_top))
        rep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", items)
        run("geometry.assign_representation", model, product=slab, representation=rep)
        if slab_def.get("rgb"):
            style_representation(rep, shading_style(f"{slab_def['id']}_style", slab_def["rgb"]))
        recess_floor(slab, slab_def.get('rects', [slab_def.get('rect')]), slab_def.get('z_top', 0) - th, slab_def.get('z_top', 0))
        if slab_def.get("props"):
            add_pset(slab, "Pset_Product", slab_def["props"])

    for layer in plan.get("floor_build_up", []):
        items = [box_solid(x0, y0, layer["z_bottom"], x1, y1, layer["z_top"]) for x0, y0, x1, y1 in layer["rects"]]
        product = place("IfcCovering", layer["id"], items, elevation)
        product.PredefinedType = layer.get("predefined_type", "FLOORING")
        recess_floor(product, layer['rects'], layer['z_bottom'], layer['z_top'])
        if layer.get("props"):
            add_pset(product, "Pset_Product", layer["props"])
        style_representation(product.Representation.Representations[0], shading_style(layer["id"] + "_style", layer["rgb"]))

    for st in plan.get("stairs", []):
        # Straight flight as a sawtooth (y,z) profile extruded over x_range: `risers`
        # steps of riser_h x tread, a waist of waist_vertical under the nosings, climbing
        # from y_start towards y_end. Step levels are FINISHED levels; an optional timber
        # build-up (stair_finishes.json) lowers the concrete core by its thickness.
        x0, x1 = st["x_range"]
        y0 = st["y_start"]
        n, rise, tread = st["risers"], st["riser_h"], st["tread"]
        waist_v = st["waist_vertical"]
        direction = 1.0 if st["y_end"] > y0 else -1.0
        finish_depth = build_up(st)
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
        flight = place("IfcStairFlight", st["id"], [yz_profile_solid(x0, x1, pts)], elevation)
        if st.get('props'):
            add_pset(flight, 'Pset_Product', st['props'])
        stair_finishes.flight(st, flight, place, box_solid, elevation)

        # Railing: vertical rods (max 11.5 cm apart) on the treads ("mount": "flight") or
        # standing on the storey floor at the void edge ("mount": "floor"), under a sloped
        # handrail 90 cm above the nosing line. "rod_y_end" stops the rods where a wall
        # takes over as the barrier.
        hr_h = st.get("handrail_height", 0.90)

        def nosing_z(yy):
            t = (yy - y0) / (y_top - y0)
            return rise + t * (z_top - rise)
        ox0, ox1 = st.get("opening_x_range", [x0 - 0.06, x1 + 0.06])
        for side_def in st.get("railings", []):
            side = side_def if isinstance(side_def, str) else side_def["side"]
            rod_y_end = None if isinstance(side_def, str) else side_def.get("rod_y_end")
            mount = "flight" if isinstance(side_def, str) else side_def.get("mount", "flight")
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
                    continue
                if mount == "flight":
                    step_i = min(int(abs(yy - y0) / tread), n - 1)
                    z_bot = (step_i + 1) * rise
                else:
                    z_bot = rod_z_base
                z_topr = nosing_z(yy) + hr_h - 0.06
                if z_topr > z_bot:
                    rods.append(box_solid(rx - 0.008, yy - 0.008, z_bot, rx + 0.008, yy + 0.008, z_topr))
            if rods:
                balustrade = place("IfcRailing", f"{st['id']}_BALUSTRADE_{side}", rods, elevation)
                stair_finishes.railing(balustrade)

            def sloped_rail(ya, yb, x_center):
                pts_ = [(ya, nosing_z(ya) + hr_h - 0.06), (yb, nosing_z(yb) + hr_h - 0.06),
                        (yb, nosing_z(yb) + hr_h), (ya, nosing_z(ya) + hr_h)]
                return yz_profile_solid(x_center - 0.025, x_center + 0.025, pts_)

            hr_end = y_top if rod_y_end is None else rod_y_end
            handrail = place("IfcRailing", f"{st['id']}_HANDRAIL_{side}", [sloped_rail(y0, hr_end, rx)], elevation)
            if internal_railing(handrail.Name):
                stair_finishes.railing(handrail, handrail=True)
            else:
                style_representation(handrail.Representation.Representations[0], wood_style)

    for fl in plan.get("floorings", []):
        top = fl.get("z_top", 0.01)
        bottom = top - fl.get("thickness", 0.01)
        items = [box_solid(x0, y0, bottom, x1, y1, top) for x0, y0, x1, y1 in fl["rects"]]
        cov = place("IfcCovering", fl["id"], items, elevation)
        cov.PredefinedType = "FLOORING"
        recess_floor(cov, fl['rects'], bottom, top)
        if fl.get("props"):
            add_pset(cov, "Pset_Product", fl["props"])
        style_representation(cov.Representation.Representations[0], shading_style(f"{fl['id']}_style", fl["rgb"]))

    for wc in plan.get("wall_coverings", []):
        # wall tiles etc.: absolute boxes, thin, glued to a wall face
        items = [box_solid(*b) for b in wc["boxes"]]
        cov = place("IfcCovering", wc["id"], items, elevation)
        cov.PredefinedType = "CLADDING"
        style_representation(cov.Representation.Representations[0], shading_style(f"{wc['id']}_style", wc["rgb"]))
        if wc.get("props"):
            add_pset(cov, "Pset_Product", wc["props"])

    # Named rooms: IfcSpace aggregated into the storey, polygon extruded to the clear
    # height, faintly transparent so viewers show the label, not a block.
    for sp in plan.get("spaces", []):
        prof = model.createIfcArbitraryClosedProfileDef("AREA", None, closed_polyline([tuple(p) for p in sp["polygon"]]))
        solid = model.createIfcExtrudedAreaSolid(
            prof, model.createIfcAxis2Placement3D(model.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None),
            model.createIfcDirection((0.0, 0.0, 1.0)), float(sp["height"]))
        space = run("root.create_entity", model, ifc_class="IfcSpace", name=sp["name"])
        space.LongName = sp.get("long_name")
        space.PredefinedType = "INTERNAL"
        run("aggregate.assign_object", model, products=[space], relating_object=storey)
        run("geometry.edit_object_placement", model, product=space, matrix=z_matrix(elevation))
        rep = model.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", [solid])
        run("geometry.assign_representation", model, product=space, representation=rep)
        style_representation(rep, shading_style(f"{sp['id']}_style", (0.55, 0.75, 0.95), 0.85))
        props = sp.get("props", {})
        add_pset(space, "Pset_SpaceCommon", {"Reference": props.get("RoomNumber", sp["name"])})
        if props:
            add_pset(space, "Pset_Room", props)

    for fu in plan.get("furniture", []) + plan.get("sanitary", []) + plan.get("door_hardware", []):
        furnishing(storey, fu, elevation)

    # Door swings, flat on the floor: the swept quarter-disc plus a bar for the leaf
    # where it ends up when fully open ("hinge", "closed", "open" points).
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
        ann = place("IfcAnnotation", sw["id"], items, elevation)
        style_representation(ann.Representation.Representations[0],
                             shading_style(f'{sw["id"]}_style', SWING_RGB.get(sw.get("kind"), SWING_RGB["door"]), 0.45))
        add_pset(ann, "Pset_DoorSwing", {"Door": sw["door"], "LeafWidth": round(r, 3), "Kind": sw.get("kind", "door"),
                                         "Source": sw.get("source", "")})

    # Power outlets: a square cover plate on the wall face it sits on ("at" is the point on
    # the face, "normal" the outward wall normal, "sill" the axis height above the floor).
    for o in plan.get("outlets", []):
        fx, fy = o["at"]
        nx, ny = o["normal"]
        gangs = int(o.get("gangs", 1))
        zc = o.get("sill", 0.30)
        w = o.get("cover", 0.088)
        z0, z1 = zc - w / 2, zc + w / 2
        seats = [(0.0, 0.0)] if gangs == 1 else [(0.0, 0.022), (-0.020, -0.016), (0.020, -0.016), (0.0, -0.03)][:gangs]
        if abs(nx) > 0.5:
            xa, xb = sorted((fx, fx + nx * 0.012))
            items = [box_solid(xa, fy - w / 2, z0, xb, fy + w / 2, z1)]
            for du, dv in seats:                      # socket openings, 30 mm, slightly proud
                sa, sb = sorted((fx + nx * 0.012, fx + nx * 0.016))
                items.append(box_solid(sa, fy + du - 0.015, zc + dv - 0.015, sb, fy + du + 0.015, zc + dv + 0.015))
        else:
            ya, yb = sorted((fy, fy + ny * 0.012))
            items = [box_solid(fx - w / 2, ya, z0, fx + w / 2, yb, z1)]
            for du, dv in seats:
                sa, sb = sorted((fy + ny * 0.012, fy + ny * 0.016))
                items.append(box_solid(fx + du - 0.015, sa, zc + dv - 0.015, fx + du + 0.015, sb, zc + dv + 0.015))
        outlet = place("IfcOutlet", o["id"], items, elevation)
        outlet.PredefinedType = "POWEROUTLET"
        style_representation(outlet.Representation.Representations[0], shading_style(f'{o["id"]}_style', o.get("rgb", (0.97, 0.97, 0.96))))
        add_pset(outlet, "Pset_Product", {"Product": "Power outlet", **o.get("props", {}),
                                          "Gangs": gangs, "MountingHeight": zc, "HostWall": o.get("wall", "")})

    # Switch banks: "columns" x rows of 6 cm modules in one plate at "height" above the floor.
    from light_switches import boxes as switch_boxes
    for switch in plan.get("switches", []):
        items = [box_solid(*b) for b in switch_boxes(switch)]
        device = place("IfcSwitchingDevice", switch["id"], items, elevation)
        device.PredefinedType = "USERDEFINED"
        device.ObjectType = "LIGHTSWITCHBANK"
        style_representation(device.Representation.Representations[0], shading_style(f'{switch["id"]}_style', switch.get("rgb", (.97, .97, .96))))
        add_pset(device, "Pset_Product", {"Product": "Light switch", **switch.get("props", {})})
        add_pset(device, "Pset_Switch", {"Controls": json.dumps(switch.get("controls", []), ensure_ascii=False),
                                         "Columns": switch["columns"], "HostFace": switch.get("face", "side")})

    for rl in plan.get("railings", []):
        # void-edge guard: vertical rods under a flat top rail, from "from" to "to"
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
        balustrade = place("IfcRailing", rl["id"], rods, elevation)
        handrail = place("IfcRailing", f"{rl['id']}_HANDRAIL", [hr], elevation)
        if rl.get('props'):
            add_pset(balustrade, 'Pset_Product', rl['props'])
        if internal_railing(rl["id"]):
            stair_finishes.railing(balustrade)
            stair_finishes.railing(handrail, handrail=True)
        else:
            if rl.get("rgb"):
                style_representation(balustrade.Representation.Representations[0], shading_style(f"{rl['id']}_style", rl["rgb"]))
            style_representation(handrail.Representation.Representations[0], wood_style)

    annotate_storey(storey, code, plan, model_source)
    return len(walls), len(plan["windows"]) + len(plan.get("windows_abs", []))


def source_props(pid, storey=None):
    """Supplier/Contract/SourceURL/Plan/Notes for an element id, from data/sources.json rules."""
    for rule in SOURCES.get("rules", []):
        if pid.startswith(rule["prefix"]):
            props = {}
            supplier = SOURCES.get("suppliers", {}).get(rule.get("supplier", ""))
            if supplier:
                props.update({"Supplier": supplier.get("supplier"), "Contract": supplier.get("contract"),
                              "SourceURL": supplier.get("url")})
            if rule.get("plan"):
                props["Plan"] = rule["plan"]
            if rule.get('plan_by_storey', {}).get(storey):
                props['Plan'] = rule['plan_by_storey'][storey]
            if rule.get("notes"):
                props["Notes"] = rule["notes"]
            return {k: v for k, v in props.items() if v}
    return None


def dims_index(plan):
    """id -> dimension props, straight from the plan JSON."""
    idx = {}
    for w in plan.get("windows", []) + plan.get("doors", []):
        if 'parts' in w:
            continue
        idx[w["id"]] = {"Width": w["width"], "Height": w["height"], "SillHeight": w.get("sill_height")}
    for fd in plan.get("free_doors", []):
        idx[fd["id"]] = {"Width": fd["width"], "Height": fd["height"], "Thickness": fd.get("thickness")}
    for wa in plan.get("windows_abs", []) + [w for w in plan.get('windows', []) if 'parts' in w]:
        x0, y0, z0, x1, y1, z1 = wa["box"]
        idx[wa["id"]] = {"Width": round(max(x1 - x0, y1 - y0), 3), "Height": round(z1 - z0, 3),
                         "Depth": round(min(x1 - x0, y1 - y0), 3)}
        if wa.get('opening_boxes'):
            idx[wa['id']]['DimensionMeaning'] = 'Inside aperture envelope; frame/glazing dimensions are separate in Pset_Product'
        if wa.get("frame_width"):
            idx[wa["id"]].update(FrameFaceWidth=wa["frame_width"], ClearOpeningWidth=round(y1 - y0 - 2 * wa["frame_width"], 3))
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
                         "TotalRise": st.get("rise_total", round(st["risers"] * st["riser_h"], 3))}
    for f in plan.get("furniture", []) + plan.get("sanitary", []):
        boxes = (f.get("boxes") or []) + [b for p in f.get("parts", []) for b in p.get("boxes", [])]
        if f.get("mesh_bounds"):
            boxes.append(f["mesh_bounds"])
        if boxes:
            xs, ys, zs = [], [], []
            for x0, y0, z0, x1, y1, z1 in boxes:
                xs += [x0, x1]; ys += [y0, y1]; zs += [z0, z1]
            idx[f["id"]] = {"ExtentX": round(max(xs) - min(xs), 3), "ExtentY": round(max(ys) - min(ys), 3),
                            "ExtentZ": round(max(zs) - min(zs), 3)}
    for wc in plan.get("wall_coverings", []):
        zs = [z for b in wc["boxes"] for z in (b[2], b[5])]
        idx[wc["id"]] = {"Panels": len(wc["boxes"]), "TopHeight": round(max(zs), 3)}
    return idx


STRUCTURAL = ('IfcWall', 'IfcWallStandardCase', 'IfcSlab', 'IfcRoof', 'IfcDoor', 'IfcWindow', 'IfcStairFlight',
              'IfcBeam', 'IfcColumn', 'IfcMember')


def annotate_storey(storey, code, plan, model_source):
    """Pset_ModelSource on structure, Pset_Source (data/sources.json) and Pset_Dimensions on everything."""
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
            if e.is_a() in STRUCTURAL:
                add_pset(e, 'Pset_ModelSource', source_overrides.get(pid, model_source))
            src = source_props(pid, code)
            if src:
                add_pset(e, "Pset_Source", src)
            d = dims.get(pid)
            if d:
                add_pset(e, "Pset_Dimensions", d)


# ---------------------------------------------------------------- site
def build_site(site_file):
    """Optional site from data/<site>.json, hanging off the IfcSite in the same local frame.

    terrain:     {"id", "polygon" | "rect", "z", "base_z", "holes": [[x0,y0,x1,y1] | polygon],
                  "rgb", "props"}  a flat ground surface with a skirt down to base_z
    boundaries:  [{"id", "points", "closed", "rgb", "width", "lift"}]  survey lines drawn as
                  flat ribbons just above the terrain
    slabs:       [{"id", "rect" | "polygon", "z_top", "thickness", "rgb", "props"}]  pads, paths
    objects:     the furniture schema (boxes/parts/mesh/texture), z above terrain
    """
    plan = json.loads(Path(site_file).read_text())
    counts = {}

    def add(ifc_class, name, items, rgb=None, predefined=None, transparency=0.0):
        prod = add_product(site, ifc_class, name, items)
        if predefined:
            prod.PredefinedType = predefined
        if rgb:
            style_representation(prod.Representation.Representations[0], shading_style(name + '_style', rgb, transparency))
        return prod

    def ring_of(spec):
        if "polygon" in spec:
            return [tuple(p) for p in spec["polygon"]]
        x0, y0, x1, y1 = spec["rect"]
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    terrain = plan.get("terrain")
    ground_z = float(terrain["z"]) if terrain else 0.0
    if terrain:
        outer = shapely.Polygon(ring_of(terrain))
        holes = [shapely.box(*h) if len(h) == 4 and not isinstance(h[0], (list, tuple)) else shapely.Polygon(h)
                 for h in terrain.get("holes", [])]
        surface = outer.difference(shapely.union_all(holes)) if holes else outer
        z_top, z_base = ground_z, float(terrain.get("base_z", ground_z - 0.5))
        verts, faces = [], []
        index = {}

        def vid(x, y, z):
            key = (round(x, 6), round(y, 6), round(z, 6))
            if key not in index:
                index[key] = len(verts)
                verts.append(key)
            return index[key]
        for poly in getattr(surface, 'geoms', [surface]):
            for tri in shapely.get_parts(shapely.constrained_delaunay_triangles(poly)):
                (ax, ay), (bx, by), (cx, cy) = tri.exterior.coords[:3]
                if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0:
                    (bx, by), (cx, cy) = (cx, cy), (bx, by)
                faces.append((vid(ax, ay, z_top), vid(bx, by, z_top), vid(cx, cy, z_top)))
                faces.append((vid(ax, ay, z_base), vid(cx, cy, z_base), vid(bx, by, z_base)))
            for ring in [poly.exterior, *poly.interiors]:
                pts = list(ring.coords)
                for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
                    a, b = vid(x0, y0, z_top), vid(x1, y1, z_top)
                    c, d = vid(x1, y1, z_base), vid(x0, y0, z_base)
                    faces += [(a, b, c), (a, c, d)]
        ground = add("IfcGeographicElement", terrain["id"], [triangulated_face_set(verts, faces)],
                     terrain.get("rgb", (0.45, 0.6, 0.35)), predefined="TERRAIN")
        if terrain.get("props"):
            add_pset(ground, "Pset_Product", terrain["props"])
        counts['terrain tris'] = len(faces)

    for mk in plan.get("boundaries", []):
        half = mk.get("width", 0.08) / 2.0
        chain = [tuple(p) for p in mk["points"]] + ([tuple(mk["points"][0])] if mk.get("closed") else [])
        z = ground_z + mk.get("lift", 0.02)
        verts, faces = [], []
        for (x0, y0), (x1, y1) in zip(chain[:-1], chain[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            nx, ny = -(y1 - y0) / seg * half, (x1 - x0) / seg * half
            b = len(verts)
            verts += [(x0 + nx, y0 + ny, z), (x0 - nx, y0 - ny, z), (x1 + nx, y1 + ny, z), (x1 - nx, y1 - ny, z)]
            faces += [(b, b + 1, b + 3), (b, b + 3, b + 2)]
        line = add("IfcAnnotation", mk["id"], [triangulated_face_set(verts, faces)], mk.get("rgb", (0.9, 0.2, 0.2)))
        if mk.get("props"):
            add_pset(line, "Pset_Product", mk["props"])
    counts['boundary lines'] = len(plan.get("boundaries", []))

    for gd in plan.get("slabs", []):
        top = gd.get("z_top", ground_z)
        pad = add("IfcSlab", gd["id"], [xy_profile_solid(ring_of(gd), top - gd["thickness"], top)],
                  gd.get("rgb", (0.7, 0.68, 0.65)), predefined=gd.get("predefined_type", "BASESLAB"))
        if gd.get("props"):
            add_pset(pad, "Pset_Product", gd["props"])
    counts['site slabs'] = len(plan.get("slabs", []))

    for go in plan.get("objects", []):
        furnishing(site, go, ground_z)
    counts['site objects'] = len(plan.get("objects", []))

    add_pset(site, "Pset_SiteCommon", {"Reference": plan.get("reference", HOUSE.name)})
    return counts


totals = []
for entry in HOUSE.storeys:
    nw, nwin = build_storey(entry)
    totals.append(f"{entry['code']} {entry['name']}: {nw} walls, {nwin} windows")

if HOUSE.site_file and (DATA / HOUSE.site_file).is_file():
    counts = build_site(DATA / HOUSE.site_file)
    totals.append("Site: " + ", ".join(f"{v} {k}" for k, v in counts.items()))

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
# Readers always see a complete IFC, including during a background rebuild.
fd, temporary = tempfile.mkstemp(prefix='.model-', suffix='.ifc', dir=OUTPUT.parent)
os.close(fd)
try:
    model.write(temporary)
    os.replace(temporary, OUTPUT)
finally:
    Path(temporary).unlink(missing_ok=True)
record_build(ROOT, BUILD_INPUTS)
print(f"Wrote {OUTPUT.relative_to(ROOT)} ({HOUSE.name}) — " + "; ".join(totals))
