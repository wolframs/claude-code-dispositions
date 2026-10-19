"""Render changed transcripts in one process; refresh labels when parsing changes."""
import hashlib
from pathlib import Path

import extract_transcript

ROOT = Path(__file__).resolve().parents[1]


def refresh(root=ROOT):
    stamp = root / 'corpus/.fetch-stamps/renderer-sha256'
    digest = hashlib.sha256(b''.join((root / 'tools' / name).read_bytes().replace(b'\r\n', b'\n')
                                   for name in ('transcript.py', 'extract_transcript.py'))).hexdigest()
    rebuild = not stamp.exists() or stamp.read_text().strip() != digest
    count = 0
    for source in (root / 'corpus/raw').rglob('*.jsonl'):
        target = root / 'corpus/rendered' / source.relative_to(root / 'corpus/raw').with_suffix('.md')
        if rebuild or not target.exists() or source.stat().st_mtime_ns > target.stat().st_mtime_ns:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(extract_transcript.render(source, 800), encoding='utf-8')
            count += 1
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(digest + '\n')
    return count


if __name__ == '__main__':
    print(f're-rendered {refresh()} transcript(s)')
