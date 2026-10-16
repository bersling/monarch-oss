"""Source documents: the optional catalogue data/plan_documents.json and read-only file links.

The catalogue pins the documents a model was traced from (plans, datasheets, surveys):

    {"documents": [{"id": "floor-gf", "title": "Ground floor plan", "path": "plans/gf.pdf",
                    "printed_date": "2026-01-15", "sha256": "<file hash>", "status": "current",
                    "scope": "walls, openings", "model_evidence": "./q compare GF"}]}

A storey or element may name one by `source_document`; build_model.py then writes its
path, date and hash into Pset_ModelSource. A replaced file fails verification until the
hash is updated, so geometry is reviewed against the new revision. Without the file
everything degrades to an empty catalogue.

Links in the viewer resolve under the registered roots only (plans/, docs/, assets/,
data/), component by component with Unicode-normalised matching; never guessed.
"""
import json
import unicodedata
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOTS = ('plans', 'docs', 'assets', 'data')


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
            # bypass the ambiguity guard for similar names.
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
    if parts[0] not in LOCAL_ROOTS:
        return None
    found = beneath(root / parts[0], Path(*parts[1:]))
    if found is None and parts[0] == 'plans':
        found = beneath(main_checkout(root) / parts[0], Path(*parts[1:]))   # worktrees share the main plans/
    return found


def href(reference):
    return '/files/' + '/'.join(quote(p, safe='') for p in str(reference).removeprefix('../').split('/'))


def catalogue(root=ROOT):
    path = Path(root) / 'data/plan_documents.json'
    return json.loads(path.read_text()).get('documents', []) if path.is_file() else []


def model_source(document_id, source_data, interpretation='', root=ROOT):
    """IFC provenance for the reference that actually governs this element."""
    props = {'SourceData': str(source_data), 'Interpretation': interpretation}
    if not document_id:
        return props
    document = next((d for d in catalogue(root) if d['id'] == document_id), None)
    if document is None:
        raise ValueError(f'Unknown source_document: {document_id}')
    return {'DocumentID': document_id, 'Plan': document['path'],
            'PlanPrintedDate': document.get('printed_date', ''), 'PlanSHA256': document.get('sha256', ''),
            **props, 'VerificationScope': document.get('model_evidence', '')}


def verify(root=ROOT):
    """Fail on replaced/missing pinned documents; model changes require source review."""
    from viewer_common import file_hash
    errors = []
    for doc in catalogue(root):
        path = resolve(doc['path'], root)
        if not path or not path.is_file():
            errors.append(f"{doc['id']}: missing {doc['path']}")
        elif doc.get('sha256') and file_hash(path) != doc['sha256']:
            errors.append(f"{doc['id']}: document content changed; review geometry and update data/plan_documents.json")
    return errors


def listing(root=ROOT):
    return [{**d, 'href': href(d['path']), 'available': bool(resolve(d['path'], root))} for d in catalogue(root)]


def cmd_sources(args, root=ROOT):
    query = normalized(' '.join(args))
    documents = listing(root)
    if not documents:
        print('No source documents registered (data/plan_documents.json). Element provenance is in Pset_Source, from data/sources.json.')
        return
    for d in documents:
        if query and query not in normalized(json.dumps(d, ensure_ascii=False)):
            continue
        print(f"{d['id']} · {d.get('title', '')} · printed {d.get('printed_date', '?')} · {d.get('status', '')}")
        print(f"  {d['path']}\n  {d.get('scope', '')}\n  {d.get('model_evidence', '')}")


if __name__ == '__main__':
    import sys
    errors = verify()
    for error in errors:
        print(error)
    if not errors:
        cmd_sources(sys.argv[1:])
    sys.exit(bool(errors))
