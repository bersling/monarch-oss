import { esc, linkify } from './document_links.js';
/* Monarch browser viewer (three.js, served by src/serve.py).

Storeys are data: generated/elements.json carries house.storeys (codes in order, bottom to
top, with elevations) from data/house.json; the pseudo storey SITE (everything contained in
the IfcSite) comes last. The examples below use the codes of the model home, GF and UF.

URL hash state. All coordinates are IFC: x east, y north, z up, metres. Order does not
matter, unknown keys are ignored, lists may use , ; or spaces, storeys are case-insensitive.

  cam=x,y,z          perspective camera position
  fov=45             vertical field of view preset: 30, 45 (default), 60 or 75 degrees
  target=x,y,z       orbit target (plan mode: x,y = centre of the plan)
  storeys=GF,UF      visible storeys (default all, SITE included; "none" allowed)
  hide=SITE          storeys to hide, applied after storeys=
  cut=GF@1.2         show GF and everything below, clip everything above GF floor + 1.2 m
                     (cut=GF means @1.2; cut=off disables)
  plan=GF            top-down orthographic view, north up; implies cut=GF@1.2 unless cut= given
  span=18            plan mode: metres visible vertically (default: fit the storey)
  sel=KEY            select an element (product panel + highlight)
  Keys while an element is selected: F frame, X remove it for this session (not saved; Z undo,
  Shift+Z restore all, reload restores everything).
  frame=KEY          frame an element from an oblique south-east angle, cutting storeys above
                     (cut=<storey>@2.2 unless cut= given). KEY may be a prefix: frame=GAL_
                     frames every element whose key starts with GAL_. Overrides cam/target.
  edges=0            hide edge lines        spaces=1   show IfcSpace volumes
  plaques=0          hide the floating title labels of elements with Pset_Gallery (default: shown
                     when the model has any)
  look=true          true-colour lighting (flat, no shadows, no bloom); default look=cinema (T toggles)

Snapshot mode (for AI verification, driven by src/snap_web.py):
  snap=NAME&w=1600&h=1000   render the hash view at w x h, POST the PNG to /api/snap?name=NAME
  snaps=[{...},...]         URL-encoded JSON array of views, each {name, cam, target, storeys, hide,
                            cut, plan, span, sel, frame, edges}; values as above, arrays allowed
                            (e.g. "cam":[1,2,3]). Top-level hash keys act as defaults for every view.
  Sets window.__monarchReady = true once the model is loaded and rendered, and
  window.__monarchSnapDone = true after all PNGs are posted (window.__monarchSnapError on failure).

Examples:
  #cam=-9,-12,9&target=4,3,2
  #plan=GF
  #frame=GAL_&sel=GAL_SHELF
*/
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

// Storeys of the loaded model (setStoreys at load): FLOORS = building storey codes bottom to
// top, STOREYS = FLOORS + the pseudo storey SITE, ELEV = floor elevation per code (IFC z),
// RANK = index in FLOORS (SITE: -1). Empty until the first elements.json arrives.
let STOREYS = ['SITE'], FLOORS = [], ELEV = {}, RANK = { SITE: -1 };
const DEFAULT_CUT = 1.2, FRAME_CUT = 2.2, EDGE_ANGLE = 30, EDGE_MAX_TRIS = 25000;
const FOV_PRESETS = [30, 45, 60, 75], DEFAULT_FOV = 45;
const storeyOf = s => (s in ELEV ? s : 'SITE');          // anything not a building storey -> SITE
function setStoreys(list) {
  FLOORS = list.map(s => String(s.code));
  STOREYS = [...FLOORS, 'SITE'];
  ELEV = Object.fromEntries(list.map(s => [String(s.code), Number.isFinite(+s.elevation) ? +s.elevation : 0]));
  RANK = { SITE: -1, ...Object.fromEntries(FLOORS.map((c, i) => [c, i])) };
}
/** Default storey for plan and cut: the one at elevation 0, else the lowest. */
const groundStorey = () => FLOORS.find(c => ELEV[c] === 0) ?? FLOORS[0] ?? null;
/** Storey code matching s case-insensitively, or null. */
function storeyCode(s) {
  const u = String(s).trim().toUpperCase();
  return STOREYS.find(c => c.toUpperCase() === u) ?? null;
}

// IFC (x, y, z) <-> three.js (x, z, -y)
const toThree = (x, y, z) => new THREE.Vector3(x, z, -y);
const toIfc = v => [v.x, -v.z, v.y];
const fmt = (a, d = 2) => a.map(n => (+n).toFixed(d)).join(', ');

// ---------------------------------------------------------------- hash parsing
function parseHashString(str) {
  const out = {};
  for (const part of str.replace(/^#/, '').split('&')) {
    if (!part.trim()) continue;
    const i = part.indexOf('=');
    const key = (i < 0 ? part : part.slice(0, i)).trim().toLowerCase();
    let value = i < 0 ? '' : part.slice(i + 1);
    try { value = decodeURIComponent(value); } catch { /* keep raw */ }
    out[key] = value;
  }
  return out;
}
function numbers(v) {
  if (v == null || v === '') return null;
  const list = (Array.isArray(v) ? v : String(v).split(/[\s,;]+/)).filter(s => s !== '').map(Number);
  return list.every(Number.isFinite) ? list : null;
}
function parseStoreys(v) {
  const list = Array.isArray(v) ? v : String(v).split(/[\s,;+|]+/);
  const out = new Set();
  for (let s of list) {
    const code = String(s).trim().toUpperCase() === 'ALL' ? 'ALL' : storeyCode(s);
    if (code === 'ALL') STOREYS.forEach(x => out.add(x));
    else if (code) out.add(code);
  }
  return out;
}
function parseCut(v) {
  if (v && typeof v === 'object') v = `${v.storey}@${v.height ?? DEFAULT_CUT}`;
  const m = String(v ?? '').trim().match(/^([^@:,\s]+)\s*(?:[@:,\s]\s*([-+]?\d*\.?\d+))?/);
  const storey = m && storeyCode(m[1]);
  return storey && FLOORS.includes(storey) ? { storey, height: m[2] != null ? +m[2] : DEFAULT_CUT } : null;   // null = off
}
const flag = v => !/^(0|false|off|no)$/i.test(String(v).trim());

/** Raw {key: string|array} -> normalised view. Only keys present in raw are set. */
function parseView(raw) {
  const v = {};
  const cam = numbers(raw.cam), target = numbers(raw.target);
  if (cam && cam.length >= 3) v.cam = cam.slice(0, 3);
  if (cam && cam.length >= 6 && !target) v.target = cam.slice(3, 6);  // cam=cx,cy,cz,tx,ty,tz
  if (target && target.length >= 2) v.target = [target[0], target[1], target[2] ?? null];
  if (raw.storeys != null) v.storeys = raw.storeys === 'none' ? new Set() : parseStoreys(raw.storeys);
  if (raw.hide != null) v.hide = parseStoreys(raw.hide);
  if (raw.cut != null) v.cut = parseCut(raw.cut);
  if (raw.plan != null && raw.plan !== '') {
    const p = [...parseStoreys(raw.plan)].find(s => FLOORS.includes(s));
    if (p) v.plan = p;
  }
  if (FOV_PRESETS.includes(Number(raw.fov))) v.fov = Number(raw.fov);
  const span = numbers(raw.span);
  if (span && span[0] > 0) v.span = span[0];
  if (raw.sel != null) v.sel = String(raw.sel).trim() || null;
  if (raw.frame != null && String(raw.frame).trim()) v.frame = String(raw.frame).trim();
  if (raw.edges != null) v.edges = flag(raw.edges);
  if (raw.spaces != null) v.spaces = flag(raw.spaces);
  if (raw.plaques != null) v.plaques = flag(raw.plaques);
  if (raw.look != null) v.look = /^(true|tc|truecolou?r|1)$/i.test(String(raw.look).trim()) ? 'true' : 'cinema';
  return v;
}

// ---------------------------------------------------------------- scene setup
const HASH = parseHashString(location.hash);
const SNAP = 'snap' in HASH || 'snaps' in HASH;
const canvas = document.getElementById('view');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, preserveDrawingBuffer: SNAP, powerPreference: 'high-performance' });
const PIXEL_RATIO = SNAP ? 1 : Math.min(window.devicePixelRatio, 2);
renderer.setPixelRatio(PIXEL_RATIO);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
renderer.shadowMap.autoUpdate = false;                  // the sun and the house are static: redraw the
const invalidateShadows = () => { if (sun.shadow.intensity > 0) renderer.shadowMap.needsUpdate = true; };   // shadow map only on scene changes
const scene = new THREE.Scene();

// Two looks. Both keep lit surfaces close to their authored sRGB colour (see LOOKS notes and
// __monarch.colourCheck()): "cinema" adds sun shadows, soft image-based light and a faint bloom on
// the selection; "true" is the flat, even lighting of the original viewer, no shadows, no bloom,
// no tone mapping, for judging finishes.
// Calibration (three.js lights: irradiance / pi = the factor on the base colour): an up-facing
// surface in sun should come out at ~1.0 x its authored colour, walls 0.75..1.0.
// Tone mapping is Khronos PBR Neutral without its toe: identity below 0.76, a hue-preserving
// shoulder above, so mid-tones (oak, paint) keep their exact hue and saturation. The stock
// Neutral toe subtracts up to 0.04 from every channel, which darkens and saturates mid-tones.
THREE.ShaderChunk.tonemapping_pars_fragment = THREE.ShaderChunk.tonemapping_pars_fragment.replace(
  'float offset = x < 0.08 ? x - 6.25 * x * x : 0.04;', 'float offset = 0.0;');
const LOOKS = {
  cinema: { toneMapping: THREE.NeutralToneMapping, exposure: 1.0, hemi: 1.1, hemiGround: 0xf2eee6, sun: 1.0, fill: 0.0,
    env: 0.3, shadows: true, bloom: false },   // bloom off: stray NaN/inf pixels bloomed into a white haze
  true: { toneMapping: THREE.NoToneMapping, exposure: 1.0, hemi: 2.5, hemiGround: 0xffffff, sun: 0.8, fill: 0.0,
    env: 0, shadows: false, bloom: false },
};
const hemi = new THREE.HemisphereLight(0xffffff, 0x9a9282, 2.2);
scene.add(hemi);
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
const SUN_DIR = new THREE.Vector3(0.55, 0.75, 0.7).normalize();      // from south-east, high (three coords): the
                                                                      // default south-west views get a lit and a shaded side
sun.position.copy(SUN_DIR);
sun.shadow.mapSize.set(4096, 4096);
// Normal offset (needs the vertex normals computed at load; shading stays flat) removes the
// checkerboard "shadow acne" on walls lit at grazing angles; a small depth bias for the rest.
sun.shadow.bias = -0.0002;
sun.shadow.normalBias = 0.04;
sun.shadow.radius = 2.5;
sun.castShadow = true;
scene.add(sun, sun.target);
const fill = new THREE.DirectionalLight(0xffffff, 0.5);
fill.position.set(0.6, 0.5, -0.8);
scene.add(fill);
const pmrem = new THREE.PMREMGenerator(renderer);
const ENV_MAP = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
pmrem.dispose();

/** Fit the sun's shadow camera around a box [x0,y0,z0,x1,y1,z1] (IFC). */
function fitSun(b) {
  const c = toThree((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2);
  const r = Math.hypot(b[3] - b[0], b[4] - b[1], b[5] - b[2]) / 2 + 10;   // + the house's shadow on the ground
  sun.target.position.copy(c);
  sun.position.copy(c).addScaledVector(SUN_DIR, r + 5);
  const cam = sun.shadow.camera;
  cam.left = -r; cam.right = r; cam.top = r; cam.bottom = -r; cam.near = 1; cam.far = 2 * r + 10;
  cam.updateProjectionMatrix();
  sun.target.updateMatrixWorld();
  invalidateShadows();
}

// Sky: a full-screen gradient by view direction (deep navy zenith, a faint blue horizon glow,
// darker below). Drawn first, ignores depth and clipping, costs one triangle.
const SKY = new THREE.Mesh(new THREE.BufferGeometry().setAttribute('position',
  new THREE.Float32BufferAttribute([-1, -1, 0, 3, -1, 0, -1, 3, 0], 3)), new THREE.ShaderMaterial({
  uniforms: { uInv: { value: new THREE.Matrix4() }, uZenith: { value: new THREE.Color('#070a12') },
    uHorizon: { value: new THREE.Color('#18223a') }, uBelow: { value: new THREE.Color('#06080d') },
    uGlow: { value: new THREE.Color('#2a3a5c') }, uSun: { value: SUN_DIR } },
  vertexShader: 'varying vec2 vNdc; void main() { vNdc = position.xy; gl_Position = vec4(position.xy, 1.0, 1.0); }',
  fragmentShader: `uniform mat4 uInv; uniform vec3 uZenith, uHorizon, uBelow, uGlow, uSun; varying vec2 vNdc;
    void main() {
      vec4 a = uInv * vec4(vNdc, -1.0, 1.0), b = uInv * vec4(vNdc, 1.0, 1.0);
      vec3 d = normalize(b.xyz / b.w - a.xyz / a.w);
      float h = d.y;
      vec3 c = h > 0.0 ? mix(uHorizon, uZenith, pow(clamp(h, 0.0, 1.0), 0.45)) : mix(uHorizon, uBelow, pow(clamp(-h * 6.0, 0.0, 1.0), 0.6));
      c += uGlow * 0.5 * pow(max(dot(d, uSun), 0.0), 6.0) * smoothstep(-0.05, 0.25, h);
      gl_FragColor = vec4(c, 1.0);
    }`,
  depthTest: false, depthWrite: false,
}));
SKY.frustumCulled = false;
SKY.renderOrder = -1000;
SKY.raycast = () => {};
SKY.onBeforeRender = (r, s, cam) => { SKY.material.uniforms.uInv.value.copy(cam.matrixWorld).multiply(cam.projectionMatrixInverse); };
scene.add(SKY);

// Ground: a dark disc under the lowest visible element that catches the house's shadow and fades
// out into the sky, like a studio floor.
const GROUND = (() => {
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const g = c.getContext('2d'), gr = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  [[0, 1], [0.2, 0.92], [0.45, 0.5], [0.75, 0.12], [1, 0]].forEach(([t, a]) => gr.addColorStop(t, `rgba(255,255,255,${a})`));
  g.fillStyle = gr; g.fillRect(0, 0, 256, 256);
  const alpha = new THREE.CanvasTexture(c);
  const m = new THREE.Mesh(new THREE.CircleGeometry(1, 96), new THREE.MeshStandardMaterial({
    color: 0x0e1422, roughness: 1, metalness: 0, alphaMap: alpha, transparent: true, depthWrite: false }));
  m.rotation.x = -Math.PI / 2;
  m.receiveShadow = true;
  m.renderOrder = -1;
  m.raycast = () => {};
  return m;
})();
scene.add(GROUND);

// Post: HDR scene -> faint bloom (threshold above any lit surface, so only the selection glows)
// -> tone mapping + sRGB. 4x MSAA on the half-float target.
const composer = new EffectComposer(renderer, new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4 }));
const renderPass = new RenderPass(scene, null);
// Threshold far above any lit surface: a sunlit wall or a sun glint on glass reached 1.6 and bloomed
// into a white haze. Only the selection box (HDR x8) is bright enough to glow.
const bloomPass = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.35, 0.35, 5.0);
composer.addPass(renderPass);
composer.addPass(bloomPass);
composer.addPass(new OutputPass());
let look = 'cinema';
function setLook(name) {
  look = LOOKS[name] ? name : 'cinema';
  const L = LOOKS[look];
  renderer.toneMapping = L.toneMapping;
  renderer.toneMappingExposure = L.exposure;
  hemi.intensity = L.hemi; hemi.groundColor.set(L.hemiGround);
  sun.intensity = L.sun; fill.intensity = L.fill; fill.visible = L.fill > 0;
  syncShadows();
  scene.environment = ENV_MAP;
  scene.environmentIntensity = L.env;
  bloomPass.enabled = L.bloom;
  document.body.classList.toggle('truecolour', look === 'true');
  invalidateShadows();
  needsRender = true;
}
// Plans are diagrams for judging floors: no sun stripes across them. The light keeps castShadow
// and the environment map in every mode (fading via intensities), so switching look or plan never
// recompiles the model's ~400 shader programs.
function syncShadows() {
  const on = LOOKS[look].shadows && !state.plan;
  sun.shadow.intensity = on ? 1 : 0;
}
function draw() {
  SKY.visible = !state.plan;                               // plan: flat navy (clear colour)
  renderPass.camera = camera;
  composer.render();
}
renderer.setClearColor(0x080b12, 1);

const persp = new THREE.PerspectiveCamera(DEFAULT_FOV, 1, 0.05, 3000);
const ortho = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 400);
ortho.up.set(0, 0, -1);                                  // IFC north (+y) = three -z = screen up
let camera = persp;
const orbit = new OrbitControls(persp, canvas);
orbit.enableDamping = true;
orbit.dampingFactor = 0.12;
orbit.screenSpacePanning = true;
orbit.zoomToCursor = true;
orbit.enableZoom = false;              // own constant-speed wheel zoom below (OrbitControls brakes near the target)
const planControls = new OrbitControls(ortho, canvas);
planControls.enableRotate = false;
planControls.screenSpacePanning = true;
planControls.zoomToCursor = true;
planControls.enabled = false;
// plan: every drag pans (mouseButtons set in applyNavMode)
let controls = orbit;

// ---------------------------------------------------------------- look around (default) / orbit
// Look (SketchUp's eye tool): left-drag turns the camera in place, yaw about world up, pitch
// clamped to +-85 deg, 0.25 deg per pixel, no damping. The orbit target stays straight ahead at
// the same distance, so zoom, WASD and a later orbit stay consistent. Orbit: left-drag orbits
// the target. Middle-drag always orbits, right-drag always pans. O toggles for the session; Look is the default on every load.
const LOOK_RAD_PER_PX = THREE.MathUtils.degToRad(0.25), LOOK_MAX_PITCH = THREE.MathUtils.degToRad(85);
let navMode = 'look';
// Look is always the default on load (owner); O / the toolbar switch for this session only.
try { localStorage.removeItem('monarch.nav'); } catch { /* storage blocked */ }
// Measure mode owns the plain left button (clicks place points, a trackpad click that slides a
// few pixels must never turn the camera). Holding Space gives left-drag back to navigation
// (look / orbit, plan: pan); middle-drag orbit, right-drag pan, scroll and WASD always work.
const measureNav = { on: false, space: false };
function applyNavMode() {
  const leftFree = !measureNav.on || measureNav.space;
  orbit.mouseButtons = { LEFT: navMode === 'look' || !leftFree ? -1 : THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.ROTATE, RIGHT: THREE.MOUSE.PAN };
  planControls.mouseButtons = { LEFT: leftFree ? THREE.MOUSE.PAN : -1, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.PAN };
  document.body.classList.toggle('nav-orbit', navMode === 'orbit');
  document.body.classList.toggle('space-nav', measureNav.on && measureNav.space);
}
function setNavMode(mode) {
  navMode = mode === 'orbit' ? 'orbit' : 'look';
  applyNavMode();
  if (typeof syncToolbar === 'function') syncToolbar();
}
applyNavMode();
const _lookDir = new THREE.Vector3();
/** Turn the perspective camera in place by (dx, dy) pixels of drag. */
function lookBy(dx, dy) {
  const dist = Math.max(persp.position.distanceTo(orbit.target), 0.05);
  _lookDir.copy(orbit.target).sub(persp.position).normalize();
  let yaw = Math.atan2(_lookDir.x, _lookDir.z), pitch = Math.asin(THREE.MathUtils.clamp(_lookDir.y, -1, 1));
  yaw -= dx * LOOK_RAD_PER_PX;                               // drag right: turn right
  pitch = THREE.MathUtils.clamp(pitch - dy * LOOK_RAD_PER_PX, -LOOK_MAX_PITCH, LOOK_MAX_PITCH);   // drag up: look up
  _lookDir.set(Math.cos(pitch) * Math.sin(yaw), Math.sin(pitch), Math.cos(pitch) * Math.cos(yaw));
  orbit.target.copy(persp.position).addScaledVector(_lookDir, dist);
  persp.lookAt(orbit.target);
  needsRender = true;
  scheduleHash();
}
let looking = null;                                        // {id, x, y, active}
canvas.addEventListener('pointerdown', ev => {
  if (ev.button !== 0 || navMode !== 'look' || controls !== orbit || !orbit.enabled) return;
  if (measureNav.on && !measureNav.space) return;          // measuring: left-click places points
  if (ev.shiftKey || ev.ctrlKey || ev.metaKey) return;     // modifiers: OrbitControls' own pan/rotate
  looking = { id: ev.pointerId, x: ev.clientX, y: ev.clientY, sx: ev.clientX, sy: ev.clientY, active: false };
  try { canvas.setPointerCapture(ev.pointerId); } catch { /* not capturable */ }
});
canvas.addEventListener('pointermove', ev => {
  if (!looking || ev.pointerId !== looking.id) return;
  if (!looking.active && Math.hypot(ev.clientX - looking.sx, ev.clientY - looking.sy) <= 4) return;   // click threshold
  if (!looking.active) { looking.active = true; turn = null; looking.x = looking.sx; looking.y = looking.sy; }
  lookBy(ev.clientX - looking.x, ev.clientY - looking.y);
  looking.x = ev.clientX; looking.y = ev.clientY;
});
for (const type of ['pointerup', 'pointercancel', 'lostpointercapture'])
  canvas.addEventListener(type, ev => { if (looking && ev.pointerId === looking.id) looking = null; });

// Double-click in look mode: turn smoothly (250 ms) to face the point, which becomes the target.
let turn = null;                                           // {t0, from, to, d0, d1}
function turnTo(point) {
  const from = orbit.target.clone().sub(persp.position), d0 = from.length();
  const to = point.clone().sub(persp.position), d1 = to.length();
  if (d1 < 1e-3) return;
  turn = { t0: performance.now(), from: from.normalize(), to: to.normalize(), d0, d1 };
  needsRender = true;
}
const _q = new THREE.Quaternion(), _qFull = new THREE.Quaternion();
function turnStep(now) {
  if (!turn) return;
  const k = Math.min(1, (now - turn.t0) / 250), e = k * k * (3 - 2 * k);
  _qFull.setFromUnitVectors(turn.from, turn.to);
  _q.identity().slerp(_qFull, e);
  _lookDir.copy(turn.from).applyQuaternion(_q);
  orbit.target.copy(persp.position).addScaledVector(_lookDir, turn.d0 + (turn.d1 - turn.d0) * e);
  persp.lookAt(orbit.target);
  needsRender = true;
  if (k >= 1) { turn = null; scheduleHash(); }
}

// OrbitControls ignores the wheel while it believes a pointer is still down. A trackpad
// tap-click can leave that state stuck (no pointerup reaches it), after which scroll-zoom
// silently does nothing. Any wheel with no button pressed proves nothing is dragging.
function unstick(c) {
  if (c.state !== -1) { c.state = -1; if (c._pointers) c._pointers.length = 0; if (c._pointerPositions) for (const k in c._pointerPositions) delete c._pointerPositions[k]; }
}
canvas.addEventListener('wheel', ev => {
  diag('wheel', { dy: +ev.deltaY.toFixed(1), dx: +ev.deltaX.toFixed(1), mode: ev.deltaMode, ctrl: ev.ctrlKey, buttons: ev.buttons,
    renders: renderCount, state: controls.state, enabled: controls.enabled, plan: controls === planControls, dist: +controls.getDistance?.().toFixed(2) });
  if (ev.buttons === 0) { unstick(orbit); unstick(planControls); }
}, { capture: true, passive: true });

// Constant-speed wheel zoom (perspective view): every scroll step moves the camera the
// same distance along the ray through the cursor, and the orbit target moves with it,
// so zooming never slows down near objects and can fly through a room. Plan view keeps
// OrbitControls' zoom (ortho scale, already uniform).
const ZOOM_METRES_PER_PIXEL = 0.02;   // trackpad deltas ~5-50 px per event, a mouse notch ~100
const _zoomRay = new THREE.Raycaster(), _zoomNdc = new THREE.Vector2();
canvas.addEventListener('wheel', ev => {
  if (controls !== orbit || !orbit.enabled) return;
  ev.preventDefault();
  let dy = ev.deltaY * (ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? 100 : 1);
  if (ev.ctrlKey) dy *= 5;                                          // trackpad pinch
  dy = Math.max(-300, Math.min(300, dy));
  const r = canvas.getBoundingClientRect();
  _zoomNdc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
  _zoomRay.setFromCamera(_zoomNdc, persp);
  const step = _zoomRay.ray.direction.clone().multiplyScalar(-dy * ZOOM_METRES_PER_PIXEL * (ev.shiftKey ? 3 : 1));
  persp.position.add(step);
  orbit.target.add(step);
  orbit.update();
  needsRender = true;
}, { passive: false });

// Diagnostics: interaction events go to the server log (<state_dir>/web-client.log) so a
// problem seen only in the owner's real browser can be diagnosed. Throttled, batched.
const diagQueue = [];
let diagWheel = 0;
function diag(type, data = {}) {
  if (SNAP) return;
  if (type === 'wheel' && ++diagWheel % 5 !== 1) return;          // every 5th wheel event
  diagQueue.push({ t: Math.round(performance.now()), type, ...data });
}
setInterval(() => {
  if (!diagQueue.length) return;
  const body = diagQueue.splice(0).map(d => JSON.stringify(d)).join('\n');
  fetch('/api/log', { method: 'POST', body, keepalive: true }).catch(() => {});
}, 1000);
diag('start', { ua: navigator.userAgent, w: innerWidth, h: innerHeight, dpr: devicePixelRatio });
window.addEventListener('error', ev => diag('error', { msg: String(ev.message), at: `${ev.filename}:${ev.lineno}` }));
window.addEventListener('unhandledrejection', ev => diag('error', { msg: String(ev.reason) }));
for (const type of ['pointerdown', 'pointerup', 'pointercancel', 'lostpointercapture'])
  canvas.addEventListener(type, ev => diag(type, { id: ev.pointerId, kind: ev.pointerType, buttons: ev.buttons, state: controls.state }));
for (const type of ['pointercancel', 'lostpointercapture']) canvas.addEventListener(type, () => { unstick(orbit); unstick(planControls); });
window.addEventListener('blur', () => { unstick(orbit); unstick(planControls); });

const clipPlane = new THREE.Plane(new THREE.Vector3(0, -1, 0), 0);   // keeps three y <= constant
const POCHE = { value: 0 };                               // back faces slate while cutting (section fill)
// Section fill: a mid slate, so cut walls read against both the navy background and light floors.
const POCHE_COLOUR = new THREE.Color('#8793aa');
const POCHE_EARTH = new THREE.Color('#2b3342');           // cut terrain (SITE): darker, so the building's cut walls lead
const EDGE_MATERIAL = new THREE.LineBasicMaterial({ color: 0x1d2025, transparent: true, opacity: 0.28 });
const ACCENT = new THREE.Color('#3ea6ff');
const SELECT_BOX = new THREE.Box3Helper(new THREE.Box3(), ACCENT.clone().multiplyScalar(1.6));   // bright accent (bloom is off)
SELECT_BOX.material.depthTest = false;
SELECT_BOX.material.transparent = true;
SELECT_BOX.renderOrder = 10;
SELECT_BOX.visible = false;
scene.add(SELECT_BOX);

const FINISH_CLASSES = new Set(['IfcCovering', 'IfcAnnotation']);
function classOf(o) {
  for (let n = o; n; n = n.parent) if (n.userData?.class) return n.userData.class;
  return '';
}
function keyOf(o) {
  for (let n = o; n; n = n.parent) if (n.userData?.key) return n.userData.key;
  return '';
}
function depthRank(key) {                                   // stable 0..11 from the element key
  let h = 2166136261;
  for (let i = 0; i < key.length; i++) h = Math.imul(h ^ key.charCodeAt(i), 16777619);
  return (h >>> 0) % 12;
}
function patchMaterial(m, earth = false) {
  const rank = m.userData.depthRank ?? 0;
  m.polygonOffset = true;                                 // faces behind their edge lines, ranked per element
  // A small slope term stabilises coplanar faces at oblique angles. The former
  // factors 1..3.75 displaced finishes by centimetres and pulled wall slats
  // through a poster mounted in front of them. Keep this bias sub-pixel.
  m.polygonOffsetFactor = m.userData.finish ? -0.01 - rank * 0.0025 : 0.01 + rank * 0.0025;
  m.polygonOffsetUnits = m.userData.finish ? -4 - rank * 2 : 1 + rank * 2;
  m.onBeforeCompile = shader => {
    shader.uniforms.uPoche = POCHE;
    shader.uniforms.uPocheColour = { value: m.userData.earth ? POCHE_EARTH : POCHE_COLOUR };
    shader.fragmentShader = 'uniform float uPoche;\nuniform vec3 uPocheColour;\n' + shader.fragmentShader.replace('#include <dithering_fragment>',
      '#include <dithering_fragment>\n\tif (uPoche > 0.5 && !gl_FrontFacing) gl_FragColor = vec4(uPocheColour, 1.0);');
  };
  if (earth) m.userData.earth = true;
  if (m.transparent && m.opacity < 0.5) { m.roughness = 0.2; m.envMapIntensity = 0.8; }   // glass: catch a soft reflection
}

// ---------------------------------------------------------------- state
const state = {
  storeys: new Set(STOREYS), cut: null, plan: null, sel: null, edges: true, spaces: false,
  plaques: false,                  // floating Pset_Gallery titles (default on when the model has any)
  measure: false,
  removed: new Set(),              // keys hidden with X: play-around only, never saved, gone on reload
};
const removedStack = [];           // removal order for Z (undo)
let model = null;                  // THREE.Group of the loaded glb
let elements = new Map();          // key -> element from elements.json
let nodes = new Map();             // key -> glTF node (Object3D)
let galleryKeys = [];              // elements with psets.Pset_Gallery, in elements.json order
let houseName = 'Monarch';
let pickables = [];                // meshes currently visible
let loadedVersion = null, loadingVersion = null, wantedVersion = null;
let modelRetry = null;
let needsRender = true;
const invalidate = () => { needsRender = true; };
// Wheel zoom is applied inside OrbitControls' own handler, so the frame loop's
// controls.update() sees no change afterwards: redraw on every 'change' event instead,
// or scroll-zoom moves the camera without redrawing the picture.
for (const c of [orbit, planControls]) c.addEventListener('change', () => { needsRender = true; scheduleHash(); });

// ---------------------------------------------------------------- model loading / hot reload
const loader = new GLTFLoader();
const loadingEl = document.getElementById('loading');

async function loadModel(version) {
  loadingVersion = version;
  const q = encodeURIComponent(version || Date.now());
  const [gltf, data] = await Promise.all([
    loader.loadAsync(`/generated/model.glb?v=${q}`),
    fetch(`/generated/elements.json?v=${q}`).then(r => {
      if (!r.ok) throw new Error(`Model metadata unavailable (${r.status})`);
      return r.json();
    }),
  ]);
  if (data.build_id && gltf.userData?.build_id !== data.build_id) {
    dispose(gltf.scene);
    throw new Error('Model and properties belong to different builds; waiting for the completed export.');
  }
  const next = gltf.scene;
  const newNodes = new Map();
  const siteMeshes = new Set();
  next.traverse(o => {
    if (o.userData?.key && storeyOf(o.userData.storey ?? '') === 'SITE') o.traverse(c => { if (c.isMesh) siteMeshes.add(c); });
  });
  next.traverse(o => {
    if (o.isMesh) {
      // The glb has no normals (flat shading via derivatives); shadows need them for the normal
      // offset, else normalize(0) = NaN. Computed once per load, shading is unaffected.
      if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
      // Own material per mesh (programs are still shared): coplanar faces of different elements
      // z-fight (stripes, checkerboards, flicker). A fixed depth rank per element key decides
      // deterministically which one wins; finish layers (paint, tiles, floor finishes) always win
      // over the wall or slab they cover.
      o.material = o.material.clone();
      o.material.userData.finish = FINISH_CLASSES.has(classOf(o));
      o.material.userData.depthRank = depthRank(keyOf(o));
      patchMaterial(o.material, siteMeshes.has(o));
      o.castShadow = !o.material.transparent;
      o.receiveShadow = true;
    }
    const key = o.userData?.key;
    if (key && !newNodes.has(key)) newNodes.set(key, o);
  });
  next.updateMatrixWorld(true);
  const oldSel = state.sel;
  if (oldSel) setSelection(null, { keepState: true });
  if (model) { scene.remove(model); dispose(model); }
  model = next;
  scene.add(model);
  nodes = newNodes;
  elements = new Map(data.elements.map(e => [e.key, e]));
  applyHouse(data.house || await fetch('/api/house').then(r => (r.ok ? r.json() : null)).catch(() => null));
  const hadGallery = galleryKeys.length > 0;
  galleryKeys = data.elements.filter(e => e.psets?.Pset_Gallery).map(e => e.key);
  if (!galleryKeys.length) state.plaques = false;
  else if (!hadGallery && loadedVersion !== null) state.plaques = true;   // a reload brought the first exhibits
  buildPlaques();
  loadedVersion = version;
  loadingVersion = null;
  applyVisibility();
  if (oldSel && nodes.has(oldSel)) setSelection(oldSel, { keepState: true });
  else if (oldSel) { state.sel = null; renderPanel(); }
  queueEdges();
  invalidate();
  loadingEl.hidden = true;
}

/** Storeys, name and the storey widgets from the house summary (elements.json "house",
 * else /api/house). Without a storey list the codes come from the elements themselves. */
function applyHouse(house) {
  houseName = house?.name || 'Monarch';
  document.querySelector('#bar .brand strong').textContent = houseName;
  document.title = houseName === 'Monarch' ? 'Monarch' : `${houseName} · Monarch`;
  let list = Array.isArray(house?.storeys) ? house.storeys.filter(s => s && s.code != null && String(s.code).toUpperCase() !== 'SITE') : [];
  if (!list.length) {
    const seen = new Map();
    for (const e of elements.values()) {
      if (!e.storey || e.storey === 'SITE') continue;
      seen.set(e.storey, Math.min(seen.get(e.storey) ?? Infinity, e.bbox?.[2] ?? 0));
    }
    list = [...seen].map(([code, elevation]) => ({ code, elevation })).sort((a, b) => a.elevation - b.elevation);
  }
  const before = new Set(STOREYS), first = loadedVersion === null;
  setStoreys(list);
  // Keep the user's storey choice across a reload; storeys new to the model start visible.
  state.storeys = new Set(STOREYS.filter(s => first || !before.has(s) || state.storeys.has(s)));
  if (state.cut && !FLOORS.includes(state.cut.storey)) state.cut = null;
  if (state.plan && !FLOORS.includes(state.plan)) state.plan = groundStorey();
  buildStoreyChips();
  cutSel.innerHTML = '<option value="">off</option>' + FLOORS.map(c => `<option>${esc(c)}</option>`).join('');
}

function dispose(root) {
  const textures = new Set();
  root.traverse(o => {
    o.geometry?.dispose();
    if (o.material && o.material !== EDGE_MATERIAL) [].concat(o.material).forEach(m => {
      if (m.map) textures.add(m.map);
      m.dispose();
    });
  });
  for (const texture of textures) { texture.dispose(); texture.image?.close?.(); }
}

async function ensureVersion(version) {
  wantedVersion = version;
  if (loadingVersion !== null || version === loadedVersion) return;
  clearTimeout(modelRetry);
  try {
    await loadModel(version);
  } catch (err) {
    console.warn('model load deferred', err);
    loadingVersion = null;
    setPill(buildPill, 'building', 'Retrying model load', String(err));
    modelRetry = setTimeout(() => ensureVersion(wantedVersion), 1500);
    return;
  }
  if (wantedVersion !== loadedVersion) ensureVersion(wantedVersion);   // a newer build landed meanwhile
}

// ---------------------------------------------------------------- edges (lazy, cheap)
let edgeQueue = [];
function queueEdges() {
  edgeQueue = [];
  model.traverse(o => { if (o.isMesh && !o.userData.edgesDone) edgeQueue.push(o); });
  // visible meshes first
  edgeQueue.sort((a, b) => isShown(b) - isShown(a));
  if (!SNAP) scheduleEdges();
}
function isShown(o) {
  for (let p = o; p; p = p.parent) if (!p.visible) return false;
  return true;
}
function addEdges(mesh) {
  mesh.userData.edgesDone = true;
  const g = mesh.geometry;
  const tris = (g.index ? g.index.count : g.attributes.position.count) / 3;
  if (tris > EDGE_MAX_TRIS) return;                       // scanned product meshes: edges are noise
  const lines = new THREE.LineSegments(new THREE.EdgesGeometry(g, EDGE_ANGLE), EDGE_MATERIAL);
  lines.raycast = () => {};
  lines.userData.isEdge = true;
  mesh.add(lines);
}
function scheduleEdges() {
  const idle = window.requestIdleCallback || (cb => setTimeout(() => cb({ timeRemaining: () => 12 }), 16));
  idle(deadline => {
    while (edgeQueue.length && deadline.timeRemaining() > 2) addEdges(edgeQueue.shift());
    invalidate();
    if (edgeQueue.length) scheduleEdges();
  });
}
function buildAllEdges() { while (edgeQueue.length) addEdges(edgeQueue.shift()); }

// ---------------------------------------------------------------- visibility & cut
function storeyVisible(s) {
  if (!state.storeys.has(s)) return false;
  if (state.cut && s !== 'SITE' && RANK[s] > RANK[state.cut.storey]) return false;
  return true;
}
function applyVisibility() {
  pickables = [];
  if (model) {
    for (const [key, node] of nodes) {
      const e = elements.get(key) || node.userData;
      node.visible = !state.removed.has(key) && storeyVisible(storeyOf(e.storey)) && (state.spaces || e.class !== 'IfcSpace');
    }
    model.traverse(o => { if (o.isMesh && isShown(o)) pickables.push(o); });
  }
  if (state.cut) {
    clipPlane.constant = ELEV[state.cut.storey] + state.cut.height;
    renderer.clippingPlanes = [clipPlane];
    POCHE.value = 1;
  } else {
    renderer.clippingPlanes = [];
    POCHE.value = 0;
  }
  EDGE_MATERIAL.visible = state.edges;
  plaqueBox.hidden = !state.plaques || !galleryKeys.length;
  placeGround();
  syncShadows();
  invalidateShadows();
  syncToolbar();
  invalidate();
}

function placeGround() {
  const b = unionBox([...nodes.keys()].filter(k => nodes.get(k).visible)) || houseBox();
  const hb = houseBox();
  const cx = (hb[0] + hb[3]) / 2, cy = (hb[1] + hb[4]) / 2;
  GROUND.position.copy(toThree(cx, cy, b[2] - 0.03));
  GROUND.scale.setScalar(Math.max(hb[3] - hb[0], hb[4] - hb[1]) * 4);
  fitSun(model ? hb : [0, 0, -3, 15, 10, 10]);
}

// ---------------------------------------------------------------- cameras
function viewportSize() {
  return SNAP ? [snapSize.w, snapSize.h] : [window.innerWidth, window.innerHeight];
}
function resize() {
  const [w, h] = viewportSize();
  renderer.setSize(w, h, !SNAP);
  composer.setPixelRatio(PIXEL_RATIO);
  composer.setSize(w, h);
  persp.aspect = w / h;
  persp.updateProjectionMatrix();
  setOrthoSpan(currentSpan());
  invalidate();
}
function currentSpan() { return (ortho.top - ortho.bottom) / ortho.zoom; }
function setOrthoSpan(span) {
  const [w, h] = viewportSize();
  const a = w / h;
  ortho.top = span / 2; ortho.bottom = -span / 2;
  ortho.left = -span * a / 2; ortho.right = span * a / 2;
  ortho.zoom = 1;
  ortho.updateProjectionMatrix();
}

function unionBox(keys) {
  const b = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
  for (const k of keys) {
    const bb = elements.get(k)?.bbox;
    if (!bb) continue;
    for (let i = 0; i < 3; i++) { b[i] = Math.min(b[i], bb[i]); b[i + 3] = Math.max(b[i + 3], bb[i + 3]); }
  }
  return Number.isFinite(b[0]) ? b : null;
}
function storeyKeys(storey) {
  return [...elements.values()].filter(e => storeyOf(e.storey) === storey && e.class !== 'IfcSpace'
    && !e.class.includes('Annotation')).map(e => e.key);
}
function houseBox() {
  return unionBox([...elements.values()].filter(e => storeyOf(e.storey) !== 'SITE').map(e => e.key))
    || [0, 0, -3, 15, 10, 10];
}

function setPerspective(camIfc, targetIfc) {
  camera = persp;
  state.plan = null;
  orbit.enabled = true; planControls.enabled = false; controls = orbit;
  persp.position.copy(toThree(...camIfc));
  orbit.target.copy(toThree(...targetIfc));
  persp.lookAt(orbit.target);
  orbit.update();
  invalidate();
}
function homeView() {
  const b = houseBox();
  const c = [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2 - 1];
  const r = Math.hypot(b[3] - b[0], b[4] - b[1], b[5] - b[2]) / 2;
  const d = r / Math.sin(THREE.MathUtils.degToRad(persp.fov / 2)) * 0.8;
  const dir = new THREE.Vector3(-0.62, -1, 0.62).normalize();   // from south-west, above
  setPerspective([c[0] + dir.x * d, c[1] + dir.y * d, c[2] + dir.z * d], c);
}
function enterPlan(storey, centre, span, keepCut = false) {
  state.plan = storey;
  if (!keepCut && (!state.cut || state.cut.storey !== storey)) state.cut = { storey, height: state.cut?.height ?? DEFAULT_CUT };
  const b = unionBox(storeyKeys(storey)) || houseBox();
  const [w, h] = viewportSize();
  const cx = centre?.[0] ?? (b[0] + b[3]) / 2, cy = centre?.[1] ?? (b[1] + b[4]) / 2;
  span = span || Math.max(b[4] - b[1], (b[3] - b[0]) * h / w) * 1.15;
  const z = ELEV[storey];
  ortho.position.copy(toThree(cx, cy, z + 100));
  planControls.target.copy(toThree(cx, cy, z));
  setOrthoSpan(span);
  ortho.lookAt(planControls.target);
  camera = ortho;
  orbit.enabled = false; planControls.enabled = true; controls = planControls;
  planControls.update();
  applyVisibility();
}
function exitPlan() {
  state.plan = null;
  camera = persp;
  orbit.enabled = true; planControls.enabled = false; controls = orbit;
  applyVisibility();
}

/** Oblique south-east view of the union bbox of keys (exact key, else prefix, else substring). */
function matchKeys(pattern) {
  if (elements.has(pattern)) return [pattern];
  const all = [...elements.keys()];
  const lower = pattern.toLowerCase();
  let hits = all.filter(k => k.startsWith(pattern));
  if (!hits.length) hits = all.filter(k => k.toLowerCase().startsWith(lower));
  if (!hits.length) hits = all.filter(k => k.toLowerCase().includes(lower));
  return hits.filter(k => elements.get(k).class !== 'IfcSpace' || hits.length === 1);
}
function frameView(pattern, explicitCut) {
  const keys = matchKeys(pattern);
  const b = unionBox(keys);
  if (!b) { console.warn(`monarch warning: frame=${pattern} matches no element`); return false; }
  const counts = {};
  keys.forEach(k => { const s = storeyOf(elements.get(k).storey); counts[s] = (counts[s] || 0) + 1; });
  const storey = Object.entries(counts).sort((a, b2) => b2[1] - a[1])[0][0];
  state.cut = explicitCut !== undefined ? explicitCut : (storey === 'SITE' ? null : { storey, height: FRAME_CUT });
  const topZ = state.cut ? Math.min(b[5], ELEV[state.cut.storey] + state.cut.height) : b[5];
  const c = [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + topZ) / 2];
  const r = Math.max(1.2, Math.hypot(b[3] - b[0], b[4] - b[1], topZ - b[2]) / 2);
  const d = r / Math.sin(THREE.MathUtils.degToRad(persp.fov / 2)) * 1.1 / Math.min(1, persp.aspect);
  const dir = new THREE.Vector3(0.55, -0.8, 1.05).normalize();
  setPerspective([c[0] + dir.x * d, c[1] + dir.y * d, c[2] + dir.z * d], c);
  return true;
}

/** Keep the view direction, move the target to the element and fit it. */
function focusOn(key) {
  const b = elements.get(key)?.bbox;
  if (!b) return;
  const s = storeyOf(elements.get(key).storey);
  if (!state.storeys.has(s)) state.storeys.add(s);
  if (state.cut && s !== 'SITE' && RANK[s] > RANK[state.cut.storey]) state.cut = { ...state.cut, storey: s };
  const c = [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2];
  const r = Math.max(0.6, Math.hypot(b[3] - b[0], b[4] - b[1], b[5] - b[2]) / 2);
  if (state.plan) {
    enterPlan(s === 'SITE' ? state.plan : s, c, Math.max(r * 4, Math.min(currentSpan(), 30)));
  } else {
    const dir = persp.position.clone().sub(orbit.target).normalize();
    const d = r / Math.sin(THREE.MathUtils.degToRad(persp.fov / 2)) * 1.6;
    orbit.target.copy(toThree(...c));
    persp.position.copy(orbit.target).addScaledVector(dir, d);
    orbit.update();
  }
  applyVisibility();
}

// ---------------------------------------------------------------- apply a view (hash or snap entry)
function applyView(v) {
  setFov(v.fov ?? DEFAULT_FOV);
  state.storeys = v.storeys ? new Set(v.storeys) : new Set(STOREYS);
  (v.hide || []).forEach(s => state.storeys.delete(s));
  state.edges = v.edges ?? true;
  state.spaces = v.spaces ?? false;
  state.plaques = v.plaques ?? galleryKeys.length > 0;
  if (v.look) setLook(v.look);
  state.cut = v.cut !== undefined ? v.cut : null;
  if (v.frame && frameView(v.frame, v.cut)) {
    // camera set by frameView
  } else if (v.plan) {
    enterPlan(v.plan, v.target, v.span, v.cut !== undefined);
  } else if (v.cam) {
    const t = v.target ? [v.target[0], v.target[1], v.target[2] ?? v.cam[2]] : null;
    setPerspective(v.cam, t || defaultTargetFrom(v.cam));
  } else if (v.target) {
    const t = [v.target[0], v.target[1], v.target[2] ?? 0];
    const off = persp.position.clone().sub(orbit.target);
    setPerspective(toIfc(toThree(...t).add(off)), t);
  } else {
    homeView();
  }
  const sel = v.sel && elements.has(v.sel) ? v.sel : caseInsensitiveKey(v.sel);
  if (v.sel && !sel) console.warn(`monarch warning: sel=${v.sel} matches no element`);
  setSelection(sel, { keepState: true });
  applyVisibility();
}
function defaultTargetFrom(cam) {
  const b = houseBox();
  return [(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2];
}
function caseInsensitiveKey(k) {
  if (!k) return null;
  const lower = k.toLowerCase();
  for (const key of elements.keys()) if (key.toLowerCase() === lower) return key;
  return null;
}

// ---------------------------------------------------------------- hash writing
let hashTimer = null, lastHash = '';
function currentHash() {
  const p = [];
  if (persp.fov !== DEFAULT_FOV) p.push(`fov=${persp.fov}`);
  if (state.plan) {
    const t = toIfc(planControls.target);
    p.push(`plan=${state.plan}`, `target=${fmt(t.slice(0, 2))}`, `span=${currentSpan().toFixed(1)}`);
  } else {
    p.push(`cam=${fmt(toIfc(persp.position))}`, `target=${fmt(toIfc(orbit.target))}`);
  }
  if (state.storeys.size !== STOREYS.length) p.push(`storeys=${STOREYS.filter(s => state.storeys.has(s)).join(',') || 'none'}`);
  if (state.cut && !(state.plan && state.cut.storey === state.plan && state.cut.height === DEFAULT_CUT)) {
    p.push(`cut=${state.cut.storey}@${+state.cut.height.toFixed(2)}`);
  } else if (!state.cut && state.plan) p.push('cut=off');
  if (state.sel) p.push(`sel=${encodeURIComponent(state.sel)}`);
  if (!state.edges) p.push('edges=0');
  if (state.spaces) p.push('spaces=1');
  if (galleryKeys.length && !state.plaques) p.push('plaques=0');
  if (look === 'true') p.push('look=true');
  return '#' + p.join('&').replace(/, /g, ',');
}
function scheduleHash() {
  if (SNAP) return;
  clearTimeout(hashTimer);
  hashTimer = setTimeout(() => {
    updateStats();
    lastHash = currentHash();
    if (location.hash !== lastHash) history.replaceState(null, '', lastHash);
  }, 350);
}
window.addEventListener('hashchange', () => {
  if (SNAP || location.hash === lastHash) return;
  lastHash = location.hash;
  applyView(parseView(parseHashString(location.hash)));
});

// ---------------------------------------------------------------- selection & product panel
const panel = document.getElementById('panel'), panelBody = document.getElementById('panelBody');
const HIGHLIGHT = ACCENT;

function setSelection(key, { keepState = false } = {}) {
  diag('select', { key });
  if (state.sel && nodes.has(state.sel)) {
    nodes.get(state.sel).traverse(o => {
      if (o.isMesh && o.userData.origMaterial) {
        o.material.dispose();
        o.material = o.userData.origMaterial;
        delete o.userData.origMaterial;
      }
    });
  }
  state.sel = key && nodes.has(key) ? key : null;
  SELECT_BOX.visible = false;
  if (state.sel) {
    const node = nodes.get(state.sel);
    node.traverse(o => {
      if (!o.isMesh || o.userData.isEdge) return;
      const m = o.material.clone();
      patchMaterial(m);
      if (m.emissive) { m.emissive.copy(HIGHLIGHT); m.emissiveIntensity = 0.35; }
      o.userData.origMaterial = o.material;
      o.material = m;
    });
    const b = elements.get(state.sel)?.bbox;
    if (b) {
      SELECT_BOX.box.min.copy(toThree(b[0], b[4], b[2]));
      SELECT_BOX.box.max.copy(toThree(b[3], b[1], b[5]));
      SELECT_BOX.visible = true;
    }
  }
  renderPanel();
  updateStats();
  invalidate();
  if (!keepState) scheduleHash();
}

/** Authored sRGB colours of an element's meshes (the finish as modelled, independent of lighting). */
function materialColours(key) {
  const seen = new Map(), c = new THREE.Color();
  nodes.get(key)?.traverse(o => {
    const m = o.isMesh && (o.userData.origMaterial || o.material);
    if (m?.color) seen.set('#' + c.copy(m.color).getHexString(THREE.SRGBColorSpace), m.opacity);
  });
  const list = [...seen].slice(0, 6).map(([hex, a]) => `<span class="swatch" style="background:${hex}"></span><span class="mono">${hex}${a < 1 ? ` · ${Math.round(a * 100)}%` : ''}</span>`);
  return list.length ? [['Colour (authored)', list.join('<br>')]] : [];
}
function table(rows) {
  return '<table>' + rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join('') + '</table>';
}
function renderPanel() {
  const e = state.sel && elements.get(state.sel);
  panel.hidden = !e || SNAP;
  if (!e) return;
  const b = e.bbox;
  const size = [b[3] - b[0], b[4] - b[1], b[5] - b[2]].map(n => Math.round(n * 1000) / 10);
  let html = `<div class="eyebrow"><i></i>${esc(e.class.replace(/^Ifc/, ''))} · ${esc(e.storey)}</div>`
    + `<h2>${esc(e.label || e.name || e.key)}</h2><div class="key">${esc(e.key)}</div>`;
  html += `<div class="actions"><button data-act="frame">Frame<kbd>F</kbd></button><button data-act="remove">Remove<kbd>X</kbd></button>`
    + `<button data-act="copy">Copy key</button><button data-act="link">Copy link</button></div>`
    + `<div class="hint">Deselect: Esc, ×, or click the object again · Remove hides it for this session only (Z undo, Shift+Z restore all)</div>`;
  html += table([
    ['Product', e.label ? esc(e.label) : '<span class="muted">(no product name)</span>'],
    ['Class', esc(e.class)],
    ['Storey', esc(e.storey)],
    ['Size (x × y × z)', `${size.join(' × ')} cm`],
    ...materialColours(state.sel),
    ['Bbox min', `<span class="mono">${fmt(b.slice(0, 3), 3)}</span>`],
    ['Bbox max', `<span class="mono">${fmt(b.slice(3), 3)}</span>`],
    ['GUID', `<span class="mono">${esc(e.guid || '')}</span>`],
  ]);
  for (const [pset, props] of Object.entries(e.psets || {})) {
    html += `<details open><summary>${esc(pset)}</summary>${table(Object.entries(props).map(([k, v]) => [k, linkify(v)]))}</details>`;
  }
  panelBody.innerHTML = html;
}
// ---------------------------------------------------------------- play-around removal (X / Z)
// Hides an element for this browser session only. Nothing is written to the model or the URL;
// a page reload brings everything back. Z undoes the last removal, Shift+Z restores all.
function removeSelected() {
  if (!state.sel || !nodes.has(state.sel)) return;
  const key = state.sel;
  setSelection(null, { keepState: true });
  state.removed.add(key);
  removedStack.push(key);
  applyVisibility();
  syncToolbar();
}
function undoRemove(all = false) {
  if (!removedStack.length) return;
  const keys = all ? removedStack.splice(0) : [removedStack.pop()];
  for (const k of keys) state.removed.delete(k);
  applyVisibility();
  if (!all && nodes.has(keys[0])) setSelection(keys[0], { keepState: true });
  syncToolbar();
}
panelBody.addEventListener('click', ev => {
  const act = ev.target.dataset?.act;
  if (act === 'frame') focusOn(state.sel);
  if (act === 'remove') removeSelected();
  if (act === 'copy') navigator.clipboard?.writeText(state.sel);
  if (act === 'link') navigator.clipboard?.writeText(location.origin + '/' + currentHash());
});
document.getElementById('panelClose').onclick = () => setSelection(null);
document.getElementById('documents').onclick = async () => {
  const dialog = document.getElementById('documentsDialog');
  const body = document.getElementById('documentsBody');
  dialog.showModal();
  try {
    const response = await fetch('/api/documents');
    if (!response.ok) throw new Error(`Source documents could not be loaded (${response.status})`);
    const data = await response.json();
    const errors = data.errors || [], docs = data.documents || [];
    body.innerHTML = (errors.length ? `<p class="warning">${errors.map(esc).join('<br>')}</p>` : '')
      + (docs.length ? '' : '<p class="empty">No source documents registered. Add data/plan_documents.json to pin the plans and datasheets this model was traced from.</p>')
      + docs.map(d => `<details><summary>${esc(d.title)} · ${esc(d.printed_date)}</summary>`
        + `<p>${d.available ? `<a href="${esc(d.href)}" target="_blank" rel="noopener">Open PDF</a>` : 'Document unavailable'} · ${esc(d.status)}</p>`
        + `<p>${esc(d.scope)}</p><p>${esc(d.model_evidence)}</p></details>`).join('');
  } catch (error) { body.textContent = String(error); }
};

// ---------------------------------------------------------------- picking
const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();
function pick(clientX, clientY) {
  if (!model) return null;
  camera.updateMatrixWorld();                              // the camera may have moved since the last redraw
  const r = canvas.getBoundingClientRect();
  ndc.set(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
  raycaster.setFromCamera(ndc, camera);
  const clipY = state.cut ? clipPlane.constant + 1e-4 : Infinity;
  for (const hit of raycaster.intersectObjects(pickables, false)) {
    if (hit.point.y > clipY) continue;
    let o = hit.object;
    while (o && !o.userData.key) o = o.parent;
    return { point: hit.point.clone(), key: o?.userData.key ?? null, object: hit.object, distance: hit.distance, face: hit.face };
  }
  return null;
}

// ---------------------------------------------------------------- measure
// M toggles. Left-click two points: a click may slide up to 10 px and last up to 400 ms (trackpad
// clicks), and in Measure mode the plain left button never looks around (Space+drag does, see
// applyNavMode). A new click after a finished measurement starts a new one, Esc clears.
// Snapping: the cursor pulls to feature-edge corners within 10 px, else to feature edges within
// 7 px, else stays on the face under it. Candidates are the meshes whose bounding sphere touches
// a thin cone around the pick ray, minus anything behind the surface the ray hits; the edges are
// the same EdgesGeometry (30 deg threshold, no flat-face diagonals) the viewer draws. Alt: no
// snap. Shift: lock to the dominant axis, applied on top of the snap.
// Drawing: screen-space ribbons (part in front solid, part behind geometry faint and dashed),
// faint dashed dx / dy / dz guides, SVG ticks / dots / snap marker and HTML labels, all
// re-projected on each redraw, so nothing runs while the page is idle.
const MEASURE_CLICK = { px: 10, ms: 400 }, SNAP_VERTEX_PX = 10, SNAP_EDGE_PX = 7;   // MEASURE_CLICK: tests may widen ms (headless ~1 fps)
const AXIS_COLOURS = { x: '#ff6b6b', y: '#6fdc8c', z: '#5aa9ff' };        // IFC x east, y north, z up
const measure = { points: [], preview: null, hover: null, pointer: null };

const RIBBON_VERT = `uniform vec3 uA, uB; uniform vec2 uRes; uniform float uWidth, uNudge, uNear, uOrtho;
attribute vec2 corner;
varying vec2 vDash;
void main() {
  vec4 a = viewMatrix * vec4(uA, 1.0), b = viewMatrix * vec4(uB, 1.0);
  if (uOrtho < 0.5) {                                        // clip the segment at the near plane
    float nz = -uNear * 1.5;
    if (a.z > nz && b.z > nz) { gl_Position = vec4(0.0, 0.0, 2.0, 1.0); vDash = vec2(0.0, 1.0); return; }
    if (a.z > nz) a = mix(a, b, (nz - a.z) / (b.z - a.z));
    else if (b.z > nz) b = mix(b, a, (nz - b.z) / (a.z - b.z));
  }
  if (uNudge > 0.5) {                // pull towards the eye along the view ray: same pixels, but the
    if (uOrtho > 0.5) { a.z += 0.03; b.z += 0.03; }          // line wins against the face it lies on
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
  vDash = vec2((atB ? len : 0.0) * c.w, c.w);              // screen-linear distance (divided per pixel)
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
  hidden: makeRibbon({ colour: '#ffb454', width: 1.5, opacity: 0.5, dash: [6, 5], depthTest: false, order: 30 }),   // behind geometry
  casing: makeRibbon({ colour: '#070a11', width: 5, opacity: 0.5, order: 31 }),
  line: makeRibbon({ colour: '#ffb454', width: 2.25, order: 32 }),
  x: makeRibbon({ colour: AXIS_COLOURS.x, width: 1.25, opacity: 0.75, dash: [4, 4], depthTest: false, order: 29 }),
  y: makeRibbon({ colour: AXIS_COLOURS.y, width: 1.25, opacity: 0.75, dash: [4, 4], depthTest: false, order: 29 }),
  z: makeRibbon({ colour: AXIS_COLOURS.z, width: 1.25, opacity: 0.75, dash: [4, 4], depthTest: false, order: 29 }),
};
const measureBox = document.getElementById('measureBox'), measureSvg = document.getElementById('measureSvg');
const mLabel = document.getElementById('measureLabel');
const axisLabels = Object.fromEntries(['x', 'y', 'z'].map(k => [k, document.querySelector(`.mlab.axis.${k}`)]));

function axisSnap(a, b) {
  const d = b.clone().sub(a);
  const ax = ['x', 'y', 'z'].reduce((m, k) => (Math.abs(d[k]) > Math.abs(d[m]) ? k : m), 'x');
  const out = a.clone();
  out[ax] = b[ax];
  return out;
}
/** The dx / dy / dz legs from a to b (three.js points), in IFC axes, with the trivial ones dropped. */
function measureLegs(a, b) {
  const ia = toIfc(a), ib = toIfc(b), d = ib.map((v, i) => v - ia[i]);
  const corners = [a, toThree(ib[0], ia[1], ia[2]), toThree(ib[0], ib[1], ia[2]), b];
  const legs = ['x', 'y', 'z'].map((k, i) => ({ k, d: d[i], from: corners[i], to: corners[i + 1] }))
    .filter(l => Math.abs(l.d) >= 0.005);
  return legs.length >= 2 ? legs : [];                    // along one axis: the line says it all
}
function updateMeasure() {
  const [a, b0] = measure.points;
  const b = b0 || measure.preview;
  for (const m of Object.values(mRibbons)) m.visible = false;
  const legs = state.measure && a && b ? measureLegs(a, b) : [];
  if (state.measure && a && b) {
    setRibbon(mRibbons.hidden, a, b); setRibbon(mRibbons.casing, a, b); setRibbon(mRibbons.line, a, b);
    for (const l of legs) setRibbon(mRibbons[l.k], l.from, l.to);
  }
  measure.legs = legs;
  if (!state.measure) { measureBox.hidden = true; invalidate(); return; }
  const help = `<div class="mhelp">Space+drag: ${state.plan ? 'pan' : navMode} · Shift: axis · Alt: no snap · Esc: clear</div>`;
  let html;
  if (!a) html = `<div class="mstep">Click the first point</div>${help}`;
  else {
    const ia = toIfc(a);
    if (!b) html = `<div class="mstep">Click the second point</div><div>A <span>${fmt(ia, 3)}</span></div>${help}`;
    else {
      const ib = toIfc(b), d = ib.map((v, i) => v - ia[i]);
      html = `<b class="${b0 ? '' : 'live'}">${a.distanceTo(b).toFixed(3)} m</b>`
        + `<div class="mdelta">${['x', 'y', 'z'].map((k, i) => `<span class="m${k}">Δ${k} ${d[i].toFixed(3)}</span>`).join('')}</div>`
        + `<div>A <span>${fmt(ia, 3)}</span></div><div>B <span>${fmt(ib, 3)}</span></div>`
        + (b0 ? '<div class="mhelp">Click to start a new measurement · Esc: clear</div>' : help);
    }
  }
  measureBox.innerHTML = html;
  measureBox.hidden = false;
  invalidate();
}
function setMeasure(on) {
  state.measure = on;
  measureNav.on = on;
  measure.points = []; measure.preview = null; measure.hover = null;
  document.body.classList.toggle('measuring', on);
  applyNavMode();
  updateMeasure();
  syncToolbar();
}

// Overlay (SVG + HTML labels), re-projected after every redraw.
const _sv = new THREE.Vector3(), _sa = new THREE.Vector3(), _sb = new THREE.Vector3();
let canvasRect = null;
/** three.js world point -> client px {x, y}, or null behind the eye. */
function toScreen(v) {
  _sv.copy(v).applyMatrix4(camera.matrixWorldInverse);
  if (camera.isPerspectiveCamera && _sv.z > -camera.near) return null;
  _sv.applyMatrix4(camera.projectionMatrix);
  return { x: canvasRect.left + (_sv.x + 1) / 2 * canvasRect.width, y: canvasRect.top + (1 - _sv.y) / 2 * canvasRect.height };
}
/** A segment in client px, clipped at the near plane; null if entirely behind the eye. */
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
  // snap marker: square = corner, highlighted piece of edge + diamond = edge, ring = face
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
    if (s && b0) {                                          // end ticks, across the line on screen
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
  // Leg labels only where they fit: a leg shorter than 56 px on screen, or a label that would
  // overlap the distance pill or another label, stays unlabelled (the box lists all three).
  const legs = new Map((measure.legs || []).map(l => [l.k, l]));
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

// ---------------------------------------------------------------- plaques (Pset_Gallery titles)
// One HTML pill per element with Pset_Gallery, anchored at the top centre of its bounding box
// (at the section plane while cut) and re-projected after every redraw. Hidden with the
// element (storey, cut, removal), behind the eye, or off screen. Clicking a pill selects it.
const plaqueBox = document.getElementById('plaques');
const plaques = new Map();                               // key -> {el, anchor: THREE.Vector3 (three coords)}
function buildPlaques() {
  plaqueBox.innerHTML = '';
  plaques.clear();
  for (const key of galleryKeys) {
    const e = elements.get(key), title = String(e.psets.Pset_Gallery.Title ?? e.label ?? key);
    const el = document.createElement('button');
    el.className = 'plaque';
    el.type = 'button';
    el.dataset.key = key;
    el.textContent = title;
    el.title = `${title} · ${key}`;
    el.hidden = true;
    el.onclick = () => setSelection(key === state.sel ? null : key);
    plaqueBox.append(el);
    plaques.set(key, { el, title });
  }
}
/** Screen position of an element's label anchor, or null when it should not show. */
function plaqueAnchor(key) {
  const node = nodes.get(key), b = elements.get(key)?.bbox;
  if (!node || !b || !isShown(node)) return null;
  const clipZ = state.cut ? clipPlane.constant : Infinity;
  if (b[2] > clipZ) return null;                         // entirely above the section plane
  const p = toScreen(toThree((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, Math.min(b[5], clipZ)));
  if (!p || p.x < canvasRect.left || p.x > canvasRect.right || p.y < canvasRect.top || p.y > canvasRect.bottom) return null;
  return p;
}
function drawPlaques() {
  if (plaqueBox.hidden) return;
  camera.updateMatrixWorld();
  canvasRect = canvas.getBoundingClientRect();
  for (const [key, { el }] of plaques) {
    const p = plaqueAnchor(key);
    if (!p) { el.hidden = true; continue; }
    el.style.transform = `translate(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px)`;
    el.classList.toggle('sel', key === state.sel);
    el.hidden = false;
  }
}
/** Snapshot mode: the PNG comes from the WebGL canvas, so the pills are painted into a 2D copy. */
function compositePlaques(target) {
  const g = target.getContext('2d');
  g.drawImage(canvas, 0, 0);
  if (!state.plaques || !galleryKeys.length) return;
  canvasRect = { left: 0, top: 0, right: target.width, bottom: target.height, width: target.width, height: target.height };
  camera.updateMatrixWorld();
  g.font = '500 13px "DM Mono", Menlo, monospace';
  g.textBaseline = 'middle';
  for (const [key, { title }] of plaques) {
    const p = plaqueAnchor(key);
    if (!p) continue;
    const w = g.measureText(title).width + 22, h = 26, x = p.x - w / 2, y = p.y - 14 - h, r = 13;
    g.strokeStyle = 'rgba(160, 190, 230, 0.45)'; g.lineWidth = 1;
    g.beginPath(); g.moveTo(p.x, y + h); g.lineTo(p.x, p.y); g.stroke();
    g.beginPath(); g.arc(p.x, p.y, 2.5, 0, Math.PI * 2); g.fillStyle = '#e8edf5'; g.fill();
    g.beginPath(); g.roundRect(x, y, w, h, r);
    g.fillStyle = 'rgba(9, 13, 21, 0.86)'; g.fill();
    g.stroke();
    g.fillStyle = '#e8edf5'; g.fillText(title, x + 11, y + h / 2 + 0.5);
  }
}

// Magnetic snap: corner > edge > face.
const _plane = new THREE.Plane(), _vinv = new THREE.Matrix4(), _toC = new THREE.Vector3(), _wa = new THREE.Vector3(), _wb = new THREE.Vector3(),
  _va = new THREE.Vector3(), _vb = new THREE.Vector3();
function featureEdges(mesh) {
  if (!mesh.userData.edgesDone) { addEdges(mesh); invalidate(); }     // the same lazy edges the viewer draws
  const lines = mesh.children.find(c => c.userData.isEdge);
  return lines ? lines.geometry.attributes.position.array : null;
}
/** The measure point under client (x, y): {point, kind: 'vertex' | 'edge' | 'face', key, edge?}. */
function snapAt(x, y, noSnap = false) {
  const hit = pick(x, y);
  if (!hit) return null;
  const face = { point: hit.point, kind: 'face', key: hit.key };
  if (noSnap) return face;
  _vinv.copy(camera.matrixWorld).invert();
  const r = canvas.getBoundingClientRect(), persp = camera.isPerspectiveCamera, ray = raycaster.ray;   // ray set by pick()
  const pxPerM = persp ? 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) / r.height : (camera.top - camera.bottom) / camera.zoom / r.height;
  // Visible = not behind the plane of the face under the cursor (2 mm slack): corners and edges of
  // that face and of anything in front of it qualify, the inside and back of the object do not.
  const plane = _plane.setFromNormalAndCoplanarPoint(_toC.copy(hit.face?.normal ?? ray.direction).transformDirection(hit.object.matrixWorld), hit.point);
  if (plane.normal.dot(ray.direction) > 0) plane.negate();
  const visible = w => plane.distanceToPoint(w) >= -0.002;
  const maxRay = hit.distance + 0.05 + 0.05 * hit.distance;             // mesh cull only
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
    if (t + ws.radius < 0 || t - ws.radius > maxRay) continue;          // behind the eye / behind the hit
    const cone = SNAP_VERTEX_PX * pxPerM * (persp ? Math.max(0, t) + ws.radius : 1);
    if (_toC.lengthSq() - t * t > (ws.radius + cone) ** 2) continue;     // not near the pick ray
    const pos = featureEdges(mesh);
    if (!pos) continue;
    const key = keyOf(mesh) || null;
    for (let i = 0; i < pos.length; i += 6) {
      _wa.fromArray(pos, i).applyMatrix4(mesh.matrixWorld); _wb.fromArray(pos, i + 3).applyMatrix4(mesh.matrixWorld);
      if (_wa.y > clipY && _wb.y > clipY) continue;          // cut: an edge through the section plane ends
      if (_wa.y > clipY) _wa.lerp(_wb, (_wa.y - clipY) / (_wa.y - _wb.y));   // there, a section corner
      else if (_wb.y > clipY) _wb.lerp(_wa, (_wb.y - clipY) / (_wb.y - _wa.y));
      _va.copy(_wa).applyMatrix4(_vinv); _vb.copy(_wb).applyMatrix4(_vinv);
      const da = -_va.z, db = -_vb.z;
      if (persp && (da < camera.near || db < camera.near)) continue;
      const [ax, ay] = toPx(_va.applyMatrix4(camera.projectionMatrix)), [bx, by] = toPx(_vb.applyMatrix4(camera.projectionMatrix));
      for (const [px, py, dep, w] of [[ax, ay, da, _wa], [bx, by, db, _wb]]) {
        if (!visible(w)) continue;
        const dd = Math.hypot(px - x, py - y);
        if (dd <= SNAP_VERTEX_PX && (!vtx || dd < vtx.d - 0.5 || (dd < vtx.d + 0.5 && dep < vtx.depth)))
          vtx = { d: dd, depth: dep, point: w.clone(), kind: 'vertex', key };
      }
      if (vtx) continue;                                   // a corner wins; edges only matter without one
      const ex = bx - ax, ey = by - ay, L2 = ex * ex + ey * ey;
      if (L2 < 1) continue;
      const s = Math.min(1, Math.max(0, ((x - ax) * ex + (y - ay) * ey) / L2));
      const dd = Math.hypot(ax + s * ex - x, ay + s * ey - y);
      if (dd > SNAP_EDGE_PX || (edge && dd >= edge.d)) continue;
      const u = persp ? (s / db) / ((1 - s) / da + s / db) : s;   // screen -> segment parameter (perspective-correct)
      const dep = da + (db - da) * u, p = _wa.clone().lerp(_wb, u);
      if (!visible(p)) continue;
      edge = { d: dd, depth: dep, point: p, kind: 'edge', key, edge: [_wa.clone(), _wb.clone()] };
    }
  }
  return vtx || edge || face;
}

// ---------------------------------------------------------------- pointer input
const cursorEl = document.getElementById('cursor'), cursorSub = document.getElementById('cursorSub');
let down = null, hoverPending = null;
const SNAP_NAMES = { vertex: 'corner', edge: 'edge', face: 'face' };
canvas.addEventListener('pointerdown', ev => {
  down = { x: ev.clientX, y: ev.clientY, t: performance.now(), space: measureNav.space, button: ev.button };
});
canvas.addEventListener('pointercancel', () => { down = null; });
canvas.addEventListener('pointerup', ev => {
  const d = down;
  down = null;
  if (!d || ev.button !== 0 || d.button !== 0) return;
  const moved = Math.hypot(ev.clientX - d.x, ev.clientY - d.y);
  if (state.measure) {
    if (d.space || moved > MEASURE_CLICK.px || performance.now() - d.t > MEASURE_CLICK.ms) return;
    const hit = snapAt(d.x, d.y, ev.altKey);               // where the press landed, i.e. the marker aimed at
    if (!hit) return;
    if (measure.points.length >= 2) measure.points = [];
    let p = hit.point.clone();
    if (measure.points.length === 1 && ev.shiftKey) p = axisSnap(measure.points[0], p);
    measure.points.push(p);
    measure.preview = null;
    measure.hover = hit;
    updateMeasure();
    return;
  }
  if (moved > 4) return;
  const hit = pick(ev.clientX, ev.clientY);
  // Clicking the selected object again (or empty space) clears the selection.
  setSelection(hit?.key && hit.key !== state.sel ? hit.key : null);
});
canvas.addEventListener('dblclick', ev => {
  if (state.measure) return;                               // two quick measure clicks are not a turn
  const hit = pick(ev.clientX, ev.clientY);
  if (!hit) return;
  if (state.plan) {
    const off = ortho.position.clone().sub(planControls.target);
    planControls.target.set(hit.point.x, planControls.target.y, hit.point.z);
    ortho.position.copy(planControls.target).add(off);
    planControls.update();
  } else if (navMode === 'look') {
    turnTo(hit.point);
  } else {
    orbit.target.copy(hit.point);
    orbit.update();
  }
  invalidate();
  scheduleHash();
});
function requestHover(p) {
  if (!hoverPending) requestAnimationFrame(() => hover(hoverPending));
  hoverPending = p;
}
canvas.addEventListener('pointermove', ev => {
  if (down) return;                                       // dragging: skip the raycast
  requestHover({ x: ev.clientX, y: ev.clientY, shift: ev.shiftKey, alt: ev.altKey });
});
// Measuring: after a scroll-fly the cursor sits over a different point, and Shift / Alt change the snap.
canvas.addEventListener('wheel', ev => {
  if (state.measure && !down) requestHover({ x: ev.clientX, y: ev.clientY, shift: ev.shiftKey, alt: ev.altKey });
}, { passive: true });
for (const type of ['keydown', 'keyup']) window.addEventListener(type, ev => {
  if ((ev.key === 'Shift' || ev.key === 'Alt') && state.measure && measure.pointer && !down) {
    if (ev.key === 'Alt') ev.preventDefault();
    requestHover({ ...measure.pointer, shift: ev.shiftKey, alt: ev.altKey });
  }
});
canvas.addEventListener('pointerleave', () => {
  cursorEl.textContent = ''; cursorSub.textContent = '';
  measure.pointer = null;
  if (measure.hover && state.measure) { measure.hover = null; invalidate(); }
});
function hover(p) {
  hoverPending = null;
  if (!p) return;
  measure.pointer = p;
  const t0 = performance.now();
  const hit = state.measure ? snapAt(p.x, p.y, p.alt) : pick(p.x, p.y);
  const took = performance.now() - t0;
  if (took > 50) diag('slow-hover', { ms: Math.round(took), measure: state.measure });
  if (state.measure) {
    measure.hover = hit;
    if (hit && measure.points.length === 1) measure.preview = p.shift ? axisSnap(measure.points[0], hit.point) : hit.point;
    updateMeasure();
  }
  if (!hit) { cursorEl.textContent = ''; cursorSub.textContent = ''; return; }
  const e = hit.key && elements.get(hit.key);
  cursorEl.textContent = fmt(toIfc(hit.point), state.measure ? 3 : 2);
  cursorSub.textContent = (e ? (e.label || e.key) : '') + (state.measure ? ` · ${SNAP_NAMES[hit.kind]}` : '');
}

// ---------------------------------------------------------------- search
const searchEl = document.getElementById('search'), resultsEl = document.getElementById('results');
let results = [], active = 0;
function runSearch() {
  const q = searchEl.value.trim().toLowerCase();
  const tokens = q.split(/\s+/).filter(Boolean);
  results = [];
  if (tokens.length) {
    for (const e of elements.values()) {
      const hay = `${e.key} ${e.label || ''} ${e.class} ${e.name || ''}`.toLowerCase();
      if (!tokens.every(t => hay.includes(t))) continue;
      const k = e.key.toLowerCase(), l = (e.label || '').toLowerCase();
      const score = (k === q ? 100 : 0) + (k.startsWith(tokens[0]) ? 20 : 0) + (l.startsWith(tokens[0]) ? 15 : 0)
        + (e.label ? 5 : 0) - k.length / 100;
      results.push([score, e]);
    }
    results.sort((a, b) => b[0] - a[0]);
    results = results.slice(0, 40).map(r => r[1]);
  }
  active = 0;
  renderResults();
}
function renderResults() {
  resultsEl.hidden = !results.length;
  resultsEl.innerHTML = results.map((e, i) =>
    `<li data-i="${i}" class="${i === active ? 'active' : ''}">${esc(e.label || e.key)}<div class="sub">${esc(e.key)} · ${esc(e.class.replace(/^Ifc/, ''))} · ${esc(e.storey)}</div></li>`).join('');
  resultsEl.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
}
function choose(i) {
  const e = results[i];
  if (!e) return;
  resultsEl.hidden = true;
  searchEl.blur();
  setSelection(e.key);
  focusOn(e.key);
  scheduleHash();
}
searchEl.addEventListener('input', runSearch);
searchEl.addEventListener('focus', runSearch);
searchEl.addEventListener('blur', () => setTimeout(() => { resultsEl.hidden = true; }, 150));
searchEl.addEventListener('keydown', ev => {
  if (ev.key === 'ArrowDown') { active = Math.min(active + 1, results.length - 1); renderResults(); ev.preventDefault(); }
  if (ev.key === 'ArrowUp') { active = Math.max(active - 1, 0); renderResults(); ev.preventDefault(); }
  if (ev.key === 'Enter') choose(active);
  if (ev.key === 'Escape') { searchEl.value = ''; results = []; renderResults(); searchEl.blur(); }
  ev.stopPropagation();
});
resultsEl.addEventListener('mousedown', ev => {
  const li = ev.target.closest('li');
  if (li) choose(+li.dataset.i);
});

// ---------------------------------------------------------------- toolbar
const storeyBar = document.getElementById('storeys');
function buildStoreyChips() {
  storeyBar.innerHTML = '';
  for (const s of STOREYS) {
    const b = document.createElement('button');
    b.textContent = s;
    b.dataset.storey = s;
    b.title = `Toggle ${s} (Alt-click: only ${s})`;
    b.onclick = ev => {
      if (ev.altKey) state.storeys = new Set([s]);
      else if (state.storeys.has(s)) state.storeys.delete(s);
      else state.storeys.add(s);
      applyVisibility(); scheduleHash();
    };
    storeyBar.append(b);
  }
}
const cutSel = document.getElementById('cut'), cutHeight = document.getElementById('cutHeight');
cutSel.onchange = () => {
  state.cut = cutSel.value ? { storey: cutSel.value, height: +cutHeight.value || DEFAULT_CUT } : null;
  if (state.plan) { if (state.cut) enterPlan(state.cut.storey, toIfc(planControls.target), currentSpan()); else exitPlan(); }
  applyVisibility(); scheduleHash();
};
cutHeight.oninput = () => {
  if (!state.cut || !Number.isFinite(+cutHeight.value) || cutHeight.value === '') return;
  state.cut = { ...state.cut, height: +cutHeight.value };
  applyVisibility(); scheduleHash();
};
const btn = id => document.getElementById(id);
function setFov(value) {
  if (!FOV_PRESETS.includes(value)) return;
  persp.fov = value;
  persp.updateProjectionMatrix();
  invalidate();
}
btn('fov').onchange = ev => {
  setFov(Number(ev.target.value));
  syncToolbar(); scheduleHash();
};
btn('plan').onclick = togglePlan;
btn('measure').onclick = ev => { ev.currentTarget.blur(); setMeasure(!state.measure); };
btn('edges').onclick = () => { state.edges = !state.edges; applyVisibility(); scheduleHash(); };
btn('spaces').onclick = () => { state.spaces = !state.spaces; applyVisibility(); scheduleHash(); };
btn('plaques').onclick = () => { state.plaques = !state.plaques; applyVisibility(); scheduleHash(); };
btn('home').onclick = () => { exitPlanIfAny(); homeView(); scheduleHash(); };
btn('trueColour').onclick = toggleLook;
// Segmented control: clicking "Look" always means look, "Orbit" always orbit (a plain toggle
// switched the owner to Orbit when he clicked "Look" to make sure it was on).
btn('navMode').onclick = ev => {
  const seg = ev.target.closest('.look, .orbit');
  setNavMode(seg ? (seg.classList.contains('orbit') ? 'orbit' : 'look') : (navMode === 'look' ? 'orbit' : 'look'));
};
function toggleLook() { setLook(look === 'true' ? 'cinema' : 'true'); syncToolbar(); scheduleHash(); }
function exitPlanIfAny() { if (state.plan) exitPlan(); }
function togglePlan() {
  if (state.plan) exitPlan();
  else { const s = state.cut?.storey || groundStorey(); if (s) enterPlan(s); }
  scheduleHash();
}
function syncToolbar() {
  storeyBar.querySelectorAll('button').forEach(b => {
    const s = b.dataset.storey;
    b.classList.toggle('on', state.storeys.has(s));
    b.style.opacity = state.storeys.has(s) && !storeyVisible(s) ? 0.45 : '';
  });
  cutSel.value = state.cut?.storey || '';
  if (state.cut && document.activeElement !== cutHeight) cutHeight.value = state.cut.height;
  btn('fov').value = String(persp.fov);
  btn('fov').disabled = !!state.plan;
  btn('fov').title = state.plan ? 'Field of view applies to 3D views; leave Plan to change it'
    : 'Vertical field of view: wider angles show more of the room';
  btn('plan').classList.toggle('on', !!state.plan);
  btn('measure').classList.toggle('on', state.measure);
  btn('edges').classList.toggle('on', state.edges);
  btn('spaces').classList.toggle('on', state.spaces);
  btn('plaques').classList.toggle('on', state.plaques && galleryKeys.length > 0);
  btn('plaques').disabled = !galleryKeys.length;
  btn('plaques').title = galleryKeys.length ? `Floating titles of the ${galleryKeys.length} elements with Pset_Gallery`
    : 'No elements with Pset_Gallery';
  btn('trueColour').classList.toggle('on', look === 'true');
  btn('navMode').dataset.mode = navMode;
  btn('hint').textContent = state.measure
    ? `Click two points · Space+drag ${state.plan ? 'pan' : navMode} · right-drag pan · scroll fly · Shift axis · Alt no snap · Esc clear`
    : state.plan ? 'Drag to pan · scroll to zoom · WASD to pan · P back to 3D'
    : navMode === 'look' ? 'Drag to look around · middle-drag to orbit · right-drag to pan · scroll to fly · WASD to walk'
      : 'Drag to orbit · right-drag to pan · scroll to fly · WASD to walk · O for look';
  if (state.removed.size && !state.measure) btn('hint').textContent += ` · ${state.removed.size} removed for play (X): Z undo · Shift+Z restore all`;
  updateStats();
}
// Bottom stat bar: view, cut, selection, model. The cursor cells are filled by hover().
const statEl = id => document.getElementById(id);
function updateStats() {
  const v = state.plan ? `Plan ${state.plan}` : 'Perspective';
  statEl('stView').textContent = v;
  statEl('stViewSub').textContent = state.plan ? `north up · ${currentSpan().toFixed(1)} m span` : (look === 'true' ? 'true colour' : 'cinema light');
  statEl('stCut').innerHTML = state.cut ? `${state.cut.storey}<small>@ ${(+state.cut.height).toFixed(2)} m</small>` : 'off';
  statEl('stCutSub').textContent = state.cut ? `section at ${(ELEV[state.cut.storey] + +state.cut.height).toFixed(2)} m` : 'whole house';
  const shown = STOREYS.filter(s => storeyVisible(s));
  statEl('stStoreys').textContent = shown.join(' ') || 'none';
  const e = state.sel && elements.get(state.sel);
  statEl('stSel').textContent = e ? (e.label || e.key) : '—';
  statEl('stSelSub').textContent = e ? `${e.class.replace(/^Ifc/, '')} · ${e.storey}` : 'click an element';
  statEl('stModel').innerHTML = `${elements.size - state.removed.size}<small>elements${state.removed.size ? ` · ${state.removed.size} removed` : ''}</small>`;
  statEl('stModelSub').textContent = loadedVersion ? `build ${String(loadedVersion).slice(-8)}` : 'no build info';
}
// ---------------------------------------------------------------- WASD / arrow movement
// W/S or Up/Down: forward/back (level, like walking), A/D or Left/Right: sideways,
// Q/E: down/up, Shift: 3x faster. Camera and orbit target move together, so orbiting
// keeps working afterwards. In plan view the keys pan the map (W/Up = north).
const MOVE_KEYS = { w: [1, 0, 0], arrowup: [1, 0, 0], s: [-1, 0, 0], arrowdown: [-1, 0, 0],
  a: [0, -1, 0], arrowleft: [0, -1, 0], d: [0, 1, 0], arrowright: [0, 1, 0], q: [0, 0, -1], e: [0, 0, 1] };
const held = new Set();
const WALK_SPEED = 5;                                    // m/s, Shift x3
let moveFast = false;
window.addEventListener('keyup', ev => { held.delete(ev.key.toLowerCase()); moveFast = ev.shiftKey; });
window.addEventListener('blur', () => { held.clear(); setSpace(false); });
// Space held: left-drag navigates again while measuring (look / orbit; plan: pan).
function setSpace(on) {
  if (measureNav.space === on) return;
  measureNav.space = on;
  applyNavMode();
}
for (const type of ['keydown', 'keyup']) window.addEventListener(type, ev => {
  if (ev.code !== 'Space' || ev.target.matches?.('input, select, textarea')) return;
  if (state.measure) ev.preventDefault();                  // no page scroll, no focused-button click
  setSpace(type === 'keydown');
});
const WORLD_UP = new THREE.Vector3(0, 1, 0), _fwd = new THREE.Vector3(), _right = new THREE.Vector3(), _move = new THREE.Vector3();
function moveStep(dt) {
  if (!held.size) return;
  let f = 0, r = 0, u = 0;
  for (const k of held) { const m = MOVE_KEYS[k]; if (m) { f += m[0]; r += m[1]; u += m[2]; } }
  if (!f && !r && !u) return;
  const plan = controls === planControls;
  // Perspective: W/S fly exactly where the camera looks (up/down included); A/D strafe
  // level. Plan: pan the map, W = screen up (north).
  camera.getWorldDirection(_fwd);
  _right.copy(_fwd).setY(0);
  if (plan || _right.lengthSq() < 1e-4) _right.set(0, 1, 0).applyQuaternion(camera.quaternion).setY(0);
  _right.normalize();
  if (plan) _fwd.copy(_right);
  _right.cross(WORLD_UP).normalize();
  const speed = (moveFast ? 3 : 1) * (plan ? currentSpan() * 0.5 : WALK_SPEED);     // constant: no braking near objects
  _move.set(0, 0, 0).addScaledVector(_fwd, f).addScaledVector(_right, r);
  if (!plan) _move.addScaledVector(WORLD_UP, u);
  if (_move.lengthSq() === 0) return;
  _move.normalize().multiplyScalar(speed * dt);
  camera.position.add(_move);
  controls.target.add(_move);
}

window.addEventListener('keydown', ev => {
  if (ev.target.matches?.('input, select, textarea') || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  const k = ev.key.toLowerCase();
  moveFast = ev.shiftKey;
  if (k in MOVE_KEYS) { held.add(k); ev.preventDefault(); return; }
  if (ev.key === 'Escape') {
    if (state.measure && measure.points.length) { measure.points = []; measure.preview = null; updateMeasure(); }
    else if (state.measure) setMeasure(false);
    else setSelection(null);
  } else if (ev.key === '/') { searchEl.focus(); ev.preventDefault(); }
  else if (k === 'f' && state.sel) focusOn(state.sel);
  else if (k === 'x' && state.sel) removeSelected();
  else if (k === 'z') undoRemove(ev.shiftKey);
  else if (k === 'm') setMeasure(!state.measure);
  else if (k === 'p') togglePlan();
  else if (k === 't') toggleLook();
  else if (k === 'o') setNavMode(navMode === 'look' ? 'orbit' : 'look');
  else if (k === 'h') { exitPlanIfAny(); homeView(); scheduleHash(); }
});

// ---------------------------------------------------------------- status pills (SSE)
const buildPill = btn('buildPill'), checksPill = btn('checksPill');
const logDialog = btn('logDialog');
function setPill(el, cls, text, log) {
  el.hidden = false;
  el.className = `pill ${cls}`;
  el.textContent = text;
  el.title = text;
  el.onclick = log ? () => { btn('logTitle').textContent = text; btn('logText').textContent = log; logDialog.showModal(); } : null;
  el.style.cursor = log ? 'pointer' : 'default';
}
function showStatus(status) {
  const b = status.build || {}, c = status.checks || {};
  const changed = (b.changed || []).map(p => p.split('/').pop());
  const changedText = changed.length ? `: ${changed.slice(0, 3).join(', ')}${changed.length > 3 ? ` +${changed.length - 3}` : ''}` : '';
  if (b.state === 'building') setPill(buildPill, 'building', `Building${changedText}`, null);
  else if (b.state === 'failed') setPill(buildPill, 'failed', 'Build failed (last good model shown)', b.log || '(no log)');
  else if (b.state === 'ready') setPill(buildPill, 'ready', `Ready · ${b.seconds ?? '?'} s${changedText}`, b.log);
  else setPill(buildPill, 'idle', 'Model loaded', null);
  if (c.state === 'running') setPill(checksPill, 'running', 'Checks running', null);
  else if (c.state === 'passed') setPill(checksPill, 'passed', `Checks passed${c.summary ? ' · ' + c.summary : ''}`, c.log);
  else if (c.state === 'failed') setPill(checksPill, 'failed', `Checks failed${c.summary ? ' · ' + c.summary : ''}`, c.log);
  else if (c.state === 'stale') setPill(checksPill, 'stale', 'Checks stale', c.log);
  else checksPill.hidden = true;
}
function connectEvents() {
  const es = new EventSource('/events');
  es.onmessage = ev => {
    let status;
    try { status = JSON.parse(ev.data); } catch { return; }
    showStatus(status);
    const v = status.build?.version;
    if (v && v !== loadedVersion) ensureVersion(v);
  };
  es.onerror = () => { setPill(buildPill, 'offline', 'Server offline (reconnecting)', null); checksPill.hidden = true; };
}

// ---------------------------------------------------------------- render loop
let lastFrame = 0, renderCount = 0;
function frame() {
  requestAnimationFrame(frame);
  const now = performance.now();
  if (lastFrame && now - lastFrame > 250) diag('slow-frame', { ms: Math.round(now - lastFrame) });
  if (lastFrame) moveStep(Math.min(0.1, (now - lastFrame) / 1000));
  turnStep(now);
  lastFrame = now;
  if (controls.update()) { needsRender = true; scheduleHash(); }
  if (!needsRender) return;
  needsRender = false;
  renderCount++;
  draw();
  drawMeasureOverlay();
  drawPlaques();
}

// ---------------------------------------------------------------- snapshot mode
/** Closed-solid containment, after visibility/cuts, to diagnose unusable camera positions. */
function cameraObstructions() {
  if (!camera.isPerspectiveCamera || renderer.clippingPlanes.some(p => p.distanceToPoint(camera.position) < 0)) return [];
  const found = new Set(), local = new THREE.Vector3(), inverse = new THREE.Matrix4();
  const ray = new THREE.Raycaster(); ray.near = 1e-6;
  const directions = [[1, .013, .027], [.019, 1, .037], [.031, .011, 1]]
    .map(v => new THREE.Vector3(...v).normalize());
  for (const mesh of pickables) {
    const key = keyOf(mesh);
    if (!key || found.has(key) || ['IfcSpace', 'IfcAnnotation'].includes(classOf(mesh))) continue;
    const original = mesh.material, materials = Array.isArray(original) ? original : [original];
    if (materials.every(m => m.transparent && m.opacity < .5)) continue;
    mesh.geometry.computeBoundingBox();
    local.copy(camera.position).applyMatrix4(inverse.copy(mesh.matrixWorld).invert());
    const box = mesh.geometry.boundingBox;
    if (!box.containsPoint(local)) continue;
    // A bounding box alone wrongly reports empty cupboard compartments and the
    // space below a sloping roof. Count actual surface crossings in three rays.
    const twoSided = materials.map(m => { const copy = m.clone(); copy.side = THREE.DoubleSide; return copy; });
    mesh.material = Array.isArray(original) ? twoSided : twoSided[0];
    let votes = 0;
    try {
      for (const direction of directions) {
        ray.set(camera.position, direction);
        let last = -Infinity, crossings = 0;
        for (const hit of ray.intersectObject(mesh, false)) {
          if (hit.distance - last > 1e-5) { crossings++; last = hit.distance; }
        }
        if (crossings % 2) votes++;
      }
    } finally {
      mesh.material = original;
      twoSided.forEach(m => m.dispose());
    }
    if (votes >= 2) found.add(key);
  }
  return [...found];
}

const snapSize = { w: Math.round(+HASH.w || 1600), h: Math.round(+HASH.h || 1000) };
function snapViews() {
  const common = { ...HASH };
  delete common.snaps; delete common.snap; delete common.w; delete common.h;
  if (HASH.snaps) {
    let list = JSON.parse(HASH.snaps);
    if (!Array.isArray(list)) list = [list];
    return list.map((entry, i) => ({ name: entry.name || `snap_${i + 1}`, view: parseView({ ...common, ...entry }) }));
  }
  return [{ name: HASH.snap || 'snap', view: parseView(common) }];
}
const nextFrame = () => new Promise(r => requestAnimationFrame(() => r()));
async function runSnaps() {
  const views = snapViews();
  for (const { name, view } of views) {
    applyView(view);
    controls.update();
    const blocked = cameraObstructions();
    if (blocked.length) console.warn(`monarch warning: snap ${name}: camera at ${fmt(toIfc(camera.position))} is inside visible ${blocked.join(', ')}; move the camera or add a cut.`);
    buildAllEdges();
    draw();
    await nextFrame();
    draw();
    const shot = document.createElement('canvas');
    shot.width = canvas.width; shot.height = canvas.height;
    compositePlaques(shot);
    const blob = await new Promise(r => shot.toBlob(r, 'image/png'));
    const res = await fetch(`/api/snap?name=${encodeURIComponent(name)}`, { method: 'POST', body: blob });
    if (!res.ok) throw new Error(`POST /api/snap ${res.status}`);
    console.log(`snap ${name} posted (${blob.size} bytes)`);
  }
}

// ---------------------------------------------------------------- self tests (driven over DevTools)
/** Frame time: n synchronous redraws, GPU flushed by a 1-pixel read. shadows=true redraws the shadow map too. */
function bench(n = 30, shadows = false) {
  const gl = renderer.getContext(), px = new Uint8Array(4);
  draw(); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  const t0 = performance.now();
  for (let i = 0; i < n; i++) { if (shadows) invalidateShadows(); draw(); }
  gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  return { msPerFrame: +((performance.now() - t0) / n).toFixed(2), px: [gl.drawingBufferWidth, gl.drawingBufferHeight] };
}
/** Verify finish ordering without letting a finish show through a separate solid. */
function depthCheck() {
  const size = 256, target = new THREE.WebGLRenderTarget(size, size);
  const test = new THREE.Scene(), geometry = new THREE.PlaneGeometry(2, 2);
  const front = new THREE.MeshBasicMaterial({ color: 0xff0000 });
  const back = new THREE.MeshBasicMaterial({ color: 0x00ff00 });
  front.userData.depthRank = 11;
  back.userData.depthRank = 11; back.userData.finish = true;
  patchMaterial(front); patchMaterial(back);
  const a = new THREE.Mesh(geometry, front), b = new THREE.Mesh(geometry, back);
  test.add(a, b);
  const cam = new THREE.PerspectiveCamera(45, 1, 0.05, 3000);
  const rays = new THREE.Raycaster(), pixel = new THREE.Vector2(), px = new Uint8Array(size * size * 4);
  const previousTarget = renderer.getRenderTarget(), results = [];
  try {
    for (const gap of [0, 0.005]) for (const x of [1, 4]) {
      a.position.z = gap;
      cam.position.set(x, 0, 1); cam.lookAt(0, 0, 0); cam.updateMatrixWorld();
      renderer.setRenderTarget(target); renderer.render(test, cam);
      renderer.readRenderTargetPixels(target, 0, 0, size, size, px);
      let samples = 0, wrong = 0;
      for (let y = 4; y < size - 4; y += 2) for (let xx = 4; xx < size - 4; xx += 2) {
        pixel.set((xx + .5) / size * 2 - 1, (y + .5) / size * 2 - 1);
        rays.setFromCamera(pixel, cam);
        const hit = rays.intersectObjects([a, b])[0];
        if (!hit || Math.abs(hit.point.x) > .85 || Math.abs(hit.point.y) > .85) continue;
        const i = (y * size + xx) * 4;
        const green = px[i + 1] > px[i];
        if (green !== (gap === 0)) wrong++;
        samples++;
      }
      results.push({ gap, angle: x, samples, wrong });
    }
  } finally {
    renderer.setRenderTarget(previousTarget);
    target.dispose(); geometry.dispose(); front.dispose(); back.dispose(); invalidate();
  }
  return results;
}
function srgbToLab([r, g, b]) {                          // 0..1 sRGB -> CIE Lab (D65)
  const lin = c => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const [R, G, B] = [lin(r), lin(g), lin(b)];
  const X = (0.4124 * R + 0.3576 * G + 0.1805 * B) / 0.95047, Y = 0.2126 * R + 0.7152 * G + 0.0722 * B,
    Z = (0.0193 * R + 0.1192 * G + 0.9505 * B) / 1.08883;
  const f = t => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
  return [116 * f(Y) - 16, 500 * (f(X) - f(Y)), 200 * (f(Y) - f(Z))];
}
/** Colour fidelity: sample the canvas on a grid, raycast each sample to its surface and compare the
 * rendered sRGB with the material's authored sRGB. Returns per-surface rows and medians. */
function colourCheck({ step = 10 } = {}) {
  const edgesWas = EDGE_MATERIAL.visible, boxWas = SELECT_BOX.visible;
  EDGE_MATERIAL.visible = false; SELECT_BOX.visible = false;
  draw();
  const gl = renderer.getContext(), W = gl.drawingBufferWidth, H = gl.drawingBufferHeight;
  const px = new Uint8Array(W * H * 4);
  gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, px);
  EDGE_MATERIAL.visible = edgesWas; SELECT_BOX.visible = boxWas; invalidate();
  const r = canvas.getBoundingClientRect(), k = W / r.width, groups = new Map(), n = new THREE.Vector3(), tmp = new THREE.Color();
  const clipY = state.cut ? clipPlane.constant + 1e-4 : Infinity;
  for (let y = step / 2; y < r.height; y += step) for (let x = step / 2; x < r.width; x += step) {
    ndc.set((x / r.width) * 2 - 1, -(y / r.height) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    const hit = raycaster.intersectObjects(pickables, false).find(h => h.point.y <= clipY);
    if (!hit || !hit.face) continue;
    const m = hit.object.material;
    if (m.map || m.transparent || (m.emissive && m.emissive.getHex())) continue;
    n.copy(hit.face.normal).transformDirection(hit.object.matrixWorld);
    if (n.dot(raycaster.ray.direction) > 0) continue;     // back face (section fill)
    const X = Math.round(x * k), Y = H - 1 - Math.round(y * k);
    const vals = [];
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      const i = ((Y + dy) * W + (X + dx)) * 4; vals.push([px[i], px[i + 1], px[i + 2]]);
    }
    const mean = [0, 1, 2].map(c => vals.reduce((a, v) => a + v[c], 0) / 9);
    if (vals.some(v => v.some((c, i) => Math.abs(c - mean[i]) > 4))) continue;   // edge or texture: skip
    const facing = n.y > 0.7 ? 'up' : n.y < -0.7 ? 'down' : 'side';
    let o = hit.object; while (o && !o.userData.key) o = o.parent;
    const id = `${m.uuid}|${facing}`;
    if (!groups.has(id)) groups.set(id, { key: o?.userData.key, facing, authored: m.color.getRGB(tmp, THREE.SRGBColorSpace) && [tmp.r, tmp.g, tmp.b], samples: [] });
    groups.get(id).samples.push(mean.map(c => c / 255));
  }
  const rows = [];
  for (const g of groups.values()) {
    if (g.samples.length < 4) continue;
    const med = [0, 1, 2].map(c => g.samples.map(s => s[c]).sort((a, b) => a - b)[g.samples.length >> 1]);
    const A = srgbToLab(g.authored), R = srgbToLab(med);
    const cA = Math.hypot(A[1], A[2]), cR = Math.hypot(R[1], R[2]);
    let dh = null;
    if (cA > 8 && cR > 2) { dh = (Math.atan2(R[2], R[1]) - Math.atan2(A[2], A[1])) * 180 / Math.PI; dh = ((dh + 540) % 360) - 180; }
    rows.push({ key: g.key, facing: g.facing, n: g.samples.length,
      authored: '#' + g.authored.map(c => Math.round(c * 255).toString(16).padStart(2, '0')).join(''),
      rendered: '#' + med.map(c => Math.round(c * 255).toString(16).padStart(2, '0')).join(''),
      dL: +(R[0] - A[0]).toFixed(1), dC: +(cR - cA).toFixed(1), dHue: dh == null ? null : +dh.toFixed(1),
      dE: +Math.hypot(R[0] - A[0], R[1] - A[1], R[2] - A[2]).toFixed(1),
      dEab: +Math.hypot(R[1] - A[1], R[2] - A[2]).toFixed(1) });
  }
  rows.sort((a, b) => b.n - a.n);
  const median = (f, list = rows) => { const v = list.map(f).filter(x => x != null).sort((a, b) => a - b); return v.length ? v[v.length >> 1] : null; };
  return { look, samples: rows.reduce((a, r2) => a + r2.n, 0), median: { dE: median(r2 => r2.dE), dL: median(r2 => r2.dL),
    dC: median(r2 => r2.dC), absHue: median(r2 => r2.dHue == null ? null : Math.abs(r2.dHue)) }, rows };
}

// ---------------------------------------------------------------- boot
async function boot() {
  if (SNAP) document.body.classList.add('snap');
  window.addEventListener('resize', resize);
  resize();
  setLook(parseView(HASH).look || 'cinema');
  let version = '';
  // A fresh checkout has no export yet. Wait for the completed first build;
  // fetching immediately previously left the viewer permanently on a 404.
  const deadline = performance.now() + 120000;
  while (loadedVersion === null) {
    try {
      const status = await (await fetch('/api/status')).json();
      if (status.build?.state === 'failed') throw new Error(status.build.log || 'Model build failed');
      version = status.build?.version || '';
      if (version) await loadModel(version);
    } catch (error) {
      loadingVersion = null;
      if (performance.now() >= deadline) throw error;
      loadingEl.textContent = `Waiting for model… ${String(error)}`;
    }
    if (loadedVersion === null) {
      if (performance.now() >= deadline) throw new Error('Timed out waiting for the model build.');
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  }
  if (SNAP) {
    try { await runSnaps(); } catch (err) { console.error('snap failed', err); window.__monarchSnapError = String(err); }
    window.__monarchReady = true;
    window.__monarchSnapDone = true;
    return;
  }
  applyView(parseView(HASH));
  lastHash = location.hash;
  requestAnimationFrame(frame);
  await nextFrame();
  window.__monarch = { state, orbit, get version() { return loadedVersion; }, get camera() { return camera; }, applyView, parseView,
    hash: currentHash, showStatus, setLook, bench, colourCheck, depthCheck, cameraObstructions, LOOKS, bloomPass, sun, hemi,
    get renderCount() { return renderCount; }, invalidate, scene, THREE, measure, snapAt, setMeasure, MEASURE_CLICK, toScreen: v => { canvasRect = canvas.getBoundingClientRect(); return toScreen(v); },
    get storeys() { return { STOREYS, FLOORS, ELEV, RANK }; }, get house() { return houseName; }, get galleryKeys() { return galleryKeys; },
    reload: () => ensureVersion(`${loadedVersion}-r${Date.now()}`) };   // test the hot-reload path
  window.__monarchReady = true;
  connectEvents();
}
boot().catch(err => {
  console.error('viewer boot failed', err);
  loadingEl.textContent = `Viewer failed: ${err}`;
  window.__monarchSnapError = String(err);
  window.__monarchSnapDone = true;
});
