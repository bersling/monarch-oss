// End-to-end check of the hosted viewer (site/) with the example house, headless Chrome over the
// DevTools protocol, no npm dependencies (node >= 22 for the built-in WebSocket).
//
// Usage:
//   ./build.sh                                   # or any IFC you want to test with
//   D=$(mktemp -d) && cp -R site/. "$D" && cp generated/casa_monarch.ifc "$D"/casa_monarch.ifc
//   python3 -m http.server 8080 -d "$D" &        # file:// does not work (ES modules + wasm)
//   node site/test_site.mjs http://127.0.0.1:8080/ [screenshot.png]
//
// Asserts: the example loads, > 100 elements, storey chips name every IfcBuildingStorey, no floating
// labels, Look navigation by default with a tutorial card (arrows + wheel move), selecting a gallery exhibit shows Pset_Gallery in the panel, a
// storey toggle hides its elements, X / Z / Shift+Z remove and restore, FOV presets, Plan (orthographic, north up,
// cut at floor + 1.2 m), Measure (two clicks, label, snapping, Esc), no overflow at 390 px, and no JavaScript errors.
// With a screenshot path it also writes <name>-plan-gf.png, <name>-measure.png, <name>-cut-3d.png and <name>-phone-390(-plan).png.
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
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`DevTools timeout: ${method}`)); }, 30000);
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

  // Look is the default; the tutorial card shows once; arrow keys and the wheel move the camera.
  assert.equal(await evaluate('window.__viewer.state.nav'), 'look');
  assert.equal(await evaluate('document.getElementById("tutorial").hidden'), false);
  const camPos = () => evaluate('window.__viewer.camera.position.toArray()');
  const p0 = await camPos();
  await evaluate('window.dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowUp"}))');
  await delay(800);
  await evaluate('window.dispatchEvent(new KeyboardEvent("keyup", {key: "ArrowUp"}))');
  const p1 = await camPos();
  assert.ok(Math.hypot(...p1.map((v, i) => v - p0[i])) > 0.3, `ArrowUp moved ${JSON.stringify([p0, p1])}`);
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
    const shot = await cdp('Page.captureScreenshot', { format: 'png' });
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
  await delay(300);
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
