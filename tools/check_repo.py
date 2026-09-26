#!/usr/bin/env python3
"""One offline consistency check for a full checkout, then every regression suite.

No model calls, no network, no binary activation. Passing proves the repository
agrees with itself — active links resolve, release.json/reviews.json/targets.json
describe the same edits, ab/data.json is fresh, every script parses — not that
any machine is patched (`ccctl.py status --check` is that claim).
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_ab  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Current documents only. notes/archive/ is moved unchanged by rule and may
# point at paths that have since moved; corpus/rendered/ is generated evidence;
# baseline/<ver>/ is stock prompt text whose links point into Anthropic's docs.
ACTIVE_DOCS = ('README.md', 'TODO.md', 'AGENTS.md', 'corpus/README.md', 'baseline/README.md')
ACTIVE_TREES = ('spec', 'notes', 'tools', 'edits', 'ab')
SKIP_PREFIXES = ('notes/archive/',)


def active_docs(root=ROOT):
    docs = [root / name for name in ACTIVE_DOCS]
    for tree in ACTIVE_TREES:
        docs.extend(p for p in (root / tree).rglob('*.md')
                    if not p.relative_to(root).as_posix().startswith(SKIP_PREFIXES))
    return [d for d in docs if d.is_file()]


def check_links(root=ROOT):
    for doc in active_docs(root):
        text = re.sub(r'```.*?```', '', doc.read_text(encoding='utf-8'), flags=re.S)
        for link in re.findall(r'\[[^\]\n]*\]\(([^)\n]+)\)', text):
            target = link.strip('<>').split('#', 1)[0]
            if not target or re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:', target):
                continue
            if not (doc.parent / unquote(target)).resolve().exists():
                raise ValueError(f'Broken active link: {doc.relative_to(root)} -> {link}')


def check_release(root=ROOT):
    release = json.loads((root / 'release.json').read_text(encoding='utf-8'))
    for key in ('ccVersion', 'promptData', 'tweakcc', 'stock'):
        if key not in release:
            raise ValueError(f'release.json lacks {key}')
    overlay = root / release['tweakcc']['overlay']
    if not overlay.is_file():
        raise ValueError(f'Selected tweakcc overlay is missing: {overlay.name}')
    if not release['stock']:
        raise ValueError('release.json records no pristine binary hash for any platform')
    # The build helper must pin what release.json pins, or the two drift apart.
    helper = (root / 'tools/build_local_tweakcc.py').read_text(encoding='utf-8')
    if 'release.json' not in helper:
        raise ValueError('build_local_tweakcc.py no longer reads its pin from release.json')


def run(command, **kw):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, **kw)
    if result.returncode:
        raise RuntimeError(f'{command}:\n{result.stdout}\n{result.stderr}')


def syntax_bash():
    """Prefer Git Bash on Windows: WSL bash cannot consume native file paths."""
    if sys.platform == 'win32':
        git = shutil.which('git')
        if git:
            candidate = Path(git).resolve().parent.parent / 'bin' / 'bash.exe'
            if candidate.is_file():
                return str(candidate)
    bash = shutil.which('bash')
    if (sys.platform == 'win32' and bash
            and Path(bash).parent.name.lower() in ('system32', 'sysnative')):
        return None
    return bash


def check_syntax(root=ROOT):
    for source in (root / 'tools').rglob('*.py'):
        compile(source.read_text(encoding='utf-8'), str(source), 'exec')
    node = shutil.which('node')
    if node:
        html = (root / 'ab/index.html').read_text(encoding='utf-8')
        for script in re.findall(r'<script>(.*?)</script>', html, re.S):
            result = subprocess.run([node, '--check', '-'], input=script,
                                    capture_output=True, text=True)
            if result.returncode:
                raise ValueError(f'ab/index.html JavaScript syntax: {result.stderr}')
    else:
        print('ab/index.html syntax check skipped: no node on PATH')
    bash = syntax_bash()
    if bash:
        for source in (root / 'tools').glob('*.sh'):
            run([bash, '-n', str(source)])
    elif sys.platform != 'win32':
        raise RuntimeError('bash is required for shell syntax checks')
    else:
        print('shell syntax checks skipped: no bash on this Windows host')


def main():
    check_links()
    check_release()
    build_ab.check()
    if (ROOT / 'CLAUDE.md').read_text(encoding='utf-8') != (ROOT / 'AGENTS.md').read_text(encoding='utf-8'):
        raise ValueError('CLAUDE.md and AGENTS.md diverged')
    check_syntax()
    print('PASS active links, release/review/target consistency, A/B view freshness, syntax')
    for test in sorted((ROOT / 'tools/tests').glob('test_*.py')):
        run([sys.executable, str(test)])
        print(f'PASS {test.name}')
    print('Repository consistency and regression checks passed.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        raise SystemExit(1)
