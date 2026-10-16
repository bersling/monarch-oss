"""Variant store CLI — swap stored setup variants in/out of the storey JSONs.

Usage:
    uv run python src/variant.py list
    uv run python src/variant.py show <group> <variant>       (ids + file path)
    uv run python src/variant.py load <group> <variant>       (rebuilds the IFC)
    uv run python src/variant.py load <group> <variant> --open  (…and opens the browser viewer)
    uv run python src/variant.py save <group> <variant>       (snapshot current state)

data/variants.json is the INDEX only: groups, their target storey JSON, the
active variant and one line per variant (description + file). The geometry of
each variant lives in its own file, data/variants/<group>/<variant>.json, which
is plain a {section: [entries]} mapping matching the storey JSON's sections.

A group owns every entry id that appears in any of its variants. Loading a
variant removes all owned ids from the target storey JSON, inserts the chosen
variant's entries and runs build_model.py.

To add a variant: create data/variants/<group>/<new>.json, register it in the
index, then `load` it. A group may declare "id_prefixes": ["DG_NEST_"]; on load,
any storey entry with such a prefix is removed too, so an id you dropped from a
variant file does not stay behind as an orphan (that happened once). To capture hand-edits you made directly in the storey
JSON, `save` them back into the variant file they came from.
"""
import json
import copy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REGISTRY = DATA / "variants.json"


def load_index():
    with open(REGISTRY) as f:
        return json.load(f)


def write_json(path, obj):
    from viewer_common import atomic_json
    atomic_json(path, obj)


def variant_items(var):
    """The {section: [entries]} mapping of one variant, read from its own file."""
    with open(DATA / var["file"]) as f:
        return json.load(f)


def pick(index, gname, vname=None):
    group = index["groups"].get(gname)
    if group is None:
        sys.exit(f"unknown group {gname!r} — try: {', '.join(index['groups'])}")
    if vname is None:
        return group, None
    var = group["variants"].get(vname)
    if var is None:
        sys.exit(f"unknown variant {vname!r} — try: {', '.join(group['variants'])}")
    return group, var


def owned_ids(group):
    ids = set()
    for var in group["variants"].values():
        for section, items in variant_items(var).items():
            for it in items:
                ids.add((section, it["id"]))
    return ids


def apply_variant(target, group, items):
    """Return a switched plan without writing files or mutating the caller.

    Checks use this pure operation rather than temporarily replacing the owner's
    live data. Include prefix-only sections so deleted objects cannot survive.
    """
    target = copy.deepcopy(target)
    owned = owned_ids(group)
    prefixes = tuple(group.get('id_prefixes', []))
    sections = {s for s, _ in owned} | set(items)
    sections |= {s for s, entries in target.items() if isinstance(entries, list)
                 and any(isinstance(it, dict) and str(it.get('id', '')).startswith(prefixes) for it in entries)}
    for section in sections:
        entries = items.get(section, [])
        pending = {it['id']: copy.deepcopy(it) for it in entries}
        if len(pending) != len(entries):
            raise ValueError(f'{section}: selected variant has duplicate IDs')
        result = []
        for it in target.get(section, []):
            if (section, it['id']) in owned or it['id'].startswith(prefixes):
                replacement = pending.pop(it['id'], None)
                if replacement is not None:
                    result.append(replacement)
            else:
                result.append(it)
        # Keep existing IDs in place: an ordinary reload should not move thousands
        # of unchanged lines around other rooms. Only newly introduced IDs append.
        result.extend(pending.values())
        target[section] = result
    return target


def same_items(present, stored):
    """Compare exact objects by stable ID; draw order is not variant identity."""
    current = {it['id']: it for it in present}
    saved = {it['id']: it for it in stored}
    return len(current) == len(present) and len(saved) == len(stored) and current == saved


def active_drift(index=None):
    index = index or load_index()
    errors = []
    for name, group in index['groups'].items():
        active = group.get('active')
        if active not in group['variants']:
            errors.append(f'{name}: unknown active variant {active!r}')
            continue
        target = json.loads((DATA / group['target']).read_text())
        expected = variant_items(group['variants'][active])
        owned, prefixes = owned_ids(group), tuple(group.get('id_prefixes', []))
        sections = {s for s, _ in owned} | set(expected)
        sections |= {s for s, entries in target.items() if isinstance(entries, list)
                     and any(isinstance(it, dict) and str(it.get('id', '')).startswith(prefixes) for it in entries)}
        for section in sections:
            present = [it for it in target.get(section, [])
                       if (section, it['id']) in owned or it['id'].startswith(prefixes)]
            stored = expected.get(section, [])
            current = {it['id']: it for it in present}
            saved = {it['id']: it for it in stored}
            if len(current) != len(present) or len(saved) != len(stored):
                errors.append(f'{name}/{active}: {section} has duplicate IDs')
            if current != saved:
                changed = sorted(k for k in current.keys() | saved.keys() if current.get(k) != saved.get(k))
                errors.append(f'{name}/{active}: {section} differs from saved variant: {", ".join(changed)}')
    return errors


def cmd_list():
    index = load_index()
    for gname, group in index["groups"].items():
        print(f"{gname}  (target: {group['target']})  — {group.get('description','')}")
        for vname, var in group["variants"].items():
            marker = "  * " if vname == group.get("active") else "    "
            print(f"{marker}{vname}: {var.get('description','')}")


def cmd_show(gname, vname):
    group, var = pick(load_index(), gname, vname)
    print(f"{gname}/{vname}  →  data/{var['file']}  (target: {group['target']})")
    print(f"  {var.get('description','')}")
    for section, items in variant_items(var).items():
        print(f"  {section}: {', '.join(it['id'] for it in items) or '—'}")


def _load(gname, vname, build, lease):
    index = load_index()
    group, var = pick(index, gname, vname)

    target_path = DATA / group["target"]
    with open(target_path) as f:
        target = json.load(f)
    target = apply_variant(target, group, variant_items(var))
    write_json(target_path, target)

    group["active"] = vname
    write_json(REGISTRY, index)

    print(f"loaded {gname}/{vname} into {target_path}" + (' — rebuilding IFC…' if build else ''))
    if build:
        subprocess.run([sys.executable, str(ROOT / "src" / "pipeline.py"), 'build-only'],
                       check=True, **lease.subprocess_options())


def cmd_load(gname, vname, open_viewer=False, build=True):
    from model_lock import Lease
    with Lease(ROOT) as lease:
        _load(gname, vname, build, lease)
    if open_viewer:
        # Release the lease before waiting for the server's validation.
        subprocess.run([str(ROOT / "open.sh"), "--wait"], check=True)


def cmd_save(gname, vname):
    """Write the group's currently-placed entries back into a variant file."""
    index = load_index()
    group, var = pick(index, gname, vname)
    with open(DATA / group["target"]) as f:
        target = json.load(f)
    owned = owned_ids(group)
    prefixes = tuple(group.get('id_prefixes', []))
    items = {}
    sections = {s for s, _ in owned} | {s for s, entries in target.items() if isinstance(entries, list)
                 and any(isinstance(it, dict) and str(it.get('id', '')).startswith(prefixes) for it in entries)}
    for section in sorted(sections):
        present = [it for it in target.get(section, []) if (section, it["id"]) in owned or it['id'].startswith(prefixes)]
        if present:
            items[section] = present
    write_json(DATA / var["file"], items)
    n = sum(len(v) for v in items.values())
    print(f"saved {n} entries from {group['target']} into data/{var['file']}")


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or ["help"]
    open_viewer = "--open" in rest
    build = '--no-build' not in rest
    rest = [a for a in rest if a not in ('--open', '--no-build')]
    if cmd == "list" and not rest:
        cmd_list()
    elif cmd == "load" and len(rest) == 2:
        cmd_load(*rest, open_viewer=open_viewer, build=build)
    elif cmd in ("show", "save") and len(rest) == 2:
        from model_lock import Lease
        with Lease(ROOT):
            {"show": cmd_show, "save": cmd_save}[cmd](*rest)
    elif cmd == 'check' and not rest:
        errors = active_drift()
        print('\n'.join(errors) if errors else 'Active variants match the placed model data.')
        sys.exit(bool(errors))
    else:
        print(__doc__)
