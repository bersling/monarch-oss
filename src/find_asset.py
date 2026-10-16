"""Search the mesh-asset platforms from one command and print download URLs.

Usage:
    uv run python src/find_asset.py "intex pool" [--count 12] [--platform 3dw|sketchfab|polyhaven]

Prints one row per candidate: platform, title, whatever dimensions the uploader
wrote into the description, licence, a preview image URL and a direct download
URL. Feed the download URL straight into src/convert_asset.py.

Why this exists: 3D Warehouse has by far the best hit rate for real building
products, and its API is open but undocumented, so it kept getting rediscovered
by hand. See assets/README.md for the per-platform caveats.
"""
import json
import re
import sys
import urllib.parse
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0'}          # every one of these CDNs 403s the urllib default
DIMS = re.compile(r'\d+\s*[x×]\s*\d+(\s*[x×]\s*\d+)?\s*(cm|mm|m|")?', re.I)


def get(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def dims_of(text):
    m = DIMS.search(text or '')
    return m.group(0).strip() if m else ''


def warehouse(query, count):
    """3D Warehouse: open API, no key. The glb binary is a public direct download."""
    url = ('https://3dwarehouse.sketchup.com/warehouse/v1.0/entities'
           f'?contentType=3dw&q={urllib.parse.quote(query)}&count={count}')
    for e in get(url).get('entries', []):
        try:
            full = get(f"https://3dwarehouse.sketchup.com/warehouse/v1.0/entities/{e['id']}")
        except Exception:
            continue
        b = full.get('binaries', {})
        glb = (b.get('glb') or {}).get('contentUrl')
        if not glb:
            continue                        # older uploads are .skp/.dae only
        yield {
            'platform': '3dwarehouse',
            'title': full.get('title') or '',
            'dims': dims_of(full.get('description')),
            'license': '3D Warehouse General Model Terms',
            'preview': (b.get('bot_lt') or {}).get('contentUrl', ''),
            'download': glb,
            'page': f"https://3dwarehouse.sketchup.com/model/{full['id']}",
        }


def sketchfab(query, count, token):
    """Sketchfab: search is open, downloads need the token. Mostly CC-BY."""
    url = ('https://api.sketchfab.com/v3/search?type=models&downloadable=true'
           f'&q={urllib.parse.quote(query)}&count={count}&sort_by=-likeCount')
    for r in get(url, {'Authorization': f'Token {token}'}).get('results', []):
        yield {
            'platform': 'sketchfab',
            'title': r.get('name') or '',
            'dims': dims_of(r.get('description')),
            'license': (r.get('license') or {}).get('label') or '?',
            'preview': (sorted(r['thumbnails']['images'], key=lambda i: -i['width'])[0]['url']
                        if r.get('thumbnails', {}).get('images') else ''),
            'download': f"https://api.sketchfab.com/v3/models/{r['uid']}/download"
                        "  (GET with Authorization: Token $SKETCHFAB, returns a signed gltf url)",
            'page': r.get('viewerUrl', ''),
        }


def polyhaven(query, count):
    """Poly Haven: CC0 and correctly scaled, but a tiny props-only catalogue."""
    terms = query.lower().split()
    hits = [(slug, a) for slug, a in get('https://api.polyhaven.com/assets?type=models').items()
            if all(t in (slug + ' ' + ' '.join(a.get('tags', []))).lower() for t in terms)]
    for slug, a in hits[:count]:
        yield {
            'platform': 'polyhaven',
            'title': a.get('name') or slug,
            'dims': '',
            'license': 'CC0',
            'preview': (a.get('thumbnail_url') or ''),
            'download': f'https://api.polyhaven.com/files/{slug}   (then pick the gltf/blend url)',
            'page': f'https://polyhaven.com/a/{slug}',
        }


def main():
    args = [a for a in sys.argv[1:]]
    count = 10
    only = None
    if '--count' in args:
        i = args.index('--count'); count = int(args[i + 1]); del args[i:i + 2]
    if '--platform' in args:
        i = args.index('--platform'); only = args[i + 1]; del args[i:i + 2]
    if not args:
        sys.exit(__doc__)
    query = ' '.join(args)

    import os
    sources = []
    if only in (None, '3dw'):
        sources.append(('3dwarehouse', lambda: warehouse(query, count)))
    if only in (None, 'sketchfab') and os.environ.get('SKETCHFAB'):
        sources.append(('sketchfab', lambda: sketchfab(query, count, os.environ['SKETCHFAB'])))
    if only in (None, 'polyhaven'):
        sources.append(('polyhaven', lambda: polyhaven(query, count)))

    found = 0
    for name, fn in sources:
        try:
            rows = list(fn())
        except Exception as exc:
            print(f'-- {name}: FAILED ({exc})')
            continue
        for r in rows:
            found += 1
            print(f"\n[{r['platform']}] {r['title']}"
                  + (f"   dims from description: {r['dims']}" if r['dims'] else ''))
            print(f"    licence : {r['license']}")
            print(f"    page    : {r['page']}")
            print(f"    preview : {r['preview']}")
            print(f"    download: {r['download']}")
    if not found:
        print('no candidates — try a shorter or English query, '
              'or a product category instead of a brand name')


if __name__ == '__main__':
    main()
