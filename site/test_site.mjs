// End-to-end check of the hosted viewer (site/) with the example house, headless Chrome over the
// DevTools protocol, no npm dependencies (node >= 22 for the built-in WebSocket).
//
// Usage:
//   ./build.sh                                   # or any IFC you want to test with
//   D=$(mktemp -d) && cp -R site/. "$D" && cp generated/casa_monarch.ifc "$D"/casa_monarch.ifc && cp -R generated/textures "$D"/
//   python3 -m http.server 8080 -d "$D" &        # file:// does not work (ES modules + wasm)
//   node site/test_site.mjs http://127.0.0.1:8080/ [screenshot.png]
//
// Asserts: the example loads, > 100 elements, storey chips name every IfcBuildingStorey, no floating
// labels, Look navigation by default with a tutorial card (arrows + wheel move), selecting a gallery exhibit shows Pset_Gallery in the panel, a
// storey toggle hides its elements, X / Z / Shift+Z remove and restore, FOV presets, Plan (orthographic, north up,
// cut at floor + 1.2 m), Measure (two clicks, label, snapping, Esc), Night (lamps from IfcLightFixture + Pset_Light,
// panel / L / list switches, lumen preview), no overflow at 390 px, and no JavaScript errors.
// With a screenshot path it also writes <name>-plan-gf.png, <name>-measure.png, <name>-cut-3d.png, <name>-night-*.png
// (outside, living, bedroom, entrance, phone-390) and <name>-phone-390(-plan).png.
// MONARCH_CHROME overrides the Chrome binary.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const base = process.argv[2];
const screenshot = process.argv[3];
if (!base) throw new Error('Usage: node site/test_site.mjs http://127.0.0.1:PORT/ [screenshot.png]');
const page = new URL(base);
if (!['127.0.0.1', 'localhost'].includes(page.hostname)) throw new Error('Use a local server');

const profile = await mkdtemp(join(tmpdir(), 'monarch-site-test-'));
const chrome = spawn(process.env.MONARCH_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ['--headless=new', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--remote-debugging-port=0',
    `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check', '--disable-extensions', 'about:blank'],
  { stdio: ['ignore', 'ignore', 'pipe'] });
let stderr = '', ws;
chrome.stderr.on('data', b => { stderr += b; });
const pending = new Map(), errors = [];
let sequence = 0;
try {
  let port;
  const deadline = Date.now() + 20000;
  while (!port) {
    try { port = Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]); } catch { /* starting */ }
    if (chrome.exitCode != null || Date.now() > deadline) throw new Error(`Chrome failed to start: ${stderr.slice(-3000)}`);
    if (!port) await delay(100);
  }
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' })).json();
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  ws.onmessage = ev => {
    const message = JSON.parse(ev.data);
    if (message.id) {
      const p = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) p?.reject(new Error(JSON.stringify(message.error)));
      else p?.resolve(message.result);
    } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text + ' ' + (message.params.exceptionDetails.exception?.description || ''));
    else if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error')
      errors.push(message.params.args.map(a => a.value || a.description).join(' '));
  };
  const cdp = (method, params = {}, timeout = 30000) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`DevTools timeout: ${method}`)); }, timeout);
    pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value); }, reject: error => { clearTimeout(timer); reject(error); } });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const response = await cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
    return response.result.value;
  };
  const until = async (expression, timeout = 120000) => {
    const end = Date.now() + timeout;
    while (!await evaluate(expression)) {
      if (errors.length) throw new Error(errors.join('\n'));
      if (Date.now() > end) throw new Error(`Timeout: ${expression}`);
      await delay(100);
    }
  };
  await cdp('Runtime.enable');
  await cdp('Page.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1500, height: 950, deviceScaleFactor: 1, mobile: false });
  await cdp('Page.navigate', { url: page.href });
  await until('window.__viewerReady === true && !!window.__viewer');
  console.log('PASS page and web-ifc wasm ready');

  // "Load the example house" fetches ./casa_monarch.ifc next to the page.
  const t0 = Date.now();
  await evaluate('document.getElementById("exampleHero").click()');
  await until('window.__viewer.state.modelID !== null && window.__viewer.state.parseMs > 0 && document.getElementById("loading").hidden !== false');
  const wall = Date.now() - t0;
  const parseMs = await evaluate('Math.round(window.__viewer.state.parseMs)');
  const stats = await evaluate(`({
    elements: [...window.__viewer.state.elements.values()].filter(e => !e.isSpace).length,
    spaces: [...window.__viewer.state.elements.values()].filter(e => e.isSpace).length,
    tris: window.__viewer.state.tris,
    shown: document.getElementById("stElements").textContent,
  })`);
  assert.ok(stats.elements > 100, JSON.stringify(stats));
  assert.equal(stats.shown, String(stats.elements));
  console.log(`PASS example house loaded: ${stats.elements} elements (+${stats.spaces} spaces), ${stats.tris} triangles, parse ${parseMs} ms, click-to-ready ${wall} ms`);

  const storeys = await evaluate('window.__viewer.state.storeys.map(s => ({name: s.name, count: s.count}))');
  const chips = await evaluate('[...document.querySelectorAll("#storeys button")].map(b => b.textContent)');
  for (const name of ['Ground floor', 'Upper floor']) {
    assert.ok(storeys.some(s => s.name === name), JSON.stringify(storeys));
    assert.ok(chips.some(c => c.includes(name)), JSON.stringify(chips));
  }
  assert.equal(await evaluate('document.getElementById("storeyGroup").hidden'), false);
  console.log(`PASS storeys: ${storeys.map(s => `${s.name} (${s.count})`).join(', ')}`);

  assert.equal(await evaluate('document.querySelectorAll("#labels, .plaque, #plaques").length'), 0);
  console.log('PASS no floating labels');

  // Image textures (IfcImageTexture next to the IFC) are mapped and loaded: plaques and pictures are not blank.
  await until('[...window.__viewer.state.materials.values()].filter(m => m.map).length > 0 && [...window.__viewer.state.materials.values()].filter(m => m.map).every(m => m.map.image?.width > 0)', 20000);
  const texCount = await evaluate('[...window.__viewer.state.materials.values()].filter(m => m.map).length');
  assert.ok(texCount >= 9, `textures: ${texCount}`);
  console.log(`PASS ${texCount} image textures loaded`);

  // Look is the default; the tutorial card shows once; arrow keys and the wheel move the camera.
  assert.equal(await evaluate('window.__viewer.state.nav'), 'look');
  assert.equal(await evaluate('document.getElementById("tutorial").hidden'), false);
  const camPos = () => evaluate('window.__viewer.camera.position.toArray()');
  const p0 = await camPos();
  await evaluate('window.dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowUp"}))');
  // Hold until the camera moved: headless Chrome renders frames irregularly.
  await until(`Math.hypot(...window.__viewer.persp.position.toArray().map((v, i) => v - ${JSON.stringify(p0)}[i])) > 0.05`, 30000).catch(() => {});
  await evaluate('window.dispatchEvent(new KeyboardEvent("keyup", {key: "ArrowUp"}))');
  const p1 = await camPos();
  assert.ok(Math.hypot(...p1.map((v, i) => v - p0[i])) > 0.05, `ArrowUp moved ${JSON.stringify([p0, p1])}`);
  assert.equal(await evaluate('document.getElementById("tutorial").hidden'), true);
  await evaluate('{ const c=document.querySelector("canvas"), r=c.getBoundingClientRect(); c.dispatchEvent(new WheelEvent("wheel", {deltaY: -100, clientX: r.left + r.width/2, clientY: r.top + r.height/2, bubbles: true, cancelable: true})); }');
  const p2 = await camPos();
  assert.ok(Math.abs(Math.hypot(...p2.map((v, i) => v - p1[i])) - 1) < 0.05, `wheel moved ${JSON.stringify([p1, p2])}`);
  console.log('PASS look by default, tutorial card, arrow keys and wheel move the camera');

  // Select a gallery exhibit programmatically: the panel must list Pset_Gallery with its prompt.
  const shelf = await evaluate('[...window.__viewer.state.elements.values()].find(e => e.name === "GAL_SHELF")?.id');
  assert.ok(shelf, 'exhibit GAL_SHELF is in the model');
  await evaluate(`window.__viewer.setSelection(${shelf})`);
  assert.equal(await evaluate('window.__viewer.state.sel'), shelf);
  assert.equal(await evaluate('document.getElementById("panel").hidden'), false);
  await until('!document.getElementById("psetWait") && /Pset_Gallery/.test(document.getElementById("panelBody").textContent)');
  const panel = await evaluate('document.getElementById("panelBody").textContent');
  for (const key of ['Title', 'Provenance', 'Prompt']) assert.ok(panel.includes(key), `panel lists ${key}`);
  assert.ok(/IfcFurnishingElement|Ifc\w+/.test(panel), panel.slice(0, 200));
  console.log('PASS click-to-select: property panel shows Pset_Gallery (Title, Provenance, Prompt)');

  // Storey toggle hides every element of that storey and restores on the second click.
  const upper = await evaluate('window.__viewer.state.storeys.find(s => s.name === "Upper floor").id');
  const visibleOf = () => evaluate(`[...window.__viewer.state.elements.values()].filter(e => e.storey === ${upper}).map(e => e.group.visible)`);
  assert.ok((await visibleOf()).every(Boolean));
  await evaluate(`[...document.querySelectorAll("#storeys button")].find(b => b.textContent.includes("Upper floor")).click()`);
  assert.equal(await evaluate(`window.__viewer.state.storeyVisible.get(${upper})`), false);
  assert.ok((await visibleOf()).every(v => v === false), 'upper floor elements hidden');
  await evaluate(`[...document.querySelectorAll("#storeys button")].find(b => b.textContent.includes("Upper floor")).click()`);
  assert.ok((await visibleOf()).every(Boolean), 'upper floor elements shown again');
  console.log('PASS storey toggle hides and restores the upper floor');

  // Search finds an exhibit by name.
  await evaluate('{ const s=document.getElementById("search"); s.value="plinth"; s.dispatchEvent(new Event("input", {bubbles:true})); }');
  await until('document.querySelectorAll("#results li").length > 0', 5000);
  console.log(`PASS search: ${await evaluate('document.querySelectorAll("#results li").length')} hits for "plinth"`);
  await evaluate('{ const s=document.getElementById("search"); s.value=""; s.dispatchEvent(new Event("input", {bubbles:true})); }');

  const key = (k, extra = '') => evaluate(`window.dispatchEvent(new KeyboardEvent("keydown", {key: "${k}"${extra}})); window.dispatchEvent(new KeyboardEvent("keyup", {key: "${k}"${extra}}))`);
  const shotPath = suffix => screenshot && screenshot.replace(/(\.png)?$/i, `-${suffix}.png`);
  const capture = async suffix => {
    if (!screenshot) return;
    await delay(1200);
    const shot = await cdp('Page.captureScreenshot', { format: 'png' }, 240000);   // software GL at night is slow
    await writeFile(shotPath(suffix), Buffer.from(shot.data, 'base64'));
    console.log(`screenshot ${shotPath(suffix)}`);
  };

  // X removes the selection for the session, Z undoes (and re-selects), Shift+Z restores all.
  const shelfVisible = () => evaluate(`window.__viewer.state.elements.get(${shelf}).group.visible`);
  await evaluate(`window.__viewer.setSelection(${shelf})`);
  await key('x');
  assert.equal(await shelfVisible(), false, 'X hides the selection');
  assert.equal(await evaluate('window.__viewer.state.sel'), null);
  assert.ok(/1 removed/.test(await evaluate('document.getElementById("stElements").textContent')));
  assert.ok(/Z undo/.test(await evaluate('document.getElementById("hint").textContent')));
  await key('z');
  assert.equal(await shelfVisible(), true, 'Z restores it');
  assert.equal(await evaluate('window.__viewer.state.sel'), shelf, 'Z re-selects it');
  const plinths = await evaluate('[...window.__viewer.state.elements.values()].filter(e => /plinth/i.test(e.name)).slice(0, 2).map(e => e.id)');
  for (const id of [shelf, ...plinths]) { await evaluate(`window.__viewer.setSelection(${id})`); await key('x'); }
  assert.equal(await evaluate('window.__viewer.state.removed.size'), 1 + plinths.length);
  await key('Z', ', shiftKey: true');
  assert.equal(await evaluate('window.__viewer.state.removed.size'), 0, 'Shift+Z restores all');
  assert.equal(await shelfVisible(), true);
  assert.equal(await evaluate('document.getElementById("hint").textContent'), '');
  console.log(`PASS X removes the selection, Z undoes, Shift+Z restores all (${1 + plinths.length} removed)`);

  // Field of view presets (default 45).
  assert.equal(await evaluate('window.__viewer.camera.fov'), 45);
  assert.deepEqual(await evaluate('[...document.getElementById("fov").options].map(o => +o.value)'), [30, 45, 60, 75]);
  await evaluate('{ const f=document.getElementById("fov"); f.value="75"; f.dispatchEvent(new Event("change", {bubbles:true})); }');
  assert.equal(await evaluate('window.__viewer.camera.fov'), 75);
  assert.ok(Math.abs(await evaluate('window.__viewer.camera.projectionMatrix.elements[5]') - 1 / Math.tan(75 / 2 * Math.PI / 180)) < 1e-6);
  await evaluate('{ const f=document.getElementById("fov"); f.value="45"; f.dispatchEvent(new Event("change", {bubbles:true})); }');
  assert.equal(await evaluate('window.__viewer.camera.fov'), 45);
  console.log('PASS field of view presets 30/45/60/75, default 45');

  // Plan (P): orthographic, straight down, north (IFC +y = three -z) up, cut at Ground floor + 1.2 m.
  await evaluate('window.__viewer.setSelection(null)');
  await key('p');
  const plan = await evaluate(`(() => { const v = window.__viewer, c = v.camera, d = c.getWorldDirection(new c.position.constructor());
    const gf = v.state.storeys.find(s => s.name === "Ground floor");
    return { ortho: c.isOrthographicCamera, dir: d.toArray(), up: c.up.toArray(), plan: v.state.plan, gf: gf.id, elev: gf.elevation,
      clip: v.renderer.clippingPlanes.length, constant: v.clipPlane.constant, fovDisabled: document.getElementById("fov").disabled,
      upperHidden: [...v.state.elements.values()].filter(e => e.storey === ${upper}).every(e => !e.group.visible),
      cutValue: document.getElementById("cut").value }; })()`);
  assert.ok(plan.ortho, 'plan camera is orthographic');
  assert.ok(plan.dir[1] < -0.999, `looks straight down ${plan.dir}`);
  assert.deepEqual(plan.up, [0, 0, -1]);
  assert.equal(plan.plan, plan.gf);
  assert.equal(plan.clip, 1);
  assert.ok(Math.abs(plan.constant - (plan.elev + 1.2)) < 1e-9, `clip at ${plan.constant}`);
  assert.ok(plan.upperHidden && plan.fovDisabled && plan.cutValue === String(plan.gf), JSON.stringify(plan));
  console.log(`PASS plan view: orthographic top-down, north up, cut at Ground floor ${plan.elev} + 1.2 m, upper floor hidden`);
  await capture('plan-gf');

  // Measure (M) in plan: two Alt-clicks (no snap) 300 px apart give 300 px worth of metres in plan.
  await evaluate('window.__viewer.MEASURE_CLICK.ms = 10000');      // headless frames are slow
  await key('m');
  assert.equal(await evaluate('window.__viewer.state.measure'), true);
  const view = await evaluate('(() => { const c = document.querySelector("canvas").getBoundingClientRect(), o = window.__viewer.camera; return { cx: c.left + c.width / 2, cy: c.top + c.height / 2, mPerPx: (o.top - o.bottom) / o.zoom / c.height }; })()');
  const click = async (x, y) => {
    for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased'])
      await cdp('Input.dispatchMouseEvent', { type, x, y, button: type === 'mouseMoved' ? 'none' : 'left', buttons: type === 'mousePressed' ? 1 : 0, clickCount: 1, modifiers: 1 });
  };
  await click(view.cx - 150, view.cy + 40);
  await click(view.cx + 150, view.cy + 40);
  const m = await evaluate('(() => { const v = window.__viewer, [a, b] = v.measure.points; return a && b ? { a: v.toIfc(a), b: v.toIfc(b), d: a.distanceTo(b) } : null; })()');
  assert.ok(m, 'two measure points placed');
  const horiz = Math.hypot(m.b[0] - m.a[0], m.b[1] - m.a[1]);
  assert.ok(Math.abs(horiz - 300 * view.mPerPx) < 0.03, `measured ${horiz} vs ${300 * view.mPerPx}`);
  assert.ok(Math.abs(m.b[1] - m.a[1]) < 0.03, 'same screen row = same northing');
  // The label is positioned in the render loop; wait for a frame instead of a fixed delay.
  await until('!document.getElementById("measureLabel").hidden && document.getElementById("measureLabel").textContent !== ""', 5000).catch(() => {});
  const label = await evaluate('(() => { const l = document.getElementById("measureLabel"); return { hidden: l.hidden, text: l.textContent, box: document.getElementById("measureBox").textContent }; })()');
  assert.ok(!label.hidden && label.text === `${m.d.toFixed(2)} m`, JSON.stringify(label));
  assert.ok(label.box.includes(`${m.d.toFixed(3)} m`));
  console.log(`PASS measure: two clicks ${horiz.toFixed(3)} m apart (expected ${(300 * view.mPerPx).toFixed(3)}), label "${label.text}"`);
  const snapped = await evaluate(`(() => { const v = window.__viewer, kinds = new Set();
    for (let x = -200; x <= 200; x += 8) for (let y = -120; y <= 120; y += 40) { const h = v.snapAt(${view.cx} + x, ${view.cy} + y); if (h) kinds.add(h.kind); }
    return [...kinds]; })()`);
  assert.ok(snapped.includes('vertex') && snapped.includes('edge'), `snap kinds ${snapped}`);
  console.log(`PASS measure snapping finds ${snapped.join(', ')}`);
  await capture('measure');
  await key('Escape');
  assert.equal(await evaluate('window.__viewer.measure.points.length'), 0, 'Esc clears');
  assert.equal(await evaluate('window.__viewer.state.measure'), true);
  await key('Escape');
  assert.equal(await evaluate('window.__viewer.state.measure'), false, 'second Esc leaves Measure');

  // Leaving plan keeps the cut (as web/viewer.js); Cut off removes the clip plane.
  await key('p');
  assert.equal(await evaluate('window.__viewer.camera.isPerspectiveCamera'), true);
  assert.equal(await evaluate('window.__viewer.renderer.clippingPlanes.length'), 1, 'the cut stays in 3D');
  await evaluate('window.__viewer.homeView()');
  await capture('cut-3d');
  await evaluate('{ const c=document.getElementById("cut"); c.value=""; c.dispatchEvent(new Event("change", {bubbles:true})); }');
  assert.equal(await evaluate('window.__viewer.renderer.clippingPlanes.length'), 0);
  assert.ok((await visibleOf()).every(Boolean), 'upper floor back after Cut off');
  console.log('PASS measure Esc clears / leaves, P back to 3D, Cut off restores the whole house');

  // Night (N): every IfcLightFixture with a Pset_Light becomes a three.js light from its photometry;
  // switching a lamp (panel button, L, lamp list) changes its light and its glow.
  const lamps = await evaluate(`[...window.__viewer.state.lamps.values()].map(L => ({ id: L.id, name: window.__viewer.state.elements.get(L.id).name,
    type: window.__viewer.state.elements.get(L.id).type, lm: +L.pset.LuminousFlux_lm, k: +L.pset.ColourTemperature_K, kind: L.pset.Distribution, glow: L.glow.length }))`);
  assert.ok(lamps.length >= 15, `lamps: ${lamps.length}`);
  assert.ok(lamps.every(l => l.lm > 0 && l.k >= 1800 && l.k <= 6500), JSON.stringify(lamps));
  assert.ok(lamps.every(l => l.type === 'IfcLightFixture'), JSON.stringify(lamps.map(l => l.type)));
  assert.ok(lamps.some(l => l.kind === 'spot') && lamps.some(l => l.kind === 'point'));
  assert.ok(lamps.filter(l => l.glow > 0).length >= 10, 'diffusers glow');
  assert.ok(lamps.some(l => /^SITE_BOLLARD/.test(l.name)) && lamps.some(l => /^OUT_/.test(l.name)), 'outdoor lamps');
  const lampState = name => evaluate(`(() => { const v = window.__viewer, L = [...v.state.lamps.values()].find(L => v.state.elements.get(L.id).name === "${name}");
    const lights = v.LAMPS.children.filter(o => o.isLight && o.userData.id === L.id);
    const g = L.glow[0]?.mesh, m = g && (g.userData.orig || g.material);
    return { id: L.id, on: L.on, intensity: lights.reduce((a, l) => a + l.intensity, 0), lights: lights.length, glow: m ? m.emissive.r + m.emissive.g + m.emissive.b : null }; })()`);
  assert.equal(await evaluate('window.__viewer.LAMPS.visible'), false, 'no lamp lights by day');
  assert.equal((await lampState('LIV_FLOOR_LAMP')).intensity, 0);
  await key('n');
  assert.equal(await evaluate('window.__viewer.state.night'), true);
  assert.ok(await evaluate('document.getElementById("night").classList.contains("on") && window.__viewer.LAMPS.visible && window.__viewer.renderer.toneMapping !== 0'));
  const lit = await lampState('LIV_FLOOR_LAMP');
  assert.ok(lit.intensity > 0 && lit.lights === 2 && lit.glow > 0, JSON.stringify(lit));
  const shadowSpots = await evaluate('window.__viewer.LAMPS.children.filter(o => o.isSpotLight && o.castShadow).length');
  assert.ok(shadowSpots >= 1 && shadowSpots <= await evaluate('window.__viewer.NIGHT.maxShadowSpots'), `shadow spots ${shadowSpots}`);
  assert.equal(await evaluate('document.getElementById("lamps").hidden'), false, 'lamp list shows at night');
  assert.ok(/Night · \d+ of \d+ lamps on/.test(await evaluate('document.getElementById("hint").textContent')));
  await evaluate(`window.__viewer.setSelection(${lit.id})`);
  assert.ok(/Light off/.test(await evaluate('document.getElementById("lampBox").textContent')), 'panel has the lamp switch');
  await evaluate('document.querySelector("#lampBox [data-act=lamp]").click()');
  const off = await lampState('LIV_FLOOR_LAMP');
  assert.ok(!off.on && off.intensity === 0 && off.glow === 0, JSON.stringify(off));
  assert.ok(/Light on/.test(await evaluate('document.getElementById("lampBox").textContent')));
  await key('l');
  assert.ok((await lampState('LIV_FLOOR_LAMP')).intensity > 0, 'L switches it back on');
  // In-session editor: lumen slider changes the light (preview only, nothing is saved).
  const before = (await lampState('LIV_FLOOR_LAMP')).intensity;
  await evaluate('{ const r = document.querySelector("#lampBox input[type=range][data-lp=lumen]"); r.value = 1600; r.dispatchEvent(new Event("input", {bubbles: true})); }');
  const after = (await lampState('LIV_FLOOR_LAMP')).intensity;
  assert.ok(Math.abs(after / before - 2) < 0.05, `lumen 800 -> 1600 doubles the light: ${before} -> ${after}`);
  await evaluate('document.querySelector("#lampBox [data-act=lampReset]").click()');
  assert.ok(Math.abs((await lampState('LIV_FLOOR_LAMP')).intensity - before) < 1e-9, 'Reset restores the modelled lumen');
  await evaluate('window.__viewer.setSelection(null)');
  // Lamp list switch.
  const bollard = lamps.find(l => /^SITE_BOLLARD/.test(l.name));
  await evaluate(`document.querySelector('#lamps [data-sw="${bollard.id}"]').click()`);
  assert.equal((await lampState(bollard.name)).intensity, 0, 'list switch turns the bollard off');
  await evaluate(`document.querySelector('#lamps [data-sw="${bollard.id}"]').click()`);
  assert.ok((await lampState(bollard.name)).intensity > 0);
  console.log(`PASS night: ${lamps.length} lamps with photometry (${lamps.filter(l => l.kind === 'spot').length} spots, ${shadowSpots} with shadows), panel/L/list switches and the lumen editor change the light`);
  // Night screenshots: outside from the south-east, inside the living room.
  const view3 = (cam, target, fov = 45) => evaluate(`(() => { const v = window.__viewer; v.setFov(${fov});
    v.persp.position.set(${cam[0]}, ${cam[2]}, ${-cam[1]}); v.orbit.target.set(${target[0]}, ${target[2]}, ${-target[1]}); v.persp.lookAt(v.orbit.target); v.orbit.update(); return v.frames; })()`);
  const settle = async () => { const f = await evaluate('window.__viewer.frames'); await until(`window.__viewer.frames > ${f + 3}`, 120000); };
  await evaluate('document.querySelector("#lamps [data-fold]").click()');            // the list folds to its header
  assert.ok(await evaluate('document.getElementById("lamps").classList.contains("folded") && !document.querySelector("#lamps .lamp").offsetParent'));
  await view3([13, -13, 4], [5, 3, 1.5]); await settle(); await capture('night-outside');
  await view3([7.2, 0.9, 1.6], [1.5, 3.2, 0.9], 60); await settle(); await capture('night-living');
  await evaluate('document.querySelector("#lamps [data-fold]").click()');
  await view3([4.6, 0.6, 4.6], [2.4, 3.4, 3.5], 60); await settle(); await capture('night-bedroom');
  await view3([6.2, 17.5, 3.0], [6.8, 8.0, 1.2]); await settle(); await capture('night-entrance');
  await evaluate('window.__viewer.setFov(45)');
  // Phone width at night: the lamp list stays inside the screen.
  await cdp('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await until('document.getElementById("lamps").getBoundingClientRect().width < 390', 5000);
  const lampsBox = await evaluate('(() => { const r = document.getElementById("lamps").getBoundingClientRect(); return { left: r.left, right: r.right, sw: document.documentElement.scrollWidth }; })()');
  assert.ok(lampsBox.left >= 0 && lampsBox.right <= 390 && lampsBox.sw <= 390, JSON.stringify(lampsBox));
  await capture('night-phone-390');
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1500, height: 950, deviceScaleFactor: 1, mobile: false });
  await key('n');
  assert.ok(await evaluate('!window.__viewer.state.night && !window.__viewer.LAMPS.visible && window.__viewer.renderer.toneMapping === 0 && document.getElementById("lamps").hidden'));
  assert.equal((await lampState('LIV_FLOOR_LAMP')).glow, 0, 'no glow by day');
  console.log('PASS night look off again (N): day lighting, no lamp lights, lamp list hidden; lamp list fits a 390 px phone');

  // Phone width: the toolbar wraps without horizontal overflow.
  await cdp('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await delay(500);
  const phone = await evaluate('({ sw: document.documentElement.scrollWidth, right: Math.max(...[...document.querySelectorAll("#bar *")].filter(e => e.offsetParent).map(e => e.getBoundingClientRect().right)) })');
  assert.ok(phone.sw <= 390 && phone.right <= 390, JSON.stringify(phone));
  console.log(`PASS phone width 390 px: no horizontal overflow (rightmost toolbar edge ${Math.round(phone.right)} px)`);
  await evaluate('window.__viewer.homeView()');
  await capture('phone-390');
  await key('p');
  await capture('phone-390-plan');
  await key('p');
  await evaluate('window.__viewer.setCut(null)');
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1500, height: 950, deviceScaleFactor: 1, mobile: false });

  assert.deepEqual(errors, []);
  console.log('PASS no JavaScript errors');

  if (screenshot) {
    await evaluate('window.__viewer.setSelection(null); window.__viewer.homeView()');
    await delay(1500);
    const shot = await cdp('Page.captureScreenshot', { format: 'png' });
    await writeFile(screenshot, Buffer.from(shot.data, 'base64'));
    console.log(`screenshot ${screenshot}`);
  }
} finally {
  ws?.close();
  chrome.kill('SIGTERM');
  await Promise.race([new Promise(resolve => chrome.once('exit', resolve)), delay(5000)]);
  if (chrome.exitCode == null) chrome.kill('SIGKILL');
  await rm(profile, { recursive: true, force: true });
}
