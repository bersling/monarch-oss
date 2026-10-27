// Monarch hosted IFC viewer. Everything runs in the browser: web-ifc (wasm) parses the file,
// three.js draws it. No upload, no server, no build step (plain ES modules + import map).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import * as WebIFC from 'web-ifc';

const EXAMPLE_URL = './casa_monarch.ifc';          // placed next to the page by .github/workflows/pages.yml
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ---------------------------------------------------------------- renderer, scene, lights
const canvas = $('view');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.NoToneMapping;
const scene = new THREE.Scene();
scene.add(new THREE.HemisphereLight(0xffffff, 0x9a9282, 2.0));
const sun = new THREE.DirectionalLight(0xffffff, 1.4);
sun.position.set(0.55, 0.75, 0.7);
scene.add(sun);
const fill = new THREE.DirectionalLight(0xffffff, 0.5);
fill.position.set(-0.6, 0.4, -0.8);
scene.add(fill);

const FOV_PRESETS = [30, 45, 60, 75], DEFAULT_FOV = 45, DEFAULT_CUT = 1.2;
const persp = new THREE.PerspectiveCamera(DEFAULT_FOV, 1, 0.05, 3000);
persp.position.set(14, 10, 16);
// Plan view: orthographic, looking straight down. IFC +y is taken as north (no TrueNorth
// rotation); web-ifc maps IFC y to three -z, so -z is screen up.
const ortho = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 1000);
ortho.up.set(0, 0, -1);
let camera = persp;
const orbit = new OrbitControls(persp, canvas);
orbit.enableDamping = true;
orbit.dampingFactor = 0.12;
orbit.screenSpacePanning = true;
orbit.zoomToCursor = true;
orbit.target.set(0, 1, 0);
const planControls = new OrbitControls(ortho, canvas);
planControls.enableRotate = false;
planControls.screenSpacePanning = true;
planControls.zoomToCursor = true;
planControls.enabled = false;
let controls = orbit;

// web-ifc already returns y-up geometry (IFC x, y, z -> three x, z, -y), so the root stays unrotated.
const root = new THREE.Group();
scene.add(root);
const spacesGroup = new THREE.Group();
spacesGroup.visible = false;
root.add(spacesGroup);
const selectBox = new THREE.Box3Helper(new THREE.Box3(), 0x3ea6ff);
selectBox.visible = false;
scene.add(selectBox);

// Cut: everything above storey floor + height is clipped (three y <= constant); opaque back
// faces turn slate while cutting, so cut walls read as a section fill (poche).
const clipPlane = new THREE.Plane(new THREE.Vector3(0, -1, 0), 0);
const POCHE = { value: 0 }, POCHE_COLOUR = { value: new THREE.Color('#8793aa') };
function patchMaterial(m) {
  if (m.transparent) return m;
  m.onBeforeCompile = shader => {
    shader.uniforms.uPoche = POCHE;
    shader.uniforms.uPocheColour = POCHE_COLOUR;
    shader.fragmentShader = 'uniform float uPoche;\nuniform vec3 uPocheColour;\n' + shader.fragmentShader.replace('#include <dithering_fragment>',
      '#include <dithering_fragment>\n\tif (uPoche > 0.5 && !gl_FrontFacing) gl_FragColor = vec4(uPocheColour, 1.0);');
  };
  return m;
}

// ---------------------------------------------------------------- web-ifc
const api = new WebIFC.IfcAPI();
api.SetWasmPath(new URL('./vendor/web-ifc/', import.meta.url).href, true);
let apiReady = api.Init(undefined, true);     // single-thread wasm: GitHub Pages is not cross-origin isolated
apiReady.catch(e => toast(`web-ifc failed to initialise: ${e.message || e}`));

// ---------------------------------------------------------------- state
const state = {
  modelID: null, fileName: '', elements: new Map(), storeys: [], storeyVisible: new Map(), sel: null,
  nav: 'look', spaces: false, tris: 0, parseMs: 0, materials: new Map(),
  cut: null, plan: null, measure: false,
  removed: new Set(),              // ids hidden with X: play-around only, never saved, gone on reload
};
const removedStack = [];           // removal order for Z (undo)
function materialFor(materials, c) {
  const key = `${c.x.toFixed(3)},${c.y.toFixed(3)},${c.z.toFixed(3)},${c.w.toFixed(3)}`;
  let m = materials.get(key);
  if (!m) {
    m = new THREE.MeshLambertMaterial({ side: THREE.DoubleSide });
    m.color.setRGB(c.x, c.y, c.z, THREE.SRGBColorSpace);
    if (c.w < 1) { m.transparent = true; m.opacity = Math.max(0.12, c.w); m.depthWrite = false; }
    materials.set(key, patchMaterial(m));
  }
  return m;
}
const SPACE_COLOUR = { x: 0.3, y: 0.6, z: 1, w: 0.18 };
// Image textures (IfcImageTexture) are files referenced relative to the IFC, so they only resolve
// when the IFC came from a URL (the example house); a dropped file keeps the style colour.
const texLoader = new THREE.TextureLoader();
function textureMaterial(materials, url) {
  const key = `tex:${url}`;
  let m = materials.get(key);
  if (!m) {
    m = new THREE.MeshLambertMaterial({ side: THREE.DoubleSide });
    m.map = texLoader.load(url, t => { t.colorSpace = THREE.SRGBColorSpace; m.needsUpdate = true; }, undefined, () => console.warn(`texture missing: ${url}`));
    m.map.colorSpace = THREE.SRGBColorSpace;
    materials.set(key, patchMaterial(m));
  }
  return m;
}
// IfcIndexedTriangleTextureMap -> {uri, coeff}: a planar UV map fitted by least squares from the
// face set's coordinates to its texture coordinates (same as src/image_textures.py). web-ifc hands
// the item's vertices back translated, so they are re-anchored on the bounding-box centre.
function textureMaps(modelID) {
  const out = new Map(), ids = api.GetLineIDsWithType(modelID, WebIFC.IFCINDEXEDTRIANGLETEXTUREMAP);
  for (let i = 0; i < ids.size(); i++) {
    try {
      const tm = api.GetLine(modelID, ids.get(i));
      const tex = api.GetLine(modelID, val(tm.Maps?.[0]));
      const uri = val(tex?.URLReference);
      const fs = api.GetLine(modelID, val(tm.MappedTo));
      const xyz = api.GetLine(modelID, val(fs.Coordinates)).CoordList.map(p => p.map(val));
      const uv = api.GetLine(modelID, val(tm.TexCoords)).TexCoordsList.map(p => p.map(val));
      const ata = Array.from({ length: 4 }, () => [0, 0, 0, 0]), atb = Array.from({ length: 4 }, () => [0, 0]);
      fs.CoordIndex.forEach((tri, t) => tri.forEach((v, k) => {
        const a = [...xyz[val(v) - 1], 1], b = uv[val(tm.TexCoordIndex[t][k]) - 1];
        for (let r = 0; r < 4; r++) { for (let c = 0; c < 4; c++) ata[r][c] += a[r] * a[c]; atb[r][0] += a[r] * b[0]; atb[r][1] += a[r] * b[1]; }
      }));
      for (let r = 0; r < 4; r++) ata[r][r] += 1e-9;
      const coeff = solve4(ata, atb);
      const centre = [0, 1, 2].map(k => (Math.min(...xyz.map(p => p[k])) + Math.max(...xyz.map(p => p[k]))) / 2);
      if (uri && coeff) out.set(val(tm.MappedTo), { uri, coeff, centre });
    } catch (e) { console.warn('texture map skipped', e); }
  }
  return out;
}
function solve4(A, B) {             // Gauss-Jordan for a 4x4 system with two right-hand sides
  const m = A.map((row, r) => [...row, ...B[r]]);
  for (let c = 0; c < 4; c++) {
    let p = c;
    for (let r = c + 1; r < 4; r++) if (Math.abs(m[r][c]) > Math.abs(m[p][c])) p = r;
    if (Math.abs(m[p][c]) < 1e-12) return null;
    [m[c], m[p]] = [m[p], m[c]];
    for (let r = 0; r < 4; r++) if (r !== c) { const f = m[r][c] / m[c][c]; for (let k = c; k < 6; k++) m[r][k] -= f * m[c][k]; }
  }
  return m.map((row, r) => [row[4] / row[r], row[5] / row[r]]);
}
const val = v => (v && typeof v === 'object' && 'value' in v) ? v.value : v;

function disposeGroup(g) {
  g.traverse(o => { if (o.isMesh) o.geometry.dispose(); });
  g.parent?.remove(g);
}
function clearModel() {
  if (state.modelID !== null) { try { api.CloseModel(state.modelID); } catch { /* already gone */ } }
  for (const g of [...root.children]) if (g !== spacesGroup) disposeGroup(g);
  for (const g of [...spacesGroup.children]) disposeGroup(g);
  for (const m of state.materials?.values() || []) { m.map?.dispose(); m.dispose(); }
  Object.assign(state, { modelID: null, elements: new Map(), materials: new Map(), storeys: [], storeyVisible: new Map(), sel: null, tris: 0, cut: null });
  state.removed.clear(); removedStack.length = 0;
  if (state.plan != null) exitPlan(false);
  measure.points = []; measure.preview = null; measure.hover = null;
  updateMeasure();
  pickables = [];
  selectBox.visible = false;
  $('panel').hidden = true;
  $('storeys').replaceChildren();
  $('storeyGroup').hidden = true;
}

// ---------------------------------------------------------------- loading
// Parse into a fresh model first; the current model is only replaced once the new one succeeded,
// so a broken drop leaves the view as it was.
async function loadBuffer(bytes, name, baseUrl = null) {
  await apiReady;
  const head = new TextDecoder().decode(bytes.subarray(0, 256));
  if (!/ISO-10303-21/i.test(head)) throw new Error('not an IFC file (no ISO-10303-21 STEP header).');
  const t0 = performance.now();
  let modelID;
  try { modelID = api.OpenModel(bytes, { COORDINATE_TO_ORIGIN: false }); } catch (e) { throw new Error(`web-ifc could not parse it: ${e.message || e}`); }
  const model = { modelID, elements: new Map(), materials: new Map(), root: new THREE.Group(), spaces: new THREE.Group(), tris: 0 };
  const mat = new THREE.Matrix4();
  const texMaps = baseUrl ? textureMaps(modelID) : new Map();
  const addMesh = (flat, target, isSpace) => {
    const id = flat.expressID;
    const byColour = new Map();
    for (let i = 0; i < flat.geometries.size(); i++) {
      const pg = flat.geometries.get(i);
      const g = api.GetGeometry(modelID, pg.geometryExpressID);
      const verts = api.GetVertexArray(g.GetVertexData(), g.GetVertexDataSize());
      const idx = api.GetIndexArray(g.GetIndexData(), g.GetIndexDataSize());
      g.delete();
      if (!idx.length) continue;
      const pos = new Float32Array(verts.length / 2), nor = new Float32Array(verts.length / 2);
      for (let v = 0, o = 0; v < verts.length; v += 6, o += 3) {
        pos[o] = verts[v]; pos[o + 1] = verts[v + 1]; pos[o + 2] = verts[v + 2];
        nor[o] = verts[v + 3]; nor[o + 1] = verts[v + 4]; nor[o + 2] = verts[v + 5];
      }
      const geom = new THREE.BufferGeometry();
      geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      geom.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
      geom.setIndex(new THREE.BufferAttribute(new Uint32Array(idx), 1));
      const tm = !isSpace && texMaps.get(pg.geometryExpressID);
      if (tm) {
        const uv = new Float32Array(pos.length / 3 * 2), C = tm.coeff, lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
        for (let v = 0; v < pos.length; v++) { lo[v % 3] = Math.min(lo[v % 3], pos[v]); hi[v % 3] = Math.max(hi[v % 3], pos[v]); }
        const d = [0, 1, 2].map(k => tm.centre[k] - (lo[k] + hi[k]) / 2);
        for (let v = 0, o = 0; v < pos.length; v += 3, o += 2) {
          for (let k = 0; k < 2; k++) uv[o + k] = (pos[v] + d[0]) * C[0][k] + (pos[v + 1] + d[1]) * C[1][k] + (pos[v + 2] + d[2]) * C[2][k] + C[3][k];
        }
        geom.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
      }
      mat.fromArray(pg.flatTransformation);
      geom.applyMatrix4(mat);
      const c = isSpace ? SPACE_COLOUR : pg.color;
      const key = tm ? `tex:${tm.uri}` : `${c.x.toFixed(3)},${c.y.toFixed(3)},${c.z.toFixed(3)},${c.w.toFixed(3)}`;
      if (!byColour.has(key)) byColour.set(key, { colour: c, tex: tm && new URL(tm.uri, baseUrl).href, list: [] });
      byColour.get(key).list.push(geom);
      model.tris += idx.length / 3;
    }
    if (!byColour.size) return;
    const group = new THREE.Group();
    group.userData.expressID = id;
    for (const { colour, tex, list } of byColour.values()) {
      const merged = list.length === 1 ? list[0] : mergeGeometries(list, false);
      if (list.length > 1) list.forEach(g => g.dispose());
      const mesh = new THREE.Mesh(merged, tex ? textureMaterial(model.materials, tex) : materialFor(model.materials, colour));
      mesh.userData.expressID = id;
      group.add(mesh);
    }
    target.add(group);
    const line = api.GetLine(modelID, id);
    model.elements.set(id, {
      id, group, isSpace, name: val(line?.Name) || '', type: api.GetNameFromTypeCode(line?.type ?? 0) || 'IfcProduct',
      gid: val(line?.GlobalId) || '', storey: null, bbox: new THREE.Box3().setFromObject(group),
    });
  };
  const abandon = e => {
    disposeGroup(model.root); disposeGroup(model.spaces);
    for (const m of model.materials.values()) { m.map?.dispose(); m.dispose(); }
    try { api.CloseModel(modelID); } catch { /* ignore */ }
    throw e;
  };
  try {
    api.StreamAllMeshes(modelID, flat => addMesh(flat, model.root, false));
    api.StreamAllMeshesWithTypes(modelID, [WebIFC.IFCSPACE], flat => addMesh(flat, model.spaces, true));
  } catch (e) { abandon(new Error(`geometry failed: ${e.message || e}`)); }
  if (!model.elements.size) abandon(new Error('no geometry found in this file.'));
  clearModel();
  Object.assign(state, { modelID, fileName: name, elements: model.elements, materials: model.materials, tris: model.tris });
  root.add(model.root);
  spacesGroup.add(model.spaces);
  buildStoreys(modelID);
  state.parseMs = performance.now() - t0;
  let projectName = '';
  try { const p = api.GetLineIDsWithType(modelID, WebIFC.IFCPROJECT); if (p.size()) projectName = val(api.GetLine(modelID, p.get(0)).Name) || ''; } catch { /* optional */ }
  $('title').textContent = projectName || name.replace(/\.ifc$/i, '');
  document.title = `${projectName || name} · Monarch IFC viewer`;
  $('empty').hidden = true;
  $('search').disabled = false;
  homeView();
  updateStats();
  setSelection(null);
  showTutorial();
}

// Storey of an element: direct containment (IfcRelContainedInSpatialStructure), else walk up
// aggregation / containment parents (parts of assemblies, elements contained in spaces).
function buildStoreys(modelID) {
  const parent = new Map();
  const rel = (type, from, to) => {
    const ids = api.GetLineIDsWithType(modelID, type);
    for (let i = 0; i < ids.size(); i++) {
      const line = api.GetLine(modelID, ids.get(i));
      const p = val(line?.[from]);
      for (const h of line?.[to] || []) if (p != null && val(h) != null) parent.set(val(h), p);
    }
  };
  rel(WebIFC.IFCRELCONTAINEDINSPATIALSTRUCTURE, 'RelatingStructure', 'RelatedElements');
  rel(WebIFC.IFCRELAGGREGATES, 'RelatingObject', 'RelatedObjects');
  // Elevation is in project length units; geometry comes in metres.
  let unit = 1;
  const units = api.GetLineIDsWithType(modelID, WebIFC.IFCSIUNIT);
  for (let i = 0; i < units.size(); i++) {
    const u = api.GetLine(modelID, units.get(i));
    if (val(u?.UnitType) === 'LENGTHUNIT') unit = { MILLI: 0.001, CENTI: 0.01, DECI: 0.1 }[val(u?.Prefix)] ?? 1;
  }
  const storeyIds = api.GetLineIDsWithType(modelID, WebIFC.IFCBUILDINGSTOREY);
  const storeys = [];
  for (let i = 0; i < storeyIds.size(); i++) {
    const id = storeyIds.get(i), line = api.GetLine(modelID, id);
    storeys.push({ id, name: val(line?.Name) || `Storey ${id}`, elevation: (Number(val(line?.Elevation)) || 0) * unit, count: 0 });
  }
  storeys.sort((a, b) => a.elevation - b.elevation);
  const isStorey = new Set(storeys.map(s => s.id));
  for (const el of state.elements.values()) {
    let cur = el.id, hops = 0;
    while (cur != null && !isStorey.has(cur) && hops++ < 32) cur = parent.get(cur);
    el.storey = isStorey.has(cur) ? cur : null;
    const s = storeys.find(s => s.id === el.storey);
    if (s) s.count++;
  }
  const orphans = [...state.elements.values()].filter(e => e.storey == null && !e.isSpace).length;
  if (orphans) storeys.push({ id: null, name: 'Site', elevation: Infinity, count: orphans });
  state.storeys = storeys;
  for (const s of storeys) state.storeyVisible.set(s.id, true);
  const chips = $('storeys');
  chips.replaceChildren(...storeys.map(s => {
    const b = document.createElement('button');
    b.textContent = s.name;
    b.className = 'on';
    b.dataset.storey = String(s.id);
    b.title = s.id == null ? 'Elements outside every storey (site, terrain, garden)' : `${s.name} · ${s.elevation.toFixed(2)} m · ${s.count} elements`;
    b.onclick = ev => {
      if (ev.altKey) for (const o of storeys) state.storeyVisible.set(o.id, o === s);
      else state.storeyVisible.set(s.id, !state.storeyVisible.get(s.id));
      applyVisibility();
    };
    return b;
  }));
  $('storeyGroup').hidden = storeys.length < 2;
  const floors = storeys.filter(s => s.id != null);
  $('cut').innerHTML = '<option value="">off</option>' + floors.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
  $('cutGroup').hidden = !floors.length;
  applyVisibility();
}
const storeyOf = id => state.storeys.find(s => s.id === id);
function groundStorey() {
  const floors = state.storeys.filter(s => s.id != null);
  return (floors.find(s => Math.abs(s.elevation) < 1e-6) || floors[0])?.id ?? null;
}
// ---------------------------------------------------------------- visibility, cut, removal
// Cut shows the cut storey and everything below it (plus Site), clipped at floor + height.
function storeyVisible(id) {
  if (state.storeyVisible.get(id) === false) return false;
  const c = state.cut && storeyOf(state.cut.storey);
  return !(c && id != null && (storeyOf(id)?.elevation ?? -Infinity) > c.elevation);
}
let pickables = [];
function applyVisibility() {
  pickables = [];
  for (const el of state.elements.values()) {
    el.group.visible = !state.removed.has(el.id) && storeyVisible(el.storey);
    if (el.group.visible && (state.spaces || !el.isSpace)) el.group.traverse(o => { if (o.isMesh) pickables.push(o); });
  }
  for (const b of $('storeys').children) {
    const id = b.dataset.storey === 'null' ? null : Number(b.dataset.storey), on = state.storeyVisible.get(id) !== false;
    b.classList.toggle('on', on);
    b.style.opacity = on && !storeyVisible(id) ? 0.45 : '';
  }
  const c = state.cut && storeyOf(state.cut.storey);
  if (c) {
    clipPlane.constant = c.elevation + state.cut.height;
    renderer.clippingPlanes = [clipPlane];
    POCHE.value = 1;
  } else {
    renderer.clippingPlanes = [];
    POCHE.value = 0;
  }
  if (state.sel != null && !state.elements.get(state.sel)?.group.visible) setSelection(null);
  syncTools();
}
// Play-around removal: X hides the selected element for this session only (nothing is written
// to the file; a reload brings everything back). Z undoes the last removal, Shift+Z restores all.
function removeSelected() {
  if (state.sel == null) return;
  const id = state.sel;
  setSelection(null);
  state.removed.add(id);
  removedStack.push(id);
  applyVisibility();
}
function undoRemove(all = false) {
  if (!removedStack.length) return;
  const ids = all ? removedStack.splice(0) : [removedStack.pop()];
  for (const id of ids) state.removed.delete(id);
  applyVisibility();
  if (!all) setSelection(ids[0]);
}
function setCut(storey, height = state.cut?.height ?? DEFAULT_CUT) {
  state.cut = storey != null && storeyOf(storey) ? { storey, height } : null;
  if (state.plan != null) { if (state.cut) enterPlan(state.cut.storey, true); else exitPlan(); }
  applyVisibility();
}

// ---------------------------------------------------------------- selection + property panel
const HIGHLIGHT = new THREE.Color(0x3ea6ff);
let panelToken = 0;
function setSelection(id) {
  if (state.sel != null) {
    const prev = state.elements.get(state.sel);
    prev?.group.traverse(o => { if (o.isMesh && o.userData.orig) { o.material.dispose(); o.material = o.userData.orig; delete o.userData.orig; } });
  }
  state.sel = id != null && state.elements.has(id) ? id : null;
  selectBox.visible = false;
  const panel = $('panel'), body = $('panelBody');
  if (state.sel == null) { panel.hidden = true; $('stSel').textContent = '—'; return; }
  const el = state.elements.get(state.sel);
  el.group.traverse(o => {
    if (!o.isMesh) return;
    const m = patchMaterial(o.material.clone());
    m.emissive.copy(HIGHLIGHT); m.emissiveIntensity = 0.4;
    o.userData.orig = o.material; o.material = m;
  });
  selectBox.box.copy(el.bbox); selectBox.visible = true;
  $('stSel').innerHTML = `${esc(el.name || el.type)}<small>${esc(el.type)}</small>`;
  const storey = state.storeys.find(s => s.id === el.storey);
  const size = new THREE.Vector3(); el.bbox.getSize(size);
  panel.hidden = false;
  body.innerHTML = `<div class="eyebrow"><i></i>${esc(el.type)}</div>
    <h2>${esc(el.name || '(unnamed)')}</h2>
    <div class="key">#${el.id} · ${esc(el.gid)}</div>
    <div class="actions"><button id="focusBtn">Focus <kbd>F</kbd></button><button id="removeBtn">Remove <kbd>X</kbd></button></div>
    <div class="hint">Deselect: Esc, ×, or click the object again · Remove hides it for this session only (Z undo, Shift+Z restore all)</div>
    <table><tr><th>Storey</th><td>${esc(storey?.name ?? '—')}</td></tr>
    <tr><th>Size (x · y · z)</th><td class="mono">${size.x.toFixed(2)} · ${size.z.toFixed(2)} · ${size.y.toFixed(2)} m</td></tr></table>
    <p class="empty" id="psetWait">Loading property sets…</p>`;
  $('focusBtn').onclick = () => focusOn(el);
  $('removeBtn').onclick = () => removeSelected();
  const token = ++panelToken;
  // includeTypeProperties=true returns nothing for elements without a type in web-ifc 0.0.78,
  // so the type's psets are fetched separately and appended.
  const P = api.properties, mid = state.modelID;
  Promise.all([P.getPropertySets(mid, el.id, true), P.getTypeProperties(mid, el.id, true).catch(() => [])]).then(([psets, types]) => {
    if (token !== panelToken) return;
    $('psetWait')?.remove();
    const typePsets = types.flatMap(t => (t.HasPropertySets || []).map(ps => ({ ...ps, Name: { value: `${fmt(ps.Name)} (type ${fmt(t.Name) || ''})`.trim() } })));
    const all = [...psets, ...typePsets];
    if (!all.length) { body.insertAdjacentHTML('beforeend', '<p class="empty">No property sets.</p>'); return; }
    body.insertAdjacentHTML('beforeend', all.map(renderPset).join(''));
  }).catch(e => { if (token === panelToken) body.insertAdjacentHTML('beforeend', `<p class="empty">Properties failed: ${esc(e.message || e)}</p>`); });
}
function fmt(v) {
  v = val(v);
  if (v == null) return '—';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(+v.toFixed(4));
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (Array.isArray(v)) return v.map(fmt).join(', ');
  if (typeof v === 'object') return fmt(v.NominalValue ?? v.wrappedValue ?? v.Name);
  return String(v);
}
function propRows(p) {
  const name = fmt(p.Name);
  if (p.HasProperties) return [[name, ''], ...p.HasProperties.flatMap(propRows).map(([k, v]) => [`${name}.${k}`, v])].slice(1);
  let value;
  if ('NominalValue' in p) value = fmt(p.NominalValue);
  else if (p.EnumerationValues) value = fmt(p.EnumerationValues);
  else if (p.ListValues) value = fmt(p.ListValues);
  else if (p.LowerBoundValue || p.UpperBoundValue) value = `${fmt(p.LowerBoundValue)} … ${fmt(p.UpperBoundValue)}`;
  else {
    const q = ['LengthValue', 'AreaValue', 'VolumeValue', 'CountValue', 'WeightValue', 'TimeValue'].find(k => k in p);
    value = q ? fmt(p[q]) : '—';
  }
  return [[name, value]];
}
function renderPset(ps) {
  const items = ps.HasProperties || ps.Quantities || [];
  const rows = items.flatMap(propRows);
  const name = fmt(ps.Name) || api.GetNameFromTypeCode(ps.type);
  const link = s => /^https?:\/\/\S+$/.test(s) ? `<a href="${esc(s)}" target="_blank" rel="noopener">${esc(s)}</a>` : esc(s);
  return `<details open><summary>${esc(name)}<span>${rows.length}</span></summary><table>${rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${link(v)}</td></tr>`).join('')}</table></details>`;
}

const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();
/** Nearest visible hit under client (x, y), below the section plane: {id, point, face, object, distance}. */
function pickHit(x, y) {
  camera.updateMatrixWorld();
  const r = canvas.getBoundingClientRect();
  raycaster.setFromCamera(ndc.set(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1), camera);
  const clipY = state.cut ? clipPlane.constant + 1e-4 : Infinity;
  for (const h of raycaster.intersectObjects(pickables, false)) {
    if (h.point.y > clipY) continue;
    const el = state.elements.get(h.object.userData.expressID);
    if (el) return { id: el.id, point: h.point.clone(), face: h.face, object: h.object, distance: h.distance };
  }
  return null;
}
const pick = (x, y) => pickHit(x, y)?.id ?? null;
const toIfc = v => [v.x, -v.z, v.y];                      // three (y up) -> IFC (x east, y north, z up)
const xyz = (a, d = 3) => a.map(n => (Math.abs(n) < 0.5 * 10 ** -d ? 0 : n).toFixed(d)).join(', ');

// ---------------------------------------------------------------- measure
// M toggles. Left-click two points: a click may slide up to 10 px and last up to 400 ms
// (trackpad clicks); in Measure mode the plain left button never turns the camera, Space+drag
// does. A click after a finished measurement starts a new one, Esc clears, a second Esc leaves.
// Snapping: corners of feature edges (30 deg) within 10 px, else edges within 7 px, else the
// face; only edges not behind the surface under the cursor. Alt: no snap. Shift: lock the
// dominant axis. Lines are screen-space ribbons (faint dashed behind geometry), labels HTML.
const MEASURE_CLICK = { px: 10, ms: 400 }, SNAP_VERTEX_PX = 10, SNAP_EDGE_PX = 7, EDGE_MAX_TRIS = 25000;
const AXIS_COLOURS = { x: '#ff6b6b', y: '#6fdc8c', z: '#5aa9ff' };        // IFC x east, y north, z up
const measure = { points: [], preview: null, hover: null, pointer: null, legs: [] };
let measureSpace = false;                                   // Space held: left-drag navigates while measuring

const RIBBON_VERT = `uniform vec3 uA, uB; uniform vec2 uRes; uniform float uWidth, uNudge, uNear, uOrtho;
attribute vec2 corner;
varying vec2 vDash;
void main() {
  vec4 a = viewMatrix * vec4(uA, 1.0), b = viewMatrix * vec4(uB, 1.0);
  if (uOrtho < 0.5) {
    float nz = -uNear * 1.5;
    if (a.z > nz && b.z > nz) { gl_Position = vec4(0.0, 0.0, 2.0, 1.0); vDash = vec2(0.0, 1.0); return; }
    if (a.z > nz) a = mix(a, b, (nz - a.z) / (b.z - a.z));
    else if (b.z > nz) b = mix(b, a, (nz - b.z) / (a.z - b.z));
  }
  if (uNudge > 0.5) {
    if (uOrtho > 0.5) { a.z += 0.03; b.z += 0.03; }
    else { a.xyz *= 1.0 - min(0.3, 0.003 + 0.015 / max(length(a.xyz), 0.05));
           b.xyz *= 1.0 - min(0.3, 0.003 + 0.015 / max(length(b.xyz), 0.05)); }
  }
  vec4 ca = projectionMatrix * a, cb = projectionMatrix * b;
  vec2 sa = ca.xy / ca.w * 0.5 * uRes, sb = cb.xy / cb.w * 0.5 * uRes;
  vec2 d = sb - sa;
  float len = length(d);
  vec2 dir = len > 1e-4 ? d / len : vec2(1.0, 0.0), n = vec2(-dir.y, dir.x);
  bool atB = corner.x > 0.5;
  vec4 c = atB ? cb : ca;
  vec2 px = n * corner.y * uWidth * 0.5 + dir * (atB ? 0.5 : -0.5) * uWidth * 0.5;
  c.xy += px / (0.5 * uRes) * c.w;
  vDash = vec2((atB ? len : 0.0) * c.w, c.w);
  gl_Position = c;
}`;
const RIBBON_FRAG = `uniform vec3 uColour; uniform float uOpacity; uniform vec2 uDash; varying vec2 vDash;
void main() {
  if (uDash.x > 0.0 && mod(vDash.x / vDash.y, uDash.x + uDash.y) > uDash.x) discard;
  gl_FragColor = vec4(uColour, uOpacity);
}`;
const ribbonGeom = new THREE.BufferGeometry();
ribbonGeom.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(12), 3));
ribbonGeom.setAttribute('corner', new THREE.Float32BufferAttribute([0, -1, 0, 1, 1, -1, 1, 1], 2));
ribbonGeom.setIndex([0, 2, 1, 1, 2, 3]);
function makeRibbon({ colour, width, opacity = 1, dash = [0, 0], depthTest = true, order = 30 }) {
  const u = { uA: { value: new THREE.Vector3() }, uB: { value: new THREE.Vector3() }, uRes: { value: new THREE.Vector2(1, 1) },
    uWidth: { value: width }, uColour: { value: new THREE.Color(colour) }, uOpacity: { value: opacity },
    uDash: { value: new THREE.Vector2(...dash) }, uNudge: { value: depthTest ? 1 : 0 }, uNear: { value: 0.05 }, uOrtho: { value: 0 } };
  const m = new THREE.Mesh(ribbonGeom, new THREE.ShaderMaterial({ uniforms: u, vertexShader: RIBBON_VERT, fragmentShader: RIBBON_FRAG,
    transparent: true, depthTest, depthWrite: false, side: THREE.DoubleSide }));
  m.frustumCulled = false; m.renderOrder = order; m.visible = false; m.raycast = () => {};
  m.onBeforeRender = (r, s, cam) => { r.getSize(u.uRes.value); u.uNear.value = cam.near; u.uOrtho.value = cam.isOrthographicCamera ? 1 : 0; };
  scene.add(m);
  return m;
}
function setRibbon(m, a, b) {
  m.material.uniforms.uA.value.copy(a);
  m.material.uniforms.uB.value.copy(b);
  m.visible = true;
}
const mRibbons = {
  hidden: makeRibbon({ colour: '#ffb454', width: 1.5, opacity: 0.5, dash: [6, 5], depthTest: false, order: 30 }),
  casing: makeRibbon({ colour: '#070a11', width: 5, opacity: 0.5, order: 31 }),
  line: makeRibbon({ colour: '#ffb454', width: 2.25, order: 32 }),
  x: makeRibbon({ colour: AXIS_COLOURS.x, width: 1.25, opacity: 0.75, dash: [4, 4], depthTest: false, order: 29 }),
  y: makeRibbon({ colour: AXIS_COLOURS.y, width: 1.25, opacity: 0.75, dash: [4, 4], depthTest: false, order: 29 }),
  z: makeRibbon({ colour: AXIS_COLOURS.z, width: 1.25, opacity: 0.75, dash: [4, 4], depthTest: false, order: 29 }),
};
const measureBox = $('measureBox'), measureSvg = $('measureSvg'), mLabel = $('measureLabel');
const axisLabels = Object.fromEntries(['x', 'y', 'z'].map(k => [k, document.querySelector(`.mlab.axis.${k}`)]));

function axisSnap(a, b) {
  const d = b.clone().sub(a);
  const ax = ['x', 'y', 'z'].reduce((m, k) => (Math.abs(d[k]) > Math.abs(d[m]) ? k : m), 'x');
  const out = a.clone();
  out[ax] = b[ax];
  return out;
}
/** The dx / dy / dz legs from a to b (three.js points) in IFC axes, the trivial ones dropped. */
function measureLegs(a, b) {
  const ia = toIfc(a), ib = toIfc(b), d = ib.map((v, i) => v - ia[i]);
  const corners = [a, new THREE.Vector3(b.x, a.y, a.z), new THREE.Vector3(b.x, a.y, b.z), b];
  const legs = ['x', 'y', 'z'].map((k, i) => ({ k, d: d[i], from: corners[i], to: corners[i + 1] })).filter(l => Math.abs(l.d) >= 0.005);
  return legs.length >= 2 ? legs : [];
}
function updateMeasure() {
  const [a, b0] = measure.points, b = b0 || measure.preview;
  for (const m of Object.values(mRibbons)) m.visible = false;
  measure.legs = state.measure && a && b ? measureLegs(a, b) : [];
  if (state.measure && a && b) {
    setRibbon(mRibbons.hidden, a, b); setRibbon(mRibbons.casing, a, b); setRibbon(mRibbons.line, a, b);
    for (const l of measure.legs) setRibbon(mRibbons[l.k], l.from, l.to);
  }
  if (!state.measure) { measureBox.hidden = true; return; }
  const help = `<div class="mhelp">Space+drag: ${state.plan != null ? 'pan' : state.nav} · Shift: axis · Alt: no snap · Esc: clear</div>`;
  let html;
  if (!a) html = `<div class="mstep">Click the first point</div>${help}`;
  else if (!b) html = `<div class="mstep">Click the second point</div><div>A <span>${xyz(toIfc(a))}</span></div>${help}`;
  else {
    const ia = toIfc(a), ib = toIfc(b), d = ib.map((v, i) => v - ia[i]);
    html = `<b class="${b0 ? '' : 'live'}">${a.distanceTo(b).toFixed(3)} m</b>`
      + `<div class="mdelta">${['x', 'y', 'z'].map((k, i) => `<span class="m${k}">Δ${k} ${d[i].toFixed(3)}</span>`).join('')}</div>`
      + `<div>A <span>${xyz(ia)}</span></div><div>B <span>${xyz(ib)}</span></div>`
      + (b0 ? '<div class="mhelp">Click to start a new measurement · Esc: clear</div>' : help);
  }
  measureBox.innerHTML = html;
  measureBox.hidden = false;
}
function setMeasure(on) {
  state.measure = on;
  measure.points = []; measure.preview = null; measure.hover = null;
  document.body.classList.toggle('measuring', on);
  if (on) { setSelection(null); hideTutorial(); }
  applyNavMode();
  updateMeasure();
  syncTools();
}
/** Place a measure point at client (x, y), as a click does; returns the snapped hit or null. */
function measureClick(x, y, { alt = false, shift = false } = {}) {
  const hit = snapAt(x, y, alt);
  if (!hit) return null;
  if (measure.points.length >= 2) measure.points = [];
  let p = hit.point.clone();
  if (measure.points.length === 1 && shift) p = axisSnap(measure.points[0], p);
  measure.points.push(p);
  measure.preview = null;
  measure.hover = hit;
  updateMeasure();
  return hit;
}

// Overlay (SVG marks + HTML labels), re-projected every frame while measuring.
const _sv = new THREE.Vector3(), _sa = new THREE.Vector3(), _sb = new THREE.Vector3();
let canvasRect = null;
function toScreen(v) {
  _sv.copy(v).applyMatrix4(camera.matrixWorldInverse);
  if (camera.isPerspectiveCamera && _sv.z > -camera.near) return null;
  _sv.applyMatrix4(camera.projectionMatrix);
  return { x: canvasRect.left + (_sv.x + 1) / 2 * canvasRect.width, y: canvasRect.top + (1 - _sv.y) / 2 * canvasRect.height };
}
function screenSeg(a, b) {
  _sa.copy(a).applyMatrix4(camera.matrixWorldInverse); _sb.copy(b).applyMatrix4(camera.matrixWorldInverse);
  const nz = -camera.near * 1.5;
  let clipA = false, clipB = false;
  if (camera.isPerspectiveCamera) {
    if (_sa.z > nz && _sb.z > nz) return null;
    if (_sa.z > nz) { _sa.lerp(_sb, (nz - _sa.z) / (_sb.z - _sa.z)); clipA = true; }
    else if (_sb.z > nz) { _sb.lerp(_sa, (nz - _sb.z) / (_sa.z - _sb.z)); clipB = true; }
  }
  const px = v => { v.applyMatrix4(camera.projectionMatrix); return { x: canvasRect.left + (v.x + 1) / 2 * canvasRect.width, y: canvasRect.top + (1 - v.y) / 2 * canvasRect.height }; };
  return { a: px(_sa), b: px(_sb), clipA, clipB };
}
function placeLabel(el, p, text, cls) {
  if (!p || p.x < -200 || p.y < -200 || p.x > innerWidth + 200 || p.y > innerHeight + 200) { el.hidden = true; return; }
  if (el.textContent !== text) el.textContent = text;
  if (cls !== undefined && el.dataset.cls !== cls) { el.className = cls; el.dataset.cls = cls; }
  el.style.transform = `translate(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px) translate(-50%, -50%)`;
  el.hidden = false;
}
const f1 = n => n.toFixed(1);
function drawMeasureOverlay() {
  if (!state.measure) {
    if (measureSvg.childElementCount) measureSvg.innerHTML = '';
    mLabel.hidden = true; for (const el of Object.values(axisLabels)) el.hidden = true;
    return;
  }
  camera.updateMatrixWorld();
  canvasRect = canvas.getBoundingClientRect();
  const [a, b0] = measure.points, b = b0 || measure.preview;
  let svg = '';
  // snap marker: square = corner, diamond + piece of edge = edge, ring = face
  const h = measure.hover, hp = h && toScreen(h.point);
  if (hp && !(b0 && h.point.equals(b0))) {
    const box = (r, extra = '') => `x="${f1(hp.x - r)}" y="${f1(hp.y - r)}" width="${2 * r}" height="${2 * r}" ${extra}`;
    if (h.kind === 'vertex') svg += `<rect class="haloShape" ${box(5.5, 'rx="1.5"')}/><rect class="snap" ${box(5.5, 'rx="1.5"')}/>`;
    else if (h.kind === 'edge') {
      const s = screenSeg(h.edge[0], h.edge[1]);
      if (s) {
        const dx = s.b.x - s.a.x, dy = s.b.y - s.a.y, L = Math.hypot(dx, dy) || 1, ux = dx / L, uy = dy / L;
        const ta = Math.min(36, Math.hypot(hp.x - s.a.x, hp.y - s.a.y)), tb = Math.min(36, Math.hypot(hp.x - s.b.x, hp.y - s.b.y));
        const seg = `x1="${f1(hp.x - ux * ta)}" y1="${f1(hp.y - uy * ta)}" x2="${f1(hp.x + ux * tb)}" y2="${f1(hp.y + uy * tb)}"`;
        svg += `<line class="halo" ${seg}/><line class="snapEdge" ${seg}/>`;
      }
      const rot = `transform="rotate(45 ${f1(hp.x)} ${f1(hp.y)})"`;
      svg += `<rect class="haloShape" ${box(4, rot)}/><rect class="snap" ${box(4, rot)}/>`;
    } else svg += `<circle class="haloShape" cx="${f1(hp.x)}" cy="${f1(hp.y)}" r="4"/><circle class="snapFace" cx="${f1(hp.x)}" cy="${f1(hp.y)}" r="4"/>`;
  }
  let main = null;
  if (a && b) {
    const s = screenSeg(a, b);
    if (s && b0) {
      const dx = s.b.x - s.a.x, dy = s.b.y - s.a.y, L = Math.hypot(dx, dy) || 1, nx = -dy / L * 7, ny = dx / L * 7;
      for (const [p, clipped] of [[s.a, s.clipA], [s.b, s.clipB]]) if (!clipped) {
        const t = `x1="${f1(p.x - nx)}" y1="${f1(p.y - ny)}" x2="${f1(p.x + nx)}" y2="${f1(p.y + ny)}"`;
        svg += `<line class="halo" ${t}/><line class="tick" ${t}/>`;
      }
    }
    if (s) main = toScreen(_sv.copy(a).add(b).multiplyScalar(0.5).clone()) || { x: (s.a.x + s.b.x) / 2, y: (s.a.y + s.b.y) / 2 };
  }
  for (const p of [a, b0]) { const q = p && toScreen(p); if (q) svg += `<circle class="end" cx="${f1(q.x)}" cy="${f1(q.y)}" r="3.5"/>`; }
  measureSvg.innerHTML = svg;
  if (main) placeLabel(mLabel, main, `${a.distanceTo(b).toFixed(2)} m`, b0 ? 'mlab main' : 'mlab main live');
  else mLabel.hidden = true;
  // Leg labels only where they fit (56 px on screen, no overlap); the box lists all three.
  const legs = new Map(measure.legs.map(l => [l.k, l]));
  const taken = mLabel.hidden ? [] : [mLabel.getBoundingClientRect()];
  const overlaps = q => taken.some(t => q.left < t.right + 4 && q.right > t.left - 4 && q.top < t.bottom + 3 && q.bottom > t.top - 3);
  for (const [k, el] of Object.entries(axisLabels)) {
    const l = legs.get(k), s = l && screenSeg(l.from, l.to);
    if (!s || Math.hypot(s.b.x - s.a.x, s.b.y - s.a.y) < 56) { el.hidden = true; continue; }
    placeLabel(el, { x: (s.a.x + s.b.x) / 2, y: (s.a.y + s.b.y) / 2 }, `Δ${k} ${Math.abs(l.d).toFixed(2)}`);
    if (el.hidden) continue;
    const q = el.getBoundingClientRect();
    if (overlaps(q)) el.hidden = true; else taken.push(q);
  }
}

// Magnetic snap: corner > edge > face.
const _plane = new THREE.Plane(), _vinv = new THREE.Matrix4(), _toC = new THREE.Vector3(), _wa = new THREE.Vector3(), _wb = new THREE.Vector3(),
  _va = new THREE.Vector3(), _vb = new THREE.Vector3();
function featureEdges(mesh) {
  if (mesh.userData.edges === undefined) {
    const tris = (mesh.geometry.index?.count ?? 0) / 3;
    mesh.userData.edges = tris > EDGE_MAX_TRIS ? null : new THREE.EdgesGeometry(mesh.geometry, 30).attributes.position.array;
  }
  return mesh.userData.edges;
}
/** The measure point under client (x, y): {point, kind: 'vertex' | 'edge' | 'face', id, edge?}. */
function snapAt(x, y, noSnap = false) {
  const hit = pickHit(x, y);
  if (!hit) return null;
  const face = { point: hit.point, kind: 'face', id: hit.id };
  if (noSnap) return face;
  _vinv.copy(camera.matrixWorld).invert();
  const r = canvas.getBoundingClientRect(), isPersp = camera.isPerspectiveCamera, ray = raycaster.ray;
  const pxPerM = isPersp ? 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) / r.height : (camera.top - camera.bottom) / camera.zoom / r.height;
  const plane = _plane.setFromNormalAndCoplanarPoint(_toC.copy(hit.face?.normal ?? ray.direction).transformDirection(hit.object.matrixWorld), hit.point);
  if (plane.normal.dot(ray.direction) > 0) plane.negate();
  const visible = w => plane.distanceToPoint(w) >= -0.002;
  const maxRay = hit.distance + 0.05 + 0.05 * hit.distance;
  const clipY = state.cut ? clipPlane.constant : Infinity;
  const toPx = v => [r.left + (v.x + 1) / 2 * r.width, r.top + (1 - v.y) / 2 * r.height];
  let vtx = null, edge = null;
  for (const mesh of pickables) {
    let ws = mesh.userData.wsphere;
    if (!ws) {
      if (!mesh.geometry.boundingSphere) mesh.geometry.computeBoundingSphere();
      ws = mesh.userData.wsphere = mesh.geometry.boundingSphere.clone().applyMatrix4(mesh.matrixWorld);
    }
    _toC.copy(ws.center).sub(ray.origin);
    const t = _toC.dot(ray.direction);
    if (t + ws.radius < 0 || t - ws.radius > maxRay) continue;
    const cone = SNAP_VERTEX_PX * pxPerM * (isPersp ? Math.max(0, t) + ws.radius : 1);
    if (_toC.lengthSq() - t * t > (ws.radius + cone) ** 2) continue;
    const pos = featureEdges(mesh);
    if (!pos) continue;
    const id = mesh.userData.expressID;
    for (let i = 0; i < pos.length; i += 6) {
      _wa.fromArray(pos, i).applyMatrix4(mesh.matrixWorld); _wb.fromArray(pos, i + 3).applyMatrix4(mesh.matrixWorld);
      if (_wa.y > clipY && _wb.y > clipY) continue;          // cut: an edge through the section plane
      if (_wa.y > clipY) _wa.lerp(_wb, (_wa.y - clipY) / (_wa.y - _wb.y));   // ends there, a section corner
      else if (_wb.y > clipY) _wb.lerp(_wa, (_wb.y - clipY) / (_wb.y - _wa.y));
      _va.copy(_wa).applyMatrix4(_vinv); _vb.copy(_wb).applyMatrix4(_vinv);
      const da = -_va.z, db = -_vb.z;
      if (isPersp && (da < camera.near || db < camera.near)) continue;
      const [ax, ay] = toPx(_va.applyMatrix4(camera.projectionMatrix)), [bx, by] = toPx(_vb.applyMatrix4(camera.projectionMatrix));
      for (const [px, py, dep, w] of [[ax, ay, da, _wa], [bx, by, db, _wb]]) {
        if (!visible(w)) continue;
        const dd = Math.hypot(px - x, py - y);
        if (dd <= SNAP_VERTEX_PX && (!vtx || dd < vtx.d - 0.5 || (dd < vtx.d + 0.5 && dep < vtx.depth)))
          vtx = { d: dd, depth: dep, point: w.clone(), kind: 'vertex', id };
      }
      if (vtx) continue;
      const ex = bx - ax, ey = by - ay, L2 = ex * ex + ey * ey;
      if (L2 < 1) continue;
      const s = Math.min(1, Math.max(0, ((x - ax) * ex + (y - ay) * ey) / L2));
      const dd = Math.hypot(ax + s * ex - x, ay + s * ey - y);
      if (dd > SNAP_EDGE_PX || (edge && dd >= edge.d)) continue;
      const u = isPersp ? (s / db) / ((1 - s) / da + s / db) : s;
      const dep = da + (db - da) * u, p = _wa.clone().lerp(_wb, u);
      if (!visible(p)) continue;
      edge = { d: dd, depth: dep, point: p, kind: 'edge', id, edge: [_wa.clone(), _wb.clone()] };
    }
  }
  return vtx || edge || face;
}
let hoverPending = null;
function requestHover(p) {
  if (!hoverPending) requestAnimationFrame(() => hover(hoverPending));
  hoverPending = p;
}
function hover(p) {
  hoverPending = null;
  if (!p || !state.measure) return;
  measure.pointer = p;
  const hit = snapAt(p.x, p.y, p.alt);
  measure.hover = hit;
  if (hit && measure.points.length === 1) measure.preview = p.shift ? axisSnap(measure.points[0], hit.point) : hit.point;
  updateMeasure();
}

// ---------------------------------------------------------------- camera: orbit / look, plan, WASD, fit
const modelBox = new THREE.Box3();
function homeView() {
  if (state.plan != null) exitPlan();
  // Frame the building (elements inside a storey), not the whole site, so a house with a
  // large terrain still fills the view on first load. Fall back to everything.
  modelBox.makeEmpty();
  for (const el of state.elements.values()) if (el.storey != null && !el.isSpace) modelBox.union(el.bbox);
  if (modelBox.isEmpty()) modelBox.setFromObject(root);
  if (modelBox.isEmpty()) return;
  const c = new THREE.Vector3(), s = new THREE.Vector3();
  modelBox.getCenter(c); modelBox.getSize(s);
  const d = Math.max(s.x, s.y, s.z, 1) * 1.4 * Math.tan(THREE.MathUtils.degToRad(25)) / Math.tan(THREE.MathUtils.degToRad(persp.fov / 2));
  persp.position.set(c.x - d * 0.9, c.y + d * 0.7, c.z + d * 1.0);
  orbit.target.copy(c);
  orbit.update();
}
function focusOn(el) {
  const c = new THREE.Vector3(), s = new THREE.Vector3();
  el.bbox.getCenter(c); el.bbox.getSize(s);
  if (state.plan != null) {
    const off = ortho.position.clone().sub(planControls.target);
    planControls.target.set(c.x, planControls.target.y, c.z);
    ortho.position.copy(planControls.target).add(off);
    planControls.update();
    return;
  }
  const d = Math.max(s.length() * 1.6, 1.2);
  const dir = persp.position.clone().sub(orbit.target).normalize();
  if (dir.lengthSq() < 1e-6) dir.set(-0.6, 0.5, 0.7);
  persp.position.copy(c).addScaledVector(dir, d);
  orbit.target.copy(c);
  orbit.update();
}
function setFov(value) {
  if (!FOV_PRESETS.includes(value)) return;
  persp.fov = value;
  persp.updateProjectionMatrix();
  syncTools();
}
// Plan: top-down orthographic view of one storey, north up, implying a cut at floor + 1.2 m.
const currentSpan = () => (ortho.top - ortho.bottom) / ortho.zoom;
function setOrthoSpan(span) {
  const a = canvas.clientWidth / Math.max(canvas.clientHeight, 1);
  ortho.top = span / 2; ortho.bottom = -span / 2;
  ortho.left = -span * a / 2; ortho.right = span * a / 2;
  ortho.zoom = 1;
  ortho.updateProjectionMatrix();
}
function enterPlan(id = groundStorey(), keepCut = false) {
  const s = storeyOf(id);
  if (!s || id == null) return;
  state.plan = id;
  if (!keepCut && (!state.cut || state.cut.storey !== id)) state.cut = { storey: id, height: state.cut?.height ?? DEFAULT_CUT };
  const b = new THREE.Box3();
  for (const el of state.elements.values()) if (el.storey === id && !el.isSpace) b.union(el.bbox);
  if (b.isEmpty()) b.copy(modelBox);
  const c = new THREE.Vector3(), size = new THREE.Vector3();
  b.getCenter(c); b.getSize(size);
  // Fit the storey into the strip between toolbar and stat bar, not behind them.
  const w = Math.max(canvas.clientWidth, 1), h = Math.max(canvas.clientHeight, 1);
  const top = $('bar').getBoundingClientRect().bottom, bottom = $('stats').getBoundingClientRect().top;
  const free = THREE.MathUtils.clamp(bottom - top - 16, h * 0.35, h);
  const span = Math.max(size.z, size.x * free / w, 4) * 1.08 * h / free;
  c.z -= ((top + bottom) / 2 - h / 2) * span / h;                // screen up = north = three -z
  ortho.position.set(c.x, s.elevation + 100, c.z);
  planControls.target.set(c.x, s.elevation, c.z);
  setOrthoSpan(span);
  ortho.lookAt(planControls.target);
  camera = ortho;
  orbit.enabled = false; planControls.enabled = true; controls = planControls;
  planControls.update();
  applyVisibility();
  updateMeasure();
}
function exitPlan(apply = true) {
  state.plan = null;
  camera = persp;
  orbit.enabled = true; planControls.enabled = false; controls = orbit;
  if (apply) { applyVisibility(); updateMeasure(); }
}
function togglePlan() {
  if (state.plan != null) exitPlan();
  else enterPlan(state.cut?.storey ?? groundStorey());
}
// Measure mode owns the plain left button; holding Space gives left-drag back to navigation
// (look / orbit, plan: pan). Middle-drag orbits, right-drag pans, scroll and WASD always work.
function applyNavMode() {
  const leftFree = !state.measure || measureSpace;
  orbit.mouseButtons = { LEFT: state.nav === 'look' || !leftFree ? -1 : THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.ROTATE, RIGHT: THREE.MOUSE.PAN };
  planControls.mouseButtons = { LEFT: leftFree ? THREE.MOUSE.PAN : -1, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.PAN };
  document.body.classList.toggle('space-nav', state.measure && measureSpace);
}
function setNav(mode) {
  state.nav = mode;
  $('navMode').dataset.mode = mode;
  // Look mode walks with the wheel (below); orbit mode keeps the dolly towards the target.
  orbit.enableZoom = mode !== 'look';
  applyNavMode();
  syncTools();
}
let pointer = null;
const LOOK_RAD = THREE.MathUtils.degToRad(0.25), MAX_PITCH = THREE.MathUtils.degToRad(85);
const _dir = new THREE.Vector3(), _sph = new THREE.Spherical();
canvas.addEventListener('pointerdown', ev => {
  pointer = { id: ev.pointerId, x: ev.clientX, y: ev.clientY, sx: ev.clientX, sy: ev.clientY, t: performance.now(), moved: false, button: ev.button, space: measureSpace };
});
canvas.addEventListener('pointermove', ev => {
  if (!pointer) { if (state.measure) requestHover({ x: ev.clientX, y: ev.clientY, shift: ev.shiftKey, alt: ev.altKey }); return; }
  if (ev.pointerId !== pointer.id) return;
  if (Math.hypot(ev.clientX - pointer.sx, ev.clientY - pointer.sy) > 4) pointer.moved = true;
  if (pointer.button === 0 && state.nav === 'look' && pointer.moved && state.plan == null && (!state.measure || pointer.space)) {
    _dir.copy(orbit.target).sub(persp.position);
    const dist = _dir.length();
    _sph.setFromVector3(_dir);
    _sph.theta -= (ev.clientX - pointer.x) * LOOK_RAD;
    _sph.phi = THREE.MathUtils.clamp(_sph.phi + (ev.clientY - pointer.y) * LOOK_RAD, Math.PI / 2 - MAX_PITCH, Math.PI / 2 + MAX_PITCH);
    _dir.setFromSpherical(_sph).setLength(dist);
    orbit.target.copy(persp.position).add(_dir);
    persp.lookAt(orbit.target);
  }
  pointer.x = ev.clientX; pointer.y = ev.clientY;
});
canvas.addEventListener('pointerup', ev => {
  if (!pointer || ev.pointerId !== pointer.id) return;
  const p = pointer;
  pointer = null;
  if (p.button !== 0 || state.modelID === null) return;
  if (state.measure) {
    const slid = Math.hypot(ev.clientX - p.sx, ev.clientY - p.sy);
    if (!p.space && slid <= MEASURE_CLICK.px && performance.now() - p.t <= MEASURE_CLICK.ms) measureClick(p.sx, p.sy, { alt: ev.altKey, shift: ev.shiftKey });
    return;
  }
  if (!p.moved) {
    const id = pick(ev.clientX, ev.clientY);
    setSelection(id === state.sel ? null : id);
  }
});
canvas.addEventListener('pointercancel', () => { pointer = null; });
canvas.addEventListener('pointerleave', () => { measure.pointer = null; if (measure.hover) { measure.hover = null; updateMeasure(); } });
// Look mode: the wheel moves camera and target together along the ray under the cursor at a
// constant speed (about 1 m per notch), so it never stalls against the orbit target. Plan: zoom.
canvas.addEventListener('wheel', ev => {
  if (state.measure && !pointer) requestHover({ x: ev.clientX, y: ev.clientY, shift: ev.shiftKey, alt: ev.altKey });
  if (state.nav !== 'look' || state.plan != null) return;
  ev.preventDefault();
  const r = canvas.getBoundingClientRect();
  raycaster.setFromCamera(ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1), persp);
  const step = THREE.MathUtils.clamp(-ev.deltaY * (ev.deltaMode === 1 ? 0.33 : 0.01), -3, 3) * (ev.shiftKey ? 3 : 1);
  _m.copy(raycaster.ray.direction).multiplyScalar(step);
  persp.position.add(_m); orbit.target.add(_m);
  hideTutorial();
}, { passive: false });

const held = new Set(), MOVE = {
  w: [1, 0, 0], s: [-1, 0, 0], a: [0, -1, 0], d: [0, 1, 0], q: [0, 0, -1], e: [0, 0, 1],
  arrowup: [1, 0, 0], arrowdown: [-1, 0, 0], arrowleft: [0, -1, 0], arrowright: [0, 1, 0],
};
let fast = false;
const UP = new THREE.Vector3(0, 1, 0), _f = new THREE.Vector3(), _r = new THREE.Vector3(), _m = new THREE.Vector3();
function moveStep(dt) {
  if (!held.size) return;
  let f = 0, r = 0, u = 0;
  for (const k of held) { const m = MOVE[k]; if (m) { f += m[0]; r += m[1]; u += m[2]; } }
  if (!f && !r && !u) return;
  const plan = state.plan != null;
  // 3D: W/S fly where the camera looks, A/D strafe level. Plan: pan the map, W = north.
  if (plan) { _f.set(0, 0, -1); _r.set(1, 0, 0); u = 0; }
  else {
    camera.getWorldDirection(_f);
    _r.copy(_f).setY(0);
    if (_r.lengthSq() < 1e-4) _r.set(0, 1, 0).applyQuaternion(camera.quaternion).setY(0);
    _r.normalize().cross(UP).normalize();
  }
  _m.set(0, 0, 0).addScaledVector(_f, f).addScaledVector(_r, r).addScaledVector(UP, u);
  if (_m.lengthSq() === 0) return;
  _m.normalize().multiplyScalar((fast ? 3 : 1) * (plan ? currentSpan() * 0.5 : 3) * dt);
  camera.position.add(_m); controls.target.add(_m);
}
window.addEventListener('keydown', ev => {
  if (ev.target.matches?.('input, select, textarea') || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const k = ev.key.toLowerCase();
  fast = ev.shiftKey;
  if (k in MOVE) { held.add(k); hideTutorial(); ev.preventDefault(); return; }
  if (ev.key === 'Escape') {
    hideTutorial();
    if (state.measure && measure.points.length) { measure.points = []; measure.preview = null; updateMeasure(); }
    else if (state.measure) setMeasure(false);
    else setSelection(null);
  } else if (ev.key === '/') { $('search').focus(); ev.preventDefault(); }
  else if (k === 'f' && state.sel != null) focusOn(state.elements.get(state.sel));
  else if (k === 'x' && state.sel != null) removeSelected();
  else if (k === 'z') undoRemove(ev.shiftKey);
  else if (k === 'm' && state.modelID !== null) setMeasure(!state.measure);
  else if (k === 'p' && state.modelID !== null) togglePlan();
  else if (k === 'o') setNav(state.nav === 'look' ? 'orbit' : 'look');
  else if (k === 'h') homeView();
});
window.addEventListener('keyup', ev => { held.delete(ev.key.toLowerCase()); fast = ev.shiftKey; });
window.addEventListener('blur', () => { held.clear(); setSpace(false); });
function setSpace(on) {
  if (measureSpace === on) return;
  measureSpace = on;
  applyNavMode();
}
for (const type of ['keydown', 'keyup']) window.addEventListener(type, ev => {
  if (ev.target.matches?.('input, select, textarea')) return;
  if (ev.code === 'Space') { if (state.measure) ev.preventDefault(); setSpace(type === 'keydown'); }
  if ((ev.key === 'Shift' || ev.key === 'Alt') && state.measure && measure.pointer && !pointer) {
    if (ev.key === 'Alt') ev.preventDefault();
    requestHover({ ...measure.pointer, shift: ev.shiftKey, alt: ev.altKey });
  }
});

// ---------------------------------------------------------------- toolbar
$('spaces').onclick = () => { state.spaces = !state.spaces; spacesGroup.visible = state.spaces; $('spaces').classList.toggle('on', state.spaces); applyVisibility(); };
$('home').onclick = () => homeView();
$('navMode').onclick = ev => {
  const seg = ev.target.closest('span');
  setNav(seg ? (seg.classList.contains('look') ? 'look' : 'orbit') : (state.nav === 'look' ? 'orbit' : 'look'));
};
$('fov').onchange = ev => { setFov(Number(ev.target.value)); ev.target.blur(); };
$('plan').onclick = ev => { ev.currentTarget.blur(); if (state.modelID !== null) togglePlan(); };
$('measure').onclick = ev => { ev.currentTarget.blur(); if (state.modelID !== null) setMeasure(!state.measure); };
$('cut').onchange = ev => { setCut(ev.target.value === '' ? null : Number(ev.target.value), +$('cutHeight').value || DEFAULT_CUT); ev.target.blur(); };
$('cutHeight').oninput = ev => {
  if (!state.cut || ev.target.value === '' || !Number.isFinite(+ev.target.value)) return;
  state.cut = { ...state.cut, height: +ev.target.value };
  applyVisibility();
};
$('panelClose').onclick = () => setSelection(null);
function syncTools() {
  $('cut').value = state.cut ? String(state.cut.storey) : '';
  if (state.cut && document.activeElement !== $('cutHeight')) $('cutHeight').value = state.cut.height;
  $('fov').value = String(persp.fov);
  $('fov').disabled = state.plan != null;
  $('fov').title = state.plan != null ? 'Field of view applies to 3D views; leave Plan to change it' : 'Vertical field of view: wider angles show more of the room';
  $('plan').classList.toggle('on', state.plan != null);
  $('measure').classList.toggle('on', state.measure);
  const removed = state.removed.size;
  let hint = state.measure
    ? `Click two points · Space+drag ${state.plan != null ? 'pan' : state.nav} · right-drag pan · scroll ${state.plan != null ? 'zoom' : 'fly'} · Shift axis · Alt no snap · Esc clear`
    : state.plan != null ? `Plan ${storeyOf(state.plan)?.name ?? ''} · north up · drag to pan · scroll to zoom · WASD to pan · P back to 3D` : '';
  if (removed && !state.measure) hint += `${hint ? ' · ' : ''}${removed} removed for play (X): Z undo · Shift+Z restore all`;
  $('hint').textContent = hint;
  updateStats();
}
setNav('look');

// ---------------------------------------------------------------- tutorial
// One card after the first model load, until the visitor moves or presses "Got it"; remembered
// per browser (storage may be unavailable, then it simply shows again next visit).
const TUTORIAL_KEY = 'monarch.tutorialSeen';
function showTutorial() {
  try { if (localStorage.getItem(TUTORIAL_KEY)) return; } catch { /* no storage */ }
  $('tutorial').hidden = false;
}
function hideTutorial() {
  if ($('tutorial').hidden) return;
  $('tutorial').hidden = true;
  try { localStorage.setItem(TUTORIAL_KEY, '1'); } catch { /* no storage */ }
}
$('tutorialOk').onclick = () => hideTutorial();

// ---------------------------------------------------------------- search
const searchEl = $('search'), resultsEl = $('results');
let results = [], active = 0;
function runSearch() {
  const q = searchEl.value.trim().toLowerCase();
  results = [];
  if (q) {
    for (const el of state.elements.values()) {
      if (el.isSpace && !state.spaces) continue;
      if (el.name.toLowerCase().includes(q) || el.type.toLowerCase().includes(q) || el.gid.toLowerCase().includes(q) || String(el.id) === q) results.push(el);
      if (results.length >= 60) break;
    }
  }
  active = 0;
  resultsEl.hidden = !results.length;
  resultsEl.innerHTML = results.map((el, i) => `<li data-i="${i}" class="${i === active ? 'active' : ''}">${esc(el.name || '(unnamed)')}<div class="sub">${esc(el.type)} · #${el.id} · ${esc(state.storeys.find(s => s.id === el.storey)?.name ?? '—')}</div></li>`).join('');
}
function chooseResult(i) {
  const el = results[i];
  if (!el) return;
  setSelection(el.id); focusOn(el);
  resultsEl.hidden = true; searchEl.blur();
}
searchEl.addEventListener('input', runSearch);
searchEl.addEventListener('focus', runSearch);
searchEl.addEventListener('keydown', ev => {
  if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
    active = (active + (ev.key === 'ArrowDown' ? 1 : results.length - 1)) % Math.max(results.length, 1);
    [...resultsEl.children].forEach((li, i) => li.classList.toggle('active', i === active));
    ev.preventDefault();
  } else if (ev.key === 'Enter') chooseResult(active);
  else if (ev.key === 'Escape') { resultsEl.hidden = true; searchEl.blur(); }
});
resultsEl.addEventListener('pointerdown', ev => { const li = ev.target.closest('li'); if (li) { ev.preventDefault(); chooseResult(Number(li.dataset.i)); } });
document.addEventListener('pointerdown', ev => { if (!ev.target.closest('.search')) resultsEl.hidden = true; });

// ---------------------------------------------------------------- files: picker, drag & drop, example
let toastTimer;
function toast(message, kind = 'error') {
  const t = $('toast');
  t.textContent = message; t.className = kind; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, kind === 'error' ? 9000 : 4000);
}
function busy(text) { $('loading').hidden = !text; if (text) $('loadingText').textContent = text; }
async function openFile(file) {
  if (!file) return;
  busy(`Reading ${file.name}…`);
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    await new Promise(r => setTimeout(r, 30));     // let the loading pill paint before the synchronous parse
    busy(`Parsing ${file.name} (${(bytes.length / 1048576).toFixed(1)} MB)…`);
    await new Promise(r => setTimeout(r, 30));
    await loadBuffer(bytes, file.name);
  } catch (e) {
    console.warn(e);
    toast(`Could not open ${file.name}: ${e.message || e}`);
  } finally { busy(null); }
}
$('file').addEventListener('change', ev => { openFile(ev.target.files[0]); ev.target.value = ''; });
async function loadExample() {
  busy('Fetching the example house…');
  try {
    const r = await fetch(EXAMPLE_URL);
    if (!r.ok) throw new Error(`HTTP ${r.status} for ${EXAMPLE_URL}`);
    const bytes = new Uint8Array(await r.arrayBuffer());
    busy(`Parsing casa_monarch.ifc (${(bytes.length / 1048576).toFixed(1)} MB)…`);
    await new Promise(r => setTimeout(r, 30));
    await loadBuffer(bytes, 'casa_monarch.ifc', new URL(EXAMPLE_URL, location.href).href);
  } catch (e) {
    console.warn(e);
    toast(`Could not load the example house: ${e.message || e}`);
  } finally { busy(null); }
}
$('example').onclick = loadExample;
$('exampleHero').onclick = loadExample;
let dragDepth = 0;
window.addEventListener('dragenter', ev => { ev.preventDefault(); if (++dragDepth === 1) $('drop').hidden = false; });
window.addEventListener('dragover', ev => { ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy'; });
window.addEventListener('dragleave', ev => { ev.preventDefault(); if (--dragDepth <= 0) { dragDepth = 0; $('drop').hidden = true; } });
window.addEventListener('drop', ev => {
  ev.preventDefault(); dragDepth = 0; $('drop').hidden = true;
  const file = [...ev.dataTransfer.files].find(f => /\.ifc$/i.test(f.name)) || ev.dataTransfer.files[0];
  if (!file) return toast('Nothing to open: drop an .ifc file.');
  openFile(file);
});

// ---------------------------------------------------------------- stats + frame loop
function updateStats() {
  const n = [...state.elements.values()].filter(e => !e.isSpace).length - [...state.removed].filter(id => !state.elements.get(id)?.isSpace).length;
  $('stElements').innerHTML = state.modelID === null ? '—' : `${n}${state.removed.size ? `<small>${state.removed.size} removed</small>` : ''}`;
  $('stTriangles').textContent = state.modelID === null ? '—' : state.tris >= 1e6 ? `${(state.tris / 1e6).toFixed(2)} M` : state.tris >= 1e3 ? `${(state.tris / 1e3).toFixed(1)} k` : String(state.tris);
  $('stParse').innerHTML = state.modelID === null ? '—' : `${(state.parseMs / 1000).toFixed(2)}<small>s</small>`;
}
function resize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * renderer.getPixelRatio()) || canvas.height !== Math.round(h * renderer.getPixelRatio())) {
    renderer.setSize(w, h, false);
    persp.aspect = w / h; persp.updateProjectionMatrix();
    setOrthoSpan(currentSpan());
  }
}
let last = performance.now(), frames = 0;
renderer.setAnimationLoop(now => {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  resize();
  moveStep(dt);
  controls.update();
  renderer.render(scene, camera);
  drawMeasureOverlay();
  frames++;
});

// Test hooks (tests drive the page over the DevTools protocol).
window.__viewer = {
  api, scene, root, persp, ortho, orbit, planControls, clipPlane, renderer, state, measure, MEASURE_CLICK, loadBuffer, setSelection, focusOn, homeView, setNav,
  showTutorial, hideTutorial, removeSelected, undoRemove, setFov, setCut, enterPlan, exitPlan, togglePlan, setMeasure, measureClick, snapAt, pickHit, toIfc,
  get camera() { return camera; }, get controls() { return controls; },
  get drawCalls() { return renderer.info.render.calls; }, get frames() { return frames; },
};
apiReady.then(() => { window.__viewerReady = true; });
