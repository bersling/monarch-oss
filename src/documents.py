"""Portable, read-only model evidence links, also from isolated worktrees.

Only registered document roots are exposed. Resolve Unicode and German spelling
variants component by component; never guess from a similar basename/revision.
"""
import json
import unicodedata
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOTS = ('plans', 'docs', 'assets', 'renders', 'data', 'floorplans_2026-03-05')
EXTERNAL_ROOTS = ('werkvertraege', 'plaene', 'apparatenliste', 'details', 'muster')


def main_checkout(root=ROOT):
    root = Path(root).resolve()
    marker = root / '.git'
    if marker.is_file():
        gitdir = (root / marker.read_text().strip().removeprefix('gitdir: ')).resolve()
        common = gitdir / 'commondir'
        if common.is_file():
            return (gitdir / common.read_text().strip()).resolve().parent
    return root


def normalized(text):
    text = unicodedata.normalize('NFC', text).casefold()
    return text.translate(str.maketrans({'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss'}))


def beneath(base, rel):
    """One unambiguous normalized component at a time, with a final root guard."""
    base = Path(base).resolve()
    current = base
    for part in Path(rel).parts:
        if part in ('..', '.') or Path(part).is_absolute():
            return None
        if current.is_dir():
            children = list(current.iterdir())
            # .exists() on macOS accepts a differently-cased filename, which can
            # bypass the ambiguity guard for Mueller vs Müller.
            matches = [p for p in children if unicodedata.normalize('NFC', p.name) == unicodedata.normalize('NFC', part)]
            if not matches:
                matches = [p for p in children if normalized(p.name) == normalized(part)]
            if len(matches) != 1:
                return None
            current = matches[0]
        else:
            return None
        if not current.resolve().is_relative_to(base):
            return None
    return current if current.exists() else None


def resolve(reference, root=ROOT):
    root = Path(root).resolve()
    rel = unquote(str(reference)).removeprefix('/files/').removeprefix('../')
    parts = Path(rel).parts
    if not parts or any(p == '..' for p in parts) or Path(rel).is_absolute():
        return None
    if parts[0] == 'felsenhofstrasse':
        if len(parts) < 2 or parts[1] not in EXTERNAL_ROOTS:
            return None
        return beneath(main_checkout(root).parent / 'felsenhofstrasse' / parts[1], Path(*parts[2:]))
    if parts[0] not in LOCAL_ROOTS:
        return None
    found = beneath(root / parts[0], Path(*parts[1:]))
    if found is None and parts[0] in ('floorplans_2026-03-05', 'renders'):
        found = beneath(main_checkout(root) / parts[0], Path(*parts[1:]))
    return found


def href(reference):
    return '/files/' + '/'.join(quote(p, safe='') for p in str(reference).removeprefix('../').split('/'))


def catalogue(root=ROOT):
    return json.loads((Path(root) / 'data/plan_documents.json').read_text())['documents']


def model_source(document_id, source_data, interpretation='', root=ROOT):
    """IFC provenance for the reference that actually governs this element."""
    document = next((d for d in catalogue(root) if d['id'] == document_id), None)
    if document is None:
        raise ValueError(f'Unknown source_document: {document_id}')
    return {'DocumentID': document_id, 'Plan': document['path'],
            'PlanPrintedDate': document['printed_date'], 'PlanSHA256': document['sha256'],
            'SourceData': str(source_data), 'Interpretation': interpretation,
            'VerificationScope': document['model_evidence']}


def verify(root=ROOT):
    """Fail on replaced/missing pinned plans; model changes require source review."""
    from viewer_common import file_hash
    errors = []
    for doc in catalogue(root):
        path = resolve(doc['path'], root)
        if not path or not path.is_file():
            errors.append(f"{doc['id']}: missing {doc['path']}")
        elif file_hash(path) != doc['sha256']:
            errors.append(f"{doc['id']}: plan content changed; review geometry and update data/plan_documents.json")
    return errors


def listing(root=ROOT):
    return [{**d, 'href': href(d['path']), 'available': bool(resolve(d['path'], root))} for d in catalogue(root)]


def cmd_plans(args, root=ROOT):
    query = normalized(' '.join(args))
    for d in listing(root):
        if query and query not in normalized(json.dumps(d, ensure_ascii=False)):
            continue
        print(f"{d['id']} · {d['title']} · printed {d['printed_date']} · {d['status']}")
        print(f"  {d['path']}\n  {d['scope']}\n  {d['model_evidence']}")


if __name__ == '__main__':
    import sys
    errors = verify()
    for error in errors:
        print(error)
    if not errors:
        cmd_plans(sys.argv[1:])
    sys.exit(bool(errors))
