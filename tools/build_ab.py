#!/usr/bin/env python3
"""Generate ab/data.json: the review view of every current target.

The view is derived, never authored. Stock fragment bodies come from the
release's prompt map (baseline/<ver>/prompts.json, hash-pinned in
release.json), edited bodies from edits/*.md, and the adhoc old/new byte runs
from the same plan_fragments/plan_adhocs pass that `ccctl.py apply` executes —
run against the pristine binary whose SHA-256 release.json records for this
platform. A patched binary, or one from another release, is refused before
planning, so the view can never describe our own output as stock.

    build_ab.py                 # from the parked pristine of the release
    build_ab.py --binary PATH   # from an explicit pristine binary
    build_ab.py --check         # offline: the committed view matches its sources

`--check` needs no binary. It fails when any source the view depends on has
changed since generation (edits, locators in ccctl.py, release.json, the prompt
map, the target map) or when ab/data.json was edited by hand.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ccctl  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_release(root=ROOT):
    return json.loads((root / 'release.json').read_text(encoding='utf-8'))


def source_hashes(root=ROOT):
    """Every input the view is a function of, LF-normalized so a checkout's
    line endings cannot make a fresh view read stale."""
    release = load_release(root)
    paths = [root / 'release.json', root / 'tools/build_ab.py', root / 'tools/ccctl.py',
             root / release['promptData']['path'], *sorted((root / 'edits').glob('*'))]
    return {p.relative_to(root).as_posix(): digest(p.read_bytes().replace(b'\r\n', b'\n'))
            for p in paths if p.is_file()}


def content_hash(data):
    return digest(json.dumps({k: v for k, v in data.items() if k != 'contentSha256'},
                             sort_keys=True, ensure_ascii=False).encode('utf-8'))


def validate_sources(root=ROOT):
    release = load_release(root)
    raw = (root / release['promptData']['path']).read_bytes()
    if digest(raw) != release['promptData']['sha256']:
        raise ValueError('Stock prompt map hash differs from release.json')
    stock = {e['id']: e for e in json.loads(raw)['prompts']}
    targets = json.loads((root / 'edits/targets.json').read_text(encoding='utf-8'))
    ids = [t['id'] for t in targets]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate target IDs')
    fragments = {t['id'] for t in targets if t['kind'] == 'fragment'}
    authored = {p.stem for p in (root / 'edits').glob('*.md') if p.name != 'README.md'}
    if fragments != authored:
        raise ValueError('Fragment target map and authored files disagree')
    reviews = json.loads((root / 'edits/reviews.json').read_text(encoding='utf-8'))
    if set(reviews) != fragments:
        raise ValueError('Review records and current fragments disagree')
    for ident in fragments:
        if ident not in stock or not ccctl.fragment_review_matches(
                reviews[ident], stock[ident], root / 'edits' / (ident + '.md')):
            raise ValueError(f'Review hash mismatch: {ident}')
    for target in targets:
        if target['kind'] not in ('fragment', 'adhoc'):
            raise ValueError(f'Unknown target kind: {target["kind"]}')
        for key in ('source', 'spec'):
            if not (root / target[key]).is_file():
                raise ValueError(f'Missing target {key}: {target[key]}')
    return release, stock, targets


def default_binary(release):
    """Where ccctl leaves a pristine binary of the release on this machine."""
    version = release['ccVersion']
    home = Path.home()
    exe = '.exe' if sys.platform == 'win32' else ''
    versions = home / '.local' / 'share' / 'claude' / 'versions'
    # win32 keeps versions/<ver> pristine (the patched copy is the launcher);
    # POSIX parks it as <ver>.stock. `update` moves the staged download into
    # place, so after an update the staging copy is gone. The SHA-256 check in
    # generate() refuses anything that is not the recorded pristine.
    for cand in (home / 'ccctl' / 'staging' / f'claude-{version}-pristine{exe}',
                 versions / f'{version}.stock',
                 *((versions / version,) if sys.platform == 'win32' else ())):
        if cand.is_file():
            return cand
    return None


def generate(binary, root=ROOT, platform=None):
    release, stock, targets = validate_sources(root)
    platform = platform or ccctl.cc_platform()
    expected = release['stock'].get(platform)
    if not expected:
        raise ValueError(f'release.json records no pristine SHA-256 for {platform}; '
                         'record one from a manifest-verified download first')
    if digest(Path(binary).read_bytes()) != expected:
        raise ValueError(f'Binary is not the pristine {release["ccVersion"]} {platform} '
                         'release (SHA-256 mismatch)')
    blob = ccctl.live_blob(binary)
    edits = [root / t['source'] for t in targets if t['kind'] == 'fragment']
    plan = ccctl.plan_fragments(blob, stock, edits) + ccctl.plan_adhocs(blob)
    if len(plan) != len(targets) or {p['name'] for p in plan} != {t['id'] for t in targets}:
        raise ValueError('Executable plan and target manifest disagree')
    by_id = {p['name']: p for p in plan}
    items = []
    for t in targets:
        p = by_id[t['id']]
        if p['status'] != 'AUTO':
            raise ValueError(f'{t["id"]}: expected AUTO, got {p["status"]} ({p.get("reason")})')
        fragment = t['kind'] == 'fragment'
        entry = stock[t['id']] if fragment else None
        items.append({'id': t['id'], 'name': t['title'], 'kind': t['kind'],
                      'variables': list(entry['identifierMap'].values()) if fragment else [],
                      'stock': ccctl.stock_body(entry) if fragment else p['old'].decode('latin-1'),
                      'edited': ccctl.edit_text(root / t['source']) if fragment else p['new'].decode('latin-1'),
                      'note': t['intent'] + ' ' + t['verification'],
                      'source': t['source'], 'spec': t['spec']})
    cfg = {'repoPath': str(root)}
    data = {'ccVersion': release['ccVersion'], 'platform': platform, 'stockSha256': expected,
            'items': items, 'unreachable': [],
            'markers': ccctl.repo_markers(cfg), 'antiMarkers': ccctl.repo_antimarkers(cfg),
            'sourceHashes': source_hashes(root)}
    data['contentSha256'] = content_hash(data)
    return data


def check(root=ROOT):
    validate_sources(root)
    data = json.loads((root / 'ab/data.json').read_text(encoding='utf-8'))
    if data.get('sourceHashes') != source_hashes(root):
        raise ValueError('A/B view is stale: regenerate with tools/build_ab.py')
    if data.get('contentSha256') != content_hash(data):
        raise ValueError('A/B data was modified after generation')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--binary', type=Path, help='pristine binary of the release in release.json')
    parser.add_argument('--platform', help='release platform key of --binary (default: this machine)')
    parser.add_argument('--check', action='store_true', help='offline freshness check; does not regenerate')
    args = parser.parse_args()
    if args.check:
        data = check()
    else:
        release = load_release()
        binary = args.binary or default_binary(release)
        if not binary or not Path(binary).is_file():
            raise ValueError(f'no pristine {release["ccVersion"]} binary found; run '
                             f'`ccctl.py analyze {release["ccVersion"]}` or pass --binary')
        data = generate(binary, platform=args.platform)
        (ROOT / 'ab/data.json').write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n',
                                           encoding='utf-8')
    print(f"OK: CC {data['ccVersion']} {data['platform']}, {len(data['items'])} current targets, "
          f"{len(data['markers'])} markers, {len(data['antiMarkers'])} anti-markers; "
          f"view {'fresh' if args.check else 'generated'}")


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        raise SystemExit(1)
