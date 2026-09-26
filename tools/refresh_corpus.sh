#!/usr/bin/env bash
# Pull new and grown transcripts into corpus/, re-render what changed, rebuild the index.
#
# Claude Code deletes transcripts after cleanupPeriodDays (default 30), and a
# raised setting protects only one disk, so this repo is the only copy that
# outlives both. Run it regularly.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${1:-$HOME/.claude/projects}"
RAW="$REPO/corpus/raw"

[ -d "$SRC" ] || { echo "no transcript dir at $SRC" >&2; exit 1; }

rsync -a --include='*/' --include='*.jsonl' --exclude='*' "$SRC/" "$RAW/"

rendered=0
while IFS= read -r f; do
  rel="${f#"$RAW"/}"
  out="$REPO/corpus/rendered/${rel%.jsonl}.md"
  if [ ! -e "$out" ] || [ "$f" -nt "$out" ]; then
    python3 "$REPO/tools/extract_transcript.py" "$f" -o "$out"
    rendered=$((rendered + 1))
  fi
done < <(find "$RAW" -name '*.jsonl')

echo "re-rendered $rendered transcript(s)"
python3 "$REPO/tools/build_corpus_index.py"
