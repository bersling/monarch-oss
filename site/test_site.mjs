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
// storey toggle hides its elements, and no JavaScript errors were logged. Prints the parse time.
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
  await delay(400);
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
