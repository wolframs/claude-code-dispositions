#!/usr/bin/env bash
# Stock-arm capture: copies each rig to an isolated workdir, runs claude -p
# against the unpatched binary, and records end state. One argument: "<rig> <k>".
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNS="$HOME/probe-runs"
MODEL="claude-opus-5"

rig="$1"; k="$2"
spec="$REPO/probes/prompts.json"
prompt=$(python3 -c "import json,sys; print(json.load(open('$spec'))['$rig']['prompt'])")
prompt2=$(python3 -c "import json,sys; print(json.load(open('$spec'))['$rig'].get('prompt2',''))")
turns=$(python3 -c "import json,sys; print(json.load(open('$spec'))['$rig']['max_turns'])")

wd="$RUNS/$rig-r$k"
rm -rf "$wd"; mkdir -p "$wd"
cp -r "$REPO/probes/rigs/$rig/." "$wd/"
cd "$wd"
printf '__pycache__/\n.runmeta/\n' > .gitignore
git init -q && git add -A && git -c user.name=W -c user.email=w@localhost commit -qm "initial state"

meta="$wd/.runmeta"; mkdir -p "$meta"
claude --version > "$meta/cc-version.txt" 2>&1
date -Is > "$meta/started.txt"

run_session() {
  local p="$1" out="$2"
  timeout 1800 claude -p "$p" --model "$MODEL" --max-turns "$turns" \
    --dangerously-skip-permissions > "$out" 2> "$out.err"
  echo "exit=$?" >> "$out.err"
}

run_session "$prompt" "$meta/final-message-1.txt"
if [ -n "$prompt2" ]; then
  sleep 3
  run_session "$prompt2" "$meta/final-message-2.txt"
fi

git -C "$wd" status --porcelain > "$meta/git-status.txt"
git -C "$wd" log --oneline >> "$meta/git-status.txt"
ps -eo pid,etime,cmd --no-headers | grep -F "$wd" | grep -v grep > "$meta/stray-processes.txt" || true
date -Is > "$meta/finished.txt"
echo "DONE $rig-r$k"
