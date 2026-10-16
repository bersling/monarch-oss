// Source paths can contain spaces, decomposed umlauts and punctuation.
// Escape text first, and allow only HTTP(S) and the server's document roots (plans/, docs/, assets/, data/).
const ROOTS = '(?:plans|docs|assets|data)';
const PATH_RE = new RegExp(`(?<![A-Za-z0-9_/])(${ROOTS}\\/[^\\n;|]*?\\.(?:pdf|png|jpe?g|webp|gif|svg|json|obj|mtl|glb|gltf|ifc|txt|md|dxf|dwg|csv|xlsx?|docx?|html?))(?=$|[\\s,;)\\]])`, 'gi');
const URL_RE = /\bhttps?:\/\/[^\s<>"')\]]+/gi;
export function fileHref(path) {
  return '/files/' + path.replace(/^\.\.\//, '').split('/').map(encodeURIComponent).join('/');
}
export function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
export function linkify(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value) ?? '';
  const spans = [];
  for (const m of text.matchAll(URL_RE)) spans.push([m.index, m.index + m[0].length, m[0]]);
  for (const m of text.matchAll(PATH_RE)) spans.push([m.index, m.index + m[1].length, fileHref(m[1])]);
  const trimmed = text.trim();
  if (!spans.length && new RegExp(`^${ROOTS}/\\S`).test(trimmed)) {
    const path = trimmed.replace(/\s+-\s.*$/, '');
    const start = text.indexOf(trimmed);
    spans.push([start, start + path.length, fileHref(path)]);
  }
  spans.sort((a, b) => a[0] - b[0]);
  let out = '', pos = 0;
  for (const [s, e, href] of spans) {
    if (s < pos) continue;
    out += esc(text.slice(pos, s)) + `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(text.slice(s, e))}</a>`;
    pos = e;
  }
  return out + esc(text.slice(pos));
}
