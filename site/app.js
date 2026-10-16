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

const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 3000);
camera.position.set(14, 10, 16);
const orbit = new OrbitControls(camera, canvas);
orbit.enableDamping = true;
orbit.dampingFactor = 0.12;
orbit.screenSpacePanning = true;
orbit.zoomToCursor = true;
orbit.target.set(0, 1, 0);

// web-ifc already returns y-up geometry (IFC x, y, z -> three x, z, -y), so the root stays unrotated.
const root = new THREE.Group();
scene.add(root);
const spacesGroup = new THREE.Group();
spacesGroup.visible = false;
root.add(spacesGroup);
const selectBox = new THREE.Box3Helper(new THREE.Box3(), 0x3ea6ff);
selectBox.visible = false;
scene.add(selectBox);

// ---------------------------------------------------------------- web-ifc
const api = new WebIFC.IfcAPI();
api.SetWasmPath(new URL('./vendor/web-ifc/', import.meta.url).href, true);
let apiReady = api.Init(undefined, true);     // single-thread wasm: GitHub Pages is not cross-origin isolated
apiReady.catch(e => toast(`web-ifc failed to initialise: ${e.message || e}`));

// ---------------------------------------------------------------- state
const state = {
  modelID: null, fileName: '', elements: new Map(), storeys: [], storeyVisible: new Map(), sel: null,
  nav: 'orbit', plaques: true, spaces: false, tris: 0, parseMs: 0, labels: [], materials: new Map(),
};
function materialFor(materials, c) {
  const key = `${c.x.toFixed(3)},${c.y.toFixed(3)},${c.z.toFixed(3)},${c.w.toFixed(3)}`;
  let m = materials.get(key);
  if (!m) {
    m = new THREE.MeshLambertMaterial({ side: THREE.DoubleSide });
    m.color.setRGB(c.x, c.y, c.z, THREE.SRGBColorSpace);
    if (c.w < 1) { m.transparent = true; m.opacity = Math.max(0.12, c.w); m.depthWrite = false; }
    materials.set(key, m);
  }
  return m;
}
const SPACE_COLOUR = { x: 0.3, y: 0.6, z: 1, w: 0.18 };
const val = v => (v && typeof v === 'object' && 'value' in v) ? v.value : v;

function disposeGroup(g) {
  g.traverse(o => { if (o.isMesh) o.geometry.dispose(); });
  g.parent?.remove(g);
}
function clearModel() {
  if (state.modelID !== null) { try { api.CloseModel(state.modelID); } catch { /* already gone */ } }
  for (const g of [...root.children]) if (g !== spacesGroup) disposeGroup(g);
  for (const g of [...spacesGroup.children]) disposeGroup(g);
  for (const m of state.materials?.values() || []) m.dispose();
  for (const l of state.labels) l.el.remove();
  Object.assign(state, { modelID: null, elements: new Map(), materials: new Map(), storeys: [], storeyVisible: new Map(), sel: null, tris: 0, labels: [] });
  selectBox.visible = false;
  $('panel').hidden = true;
  $('storeys').replaceChildren();
  $('storeyGroup').hidden = true;
}

// ---------------------------------------------------------------- loading
// Parse into a fresh model first; the current model is only replaced once the new one succeeded,
// so a broken drop leaves the view as it was.
async function loadBuffer(bytes, name) {
  await apiReady;
  const head = new TextDecoder().decode(bytes.subarray(0, 256));
  if (!/ISO-10303-21/i.test(head)) throw new Error('not an IFC file (no ISO-10303-21 STEP header).');
  const t0 = performance.now();
  let modelID;
  try { modelID = api.OpenModel(bytes, { COORDINATE_TO_ORIGIN: false }); } catch (e) { throw new Error(`web-ifc could not parse it: ${e.message || e}`); }
  const model = { modelID, elements: new Map(), materials: new Map(), root: new THREE.Group(), spaces: new THREE.Group(), tris: 0 };
  const mat = new THREE.Matrix4();
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
      mat.fromArray(pg.flatTransformation);
      geom.applyMatrix4(mat);
      const c = isSpace ? SPACE_COLOUR : pg.color;
      const key = `${c.x.toFixed(3)},${c.y.toFixed(3)},${c.z.toFixed(3)},${c.w.toFixed(3)}`;
      if (!byColour.has(key)) byColour.set(key, { colour: c, list: [] });
      byColour.get(key).list.push(geom);
      model.tris += idx.length / 3;
    }
    if (!byColour.size) return;
    const group = new THREE.Group();
    group.userData.expressID = id;
    for (const { colour, list } of byColour.values()) {
      const merged = list.length === 1 ? list[0] : mergeGeometries(list, false);
      if (list.length > 1) list.forEach(g => g.dispose());
      const mesh = new THREE.Mesh(merged, materialFor(model.materials, colour));
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
    for (const m of model.materials.values()) m.dispose();
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
  buildPlaques(modelID);
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
  const storeyIds = api.GetLineIDsWithType(modelID, WebIFC.IFCBUILDINGSTOREY);
  const storeys = [];
  for (let i = 0; i < storeyIds.size(); i++) {
    const id = storeyIds.get(i), line = api.GetLine(modelID, id);
    storeys.push({ id, name: val(line?.Name) || `Storey ${id}`, elevation: Number(val(line?.Elevation)) || 0, count: 0 });
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
  applyVisibility();
}
function applyVisibility() {
  for (const el of state.elements.values()) el.group.visible = state.storeyVisible.get(el.storey) !== false;
  for (const b of $('storeys').children) b.classList.toggle('on', state.storeyVisible.get(b.dataset.storey === 'null' ? null : Number(b.dataset.storey)) !== false);
  if (state.sel != null && !state.elements.get(state.sel)?.group.visible) setSelection(null);
}

// Plaques: one floating label per element with a Pset_Gallery.Title, found through
// IfcRelDefinesByProperties (cheap: no per-element property query).
function buildPlaques(modelID) {
  const ids = api.GetLineIDsWithType(modelID, WebIFC.IFCRELDEFINESBYPROPERTIES);
  const labels = [];
  for (let i = 0; i < ids.size(); i++) {
    const rel = api.GetLine(modelID, ids.get(i));
    const psetId = val(rel?.RelatingPropertyDefinition);
    if (psetId == null) continue;
    const pset = api.GetLine(modelID, psetId);
    if (val(pset?.Name) !== 'Pset_Gallery') continue;
    let title = '';
    for (const h of pset.HasProperties || []) {
      const p = api.GetLine(modelID, val(h));
      if (val(p?.Name) === 'Title') title = String(val(p.NominalValue) ?? '');
    }
    if (!title) continue;
    for (const h of rel.RelatedObjects || []) {
      const el = state.elements.get(val(h));
      if (!el || el.isSpace) continue;
      const div = document.createElement('div');
      div.className = 'plaque';
      div.innerHTML = `<small>Plaque</small>${esc(title)}`;
      div.onclick = () => { setSelection(el.id); };
      $('labels').appendChild(div);
      const top = new THREE.Vector3(); el.bbox.getCenter(top); top.y = el.bbox.max.y + 0.15;
      labels.push({ el, div, pos: top, title });
    }
  }
  state.labels = labels.map(l => ({ el: l.div, element: l.el, pos: l.pos, title: l.title }));
}
const _p = new THREE.Vector3();
function updateLabels() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  for (const l of state.labels) {
    const show = state.plaques && l.element.group.visible;
    if (!show) { l.el.hidden = true; continue; }
    // Labels are for reading inside the room: beyond 14 m (or with the exhibit selected,
    // at any distance) they would only pile up over the facade seen from the garden.
    const far = camera.position.distanceTo(l.pos) > 14 && l.element.id !== state.sel;
    _p.copy(l.pos).project(camera);
    if (far || _p.z > 1 || _p.z < -1) { l.el.hidden = true; continue; }
    l.el.hidden = false;
    l.el.style.transform = `translate(-50%, -100%) translate(${((_p.x + 1) / 2 * w).toFixed(1)}px, ${((1 - _p.y) / 2 * h - 10).toFixed(1)}px)`;
    l.el.classList.toggle('sel', l.element.id === state.sel);
  }
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
  if (state.sel == null) { panel.hidden = true; $('stSel').textContent = '—'; updateLabels(); return; }
  const el = state.elements.get(state.sel);
  el.group.traverse(o => {
    if (!o.isMesh) return;
    const m = o.material.clone();
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
    <div class="actions"><button id="focusBtn">Focus <kbd>F</kbd></button></div>
    <table><tr><th>Storey</th><td>${esc(storey?.name ?? '—')}</td></tr>
    <tr><th>Size (x · y · z)</th><td class="mono">${size.x.toFixed(2)} · ${size.z.toFixed(2)} · ${size.y.toFixed(2)} m</td></tr></table>
    <p class="empty" id="psetWait">Loading property sets…</p>`;
  $('focusBtn').onclick = () => focusOn(el);
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
  updateLabels();
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
function pick(x, y) {
  const r = canvas.getBoundingClientRect();
  raycaster.setFromCamera(new THREE.Vector2(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1), camera);
  const hits = raycaster.intersectObjects(root.children, true);
  for (const h of hits) {
    const el = state.elements.get(h.object.userData.expressID);
    if (el && el.group.visible && (state.spaces || !el.isSpace)) return el.id;
  }
  return null;
}

// ---------------------------------------------------------------- camera: orbit / look, WASD, fit
const modelBox = new THREE.Box3();
function homeView() {
  // Frame the building (elements inside a storey), not the whole site, so a house with a
  // large terrain still fills the view on first load. Fall back to everything.
  modelBox.makeEmpty();
  for (const el of state.elements.values()) if (el.storey != null && !el.isSpace) modelBox.union(el.bbox);
  if (modelBox.isEmpty()) modelBox.setFromObject(root);
  if (modelBox.isEmpty()) return;
  const c = new THREE.Vector3(), s = new THREE.Vector3();
  modelBox.getCenter(c); modelBox.getSize(s);
  const d = Math.max(s.x, s.y, s.z, 1) * 1.4;
  camera.position.set(c.x - d * 0.9, c.y + d * 0.7, c.z + d * 1.0);
  orbit.target.copy(c);
  orbit.update();
}
function focusOn(el) {
  const c = new THREE.Vector3(), s = new THREE.Vector3();
  el.bbox.getCenter(c); el.bbox.getSize(s);
  const d = Math.max(s.length() * 1.6, 1.2);
  const dir = camera.position.clone().sub(orbit.target).normalize();
  if (dir.lengthSq() < 1e-6) dir.set(-0.6, 0.5, 0.7);
  camera.position.copy(c).addScaledVector(dir, d);
  orbit.target.copy(c);
  orbit.update();
}
function setNav(mode) {
  state.nav = mode;
  $('navMode').dataset.mode = mode;
  orbit.mouseButtons = { LEFT: mode === 'look' ? -1 : THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.ROTATE, RIGHT: THREE.MOUSE.PAN };
}
let pointer = null;
const LOOK_RAD = THREE.MathUtils.degToRad(0.25), MAX_PITCH = THREE.MathUtils.degToRad(85);
const _dir = new THREE.Vector3(), _sph = new THREE.Spherical();
canvas.addEventListener('pointerdown', ev => {
  pointer = { id: ev.pointerId, x: ev.clientX, y: ev.clientY, sx: ev.clientX, sy: ev.clientY, moved: false, button: ev.button };
});
canvas.addEventListener('pointermove', ev => {
  if (!pointer || ev.pointerId !== pointer.id) return;
  if (Math.hypot(ev.clientX - pointer.sx, ev.clientY - pointer.sy) > 4) pointer.moved = true;
  if (pointer.button === 0 && state.nav === 'look' && pointer.moved) {
    _dir.copy(orbit.target).sub(camera.position);
    const dist = _dir.length();
    _sph.setFromVector3(_dir);
    _sph.theta -= (ev.clientX - pointer.x) * LOOK_RAD;
    _sph.phi = THREE.MathUtils.clamp(_sph.phi + (ev.clientY - pointer.y) * LOOK_RAD, Math.PI / 2 - MAX_PITCH, Math.PI / 2 + MAX_PITCH);
    _dir.setFromSpherical(_sph).setLength(dist);
    orbit.target.copy(camera.position).add(_dir);
    camera.lookAt(orbit.target);
  }
  pointer.x = ev.clientX; pointer.y = ev.clientY;
});
canvas.addEventListener('pointerup', ev => {
  if (!pointer || ev.pointerId !== pointer.id) return;
  if (!pointer.moved && pointer.button === 0 && state.modelID !== null) {
    const id = pick(ev.clientX, ev.clientY);
    setSelection(id === state.sel ? null : id);
  }
  pointer = null;
});
canvas.addEventListener('pointercancel', () => { pointer = null; });

const held = new Set(), MOVE = { w: [1, 0, 0], s: [-1, 0, 0], a: [0, -1, 0], d: [0, 1, 0], q: [0, 0, -1], e: [0, 0, 1] };
let fast = false;
const UP = new THREE.Vector3(0, 1, 0), _f = new THREE.Vector3(), _r = new THREE.Vector3(), _m = new THREE.Vector3();
function moveStep(dt) {
  if (!held.size) return;
  let f = 0, r = 0, u = 0;
  for (const k of held) { const m = MOVE[k]; if (m) { f += m[0]; r += m[1]; u += m[2]; } }
  if (!f && !r && !u) return;
  camera.getWorldDirection(_f);
  _r.copy(_f).setY(0);
  if (_r.lengthSq() < 1e-4) _r.set(0, 1, 0).applyQuaternion(camera.quaternion).setY(0);
  _r.normalize().cross(UP).normalize();
  _m.set(0, 0, 0).addScaledVector(_f, f).addScaledVector(_r, r).addScaledVector(UP, u);
  if (_m.lengthSq() === 0) return;
  _m.normalize().multiplyScalar((fast ? 9 : 3) * dt);
  camera.position.add(_m); orbit.target.add(_m);
}
window.addEventListener('keydown', ev => {
  if (ev.target.matches?.('input, select, textarea') || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const k = ev.key.toLowerCase();
  fast = ev.shiftKey;
  if (k in MOVE) { held.add(k); ev.preventDefault(); return; }
  if (ev.key === 'Escape') setSelection(null);
  else if (ev.key === '/') { $('search').focus(); ev.preventDefault(); }
  else if (k === 'f' && state.sel != null) focusOn(state.elements.get(state.sel));
  else if (k === 'o') setNav(state.nav === 'look' ? 'orbit' : 'look');
  else if (k === 'l') togglePlaques();
  else if (k === 'h') homeView();
});
window.addEventListener('keyup', ev => { held.delete(ev.key.toLowerCase()); fast = ev.shiftKey; });
window.addEventListener('blur', () => held.clear());

// ---------------------------------------------------------------- toolbar
function togglePlaques(on = !state.plaques) {
  state.plaques = on;
  $('plaques').classList.toggle('on', on);
  updateLabels();
}
$('plaques').onclick = () => togglePlaques();
$('spaces').onclick = () => { state.spaces = !state.spaces; spacesGroup.visible = state.spaces; $('spaces').classList.toggle('on', state.spaces); };
$('home').onclick = () => homeView();
$('navMode').onclick = ev => {
  const seg = ev.target.closest('span');
  setNav(seg ? (seg.classList.contains('look') ? 'look' : 'orbit') : (state.nav === 'look' ? 'orbit' : 'look'));
};
$('panelClose').onclick = () => setSelection(null);
setNav('orbit');

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
    await loadBuffer(bytes, 'casa_monarch.ifc');
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
  $('stElements').textContent = state.modelID === null ? '—' : String([...state.elements.values()].filter(e => !e.isSpace).length);
  $('stTriangles').textContent = state.modelID === null ? '—' : state.tris >= 1e6 ? `${(state.tris / 1e6).toFixed(2)} M` : state.tris >= 1e3 ? `${(state.tris / 1e3).toFixed(1)} k` : String(state.tris);
  $('stParse').innerHTML = state.modelID === null ? '—' : `${(state.parseMs / 1000).toFixed(2)}<small>s</small>`;
}
function resize() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * renderer.getPixelRatio()) || canvas.height !== Math.round(h * renderer.getPixelRatio())) {
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  }
}
let last = performance.now(), frames = 0;
renderer.setAnimationLoop(now => {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  resize();
  moveStep(dt);
  orbit.update();
  renderer.render(scene, camera);
  if (state.labels.length) updateLabels();
  frames++;
});

// Test hooks (tests drive the page over the DevTools protocol).
window.__viewer = {
  api, scene, root, camera, orbit, state, loadBuffer, setSelection, focusOn, homeView, setNav, togglePlaques,
  get drawCalls() { return renderer.info.render.calls; }, get frames() { return frames; },
};
apiReady.then(() => { window.__viewerReady = true; });
