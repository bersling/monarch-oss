// Real viewer regressions using Chrome's built-in DevTools protocol, no npm dependencies.
// Start ./open.sh --no-browser --wait, then: node tests/browser_viewer.mjs http://127.0.0.1:PORT/
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileHref, linkify } from '../web/document_links.js';

const base = process.argv[2];
if (!base) throw new Error('Usage: node tests/browser_viewer.mjs http://127.0.0.1:PORT/');
const viewer = new URL(base);
if (!['127.0.0.1', 'localhost'].includes(viewer.hostname)) throw new Error('Use a local viewer');
assert.equal(fileHref('../felsenhofstrasse/werkvertraege/Haus Nord.pdf'), '/files/felsenhofstrasse/werkvertraege/Haus%20Nord.pdf');
assert.match(linkify('  docs/viewer.md  '), /^  <a .*<\/a>  $/);
assert.match(linkify('../felsenhofstrasse/plaene/A.pdf'), /^<a /);
assert.equal(linkify('<img src=x onerror=alert(1)>'), '&lt;img src=x onerror=alert(1)&gt;');
assert.equal((linkify('https://host.test/plans/A.pdf').match(/<a /g) || []).length, 1);

const profile = await mkdtemp(join(tmpdir(), 'monarch-browser-test-'));
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
    } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
    else if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error')
      errors.push(message.params.args.map(a => a.value || a.description).join(' '));
  };
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`DevTools timeout: ${method}`)); }, 15000);
    pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value); }, reject: error => { clearTimeout(timer); reject(error); } });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const response = await cdp('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
    return response.result.value;
  };
  const until = async (expression, timeout = 60000) => {
    const end = Date.now() + timeout;
    while (!await evaluate(expression)) {
      if (errors.length) throw new Error(errors.join('\n'));
      if (Date.now() > end) throw new Error(`Timeout: ${expression}`);
      await delay(100);
    }
  };
  await cdp('Runtime.enable');
  await cdp('Page.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await cdp('Page.navigate', { url: new URL('#frame=DG_SE_&storeys=DG&hide=SITE&cut=DG@1.5&sel=DG_SE_STORAGE_WEST', viewer).href });
  await until('window.__monarchReady === true && !!window.__monarch');
  assert.equal(await evaluate('window.__monarch.state.sel'), 'DG_SE_STORAGE_WEST');
  assert.match(await evaluate('document.getElementById("panelBody").textContent'), /KALLAX/);
  console.log('PASS selected IFC product identity');
  const artwork = await evaluate(`(() => {
    const found = [];
    window.__monarch.scene.traverse(o => {
      if (o.userData?.key === 'UG_GYM_POSTER' || o.userData?.key === 'UG_GYM_POSTER_POKEMON') {
        const images=[]; o.traverse(m => { if(m.isMesh && m.material.map) images.push({
          width:m.material.map.image.width, height:m.material.map.image.height,
          uv:!!m.geometry.attributes.uv, colour:m.material.color.getHexString()}); });
        found.push({key:o.userData.key, images});
      }
    });
    return found;
  })()`);
  assert.equal(artwork.length, 2);
  assert.ok(artwork.every(o => o.images.length === 1 && o.images[0].width > 500 && o.images[0].height > 500 && o.images[0].uv && o.images[0].colour === 'ffffff'), JSON.stringify(artwork));
  console.log('PASS both owner-supplied poster images load from the IFC export with UVs and neutral colour');

  await evaluate('document.getElementById("documents").click()');
  const expectedDocuments = (await (await fetch(new URL('/api/documents', viewer))).json()).documents.length;
  await until(`document.querySelectorAll("#documentsBody a").length === ${expectedDocuments}`);
  const documents = await evaluate(`Promise.all([...document.querySelectorAll('#documentsBody a')].map(async a => {
    const r = await fetch(a.href); return {status:r.status,type:r.headers.get('content-type')}; }))`);
  assert.ok(documents.every(d => d.status === 200 && d.type.includes('pdf')));
  await evaluate('document.getElementById("documentsDialog").close()');
  console.log('PASS all architectural PDF links');
  const contracts = await evaluate(`fetch('/files/felsenhofstrasse/werkvertraege/Werkvertrag_2730_TT_Haus%20Nord.pdf').then(r=>r.status)`);
  assert.equal(contracts, 200);
  console.log('PASS worktree contract link');

  const camera = () => evaluate('window.__monarch.camera.position.toArray()');
  const close = (actual, expected, message) => assert.ok(actual.every((x, i) => Math.abs(x - expected[i]) < 1e-9), message);
  const beforeZoom = await camera();
  await cdp('Input.dispatchMouseEvent', { type: 'mouseWheel', x: 700, y: 500, deltaX: 0, deltaY: -120 });
  await delay(300);
  assert.notDeepEqual(await camera(), beforeZoom, 'wheel must fly while an object is selected');
  console.log('PASS scroll with selection');

  // Look turns the camera in place; it must never orbit around a selected object.
  await evaluate('window.__monarch.state.navMode="look"');
  const beforeLook = await camera();
  const beforeTurn = await evaluate('window.__monarch.camera.quaternion.toArray()');
  await cdp('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', buttons: 1, x: 700, y: 500, clickCount: 1 });
  await cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', button: 'left', buttons: 1, x: 780, y: 530 });
  await cdp('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', x: 780, y: 530, clickCount: 1 });
  await delay(100);
  close(await camera(), beforeLook, 'Look must keep the observer position fixed');
  assert.notDeepEqual(await evaluate('window.__monarch.camera.quaternion.toArray()'), beforeTurn, 'Look must turn the view');
  console.log('PASS Look keeps observer position fixed');

  await evaluate('window.__monarch.setMeasure(true)');
  const beforeMeasure = await camera();
  const quaternion = await evaluate('window.__monarch.camera.quaternion.toArray()');
  await cdp('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', buttons: 1, x: 800, y: 520, clickCount: 1 });
  await cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', button: 'left', buttons: 1, x: 805, y: 523 });
  await cdp('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', x: 805, y: 523, clickCount: 1 });
  close(await camera(), beforeMeasure, 'Measure must keep the camera position');
  close(await evaluate('window.__monarch.camera.quaternion.toArray()'), quaternion, 'Measure must keep view direction');
  console.log('PASS measuring never turns the camera');
  await evaluate('window.__monarch.setMeasure(false)');

  // Full hot reload keeps camera and stable selection and uses matching GLB/properties.
  const selected = await evaluate('window.__monarch.state.sel');
  const oldVersion = await evaluate('window.__monarch.version');
  const beforeReload = await camera();
  await evaluate('window.__monarch.reload()');
  await until(`window.__monarch.version !== ${JSON.stringify(oldVersion)}`);
  close(await camera(), beforeReload, 'Reload must keep the camera position');
  assert.equal(await evaluate('window.__monarch.state.sel'), selected);
  assert.deepEqual(errors, []);
  console.log('PASS reload preserves camera and selection; no JavaScript errors');

  const depths = await evaluate('window.__monarch.depthCheck()');
  assert.ok(depths.every(r => r.samples > 100 && r.wrong / r.samples < .01), JSON.stringify(depths));
  assert.deepEqual(errors, []);
  console.log('PASS coplanar finishes remain visible and cannot bleed through a solid 5 mm in front');

  const cameras = await evaluate(`(() => {
    const m=window.__monarch;
    const check=raw => {m.applyView(m.parseView(raw)); return m.cameraObstructions();};
    return {
      inRoof:check({cam:[9.2,8.5,7.3],target:[13.7,6.7,6.85],storeys:'DG'}),
      inRoom:check({cam:[9.2,7.8,6.8],target:[13.7,6.7,6.85],storeys:'DG'}),
      cutRoof:check({cam:[9.2,8.5,7.3],target:[13.7,6.7,6.85],storeys:'DG',cut:'DG@1.2'}),
      hiddenRoof:check({cam:[9.2,8.5,7.3],target:[13.7,6.7,6.85],storeys:'EG'})
    };
  })()`);
  assert.ok(cameras.inRoof.includes('ROOF_N_E'), JSON.stringify(cameras));
  assert.deepEqual(cameras.inRoom, []);
  assert.deepEqual(cameras.cutRoof, []);
  assert.deepEqual(cameras.hiddenRoof, []);
  assert.deepEqual(errors, []);
  console.log('PASS screenshot camera diagnostics detect the actual roof, respecting empty room space, cuts and hidden storeys');

  if (process.env.MONARCH_BROWSER_SCREENSHOT) {
    const shot = await cdp('Page.captureScreenshot', { format: 'png' });
    await writeFile(process.env.MONARCH_BROWSER_SCREENSHOT, Buffer.from(shot.data, 'base64'));
  }
} finally {
  ws?.close();
  chrome.kill('SIGTERM');
  await Promise.race([new Promise(resolve => chrome.once('exit', resolve)), delay(5000)]);
  if (chrome.exitCode == null) chrome.kill('SIGKILL');
  await rm(profile, { recursive: true, force: true });
}
