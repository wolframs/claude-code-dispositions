#!/usr/bin/env bash
# Pull new and grown transcripts from every machine into corpus/, re-render
# what changed, rebuild the index.
#
# Claude Code deletes transcripts after cleanupPeriodDays (default 30), and a
# raised setting protects only one disk, so this repo is the only copy that
# outlives both. Run it regularly.
#
# corpus/raw/ stays flat: one directory per Claude Code project dir, exactly as
# the machine names it. The three machines cannot collide because CC derives the
# name from the absolute cwd: Linux is `-home-[HUMAN]-*`, macOS `-Users-[HUMAN]-*`,
# Windows `C--Users-w-[HUMAN]-*`. Every tool under tools/ globs `raw/*/*.jsonl`
# and needs no machine awareness.
#
#   tools/refresh_corpus.sh            # all machines (an unreachable one is skipped, loudly)
#   tools/refresh_corpus.sh local      # only this machine
#   tools/refresh_corpus.sh air hn     # named machines
#
# Hosts and users come from ~/.zshrc's alias section (air, hn); the Windows box
# is reached as ssh-recovery and reads [USER]'s profile. SSH is BatchMode, so
# the key must already be loaded.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="$REPO/corpus/raw"
STAMPS="$REPO/corpus/.fetch-stamps"   # untracked; one mtime file per remote
mkdir -p "$RAW" "$STAMPS"

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10)
AIR_HOST="[HUMAN]@[MACOS_LAPTOP]"
AIR_RSYNC="${CORPUS_AIR_RSYNC:-/opt/homebrew/bin/rsync}"
HN_HOST="ssh-recovery@hn-[HUMAN].[LAN]"
HN_PROJECTS='[WINDOWS_HOME]\.claude\projects'
LINUX_HOST="[HUMAN]@[LINUX_WORKSTATION]"
RSYNC_BIN="${CORPUS_RSYNC:-rsync}"
if [ "$(uname -s)" = Darwin ] && [ -z "${CORPUS_RSYNC:-}" ] && [ -x /opt/homebrew/bin/rsync ]; then
  RSYNC_BIN=/opt/homebrew/bin/rsync
fi

remote_rsync() {
  if "$RSYNC_BIN" --version | head -1 | grep -q '^openrsync:'; then
    echo 'Remote corpus intake requires GNU rsync; on macOS install brew rsync or set CORPUS_RSYNC.' >&2
    return 1
  fi
  "$RSYNC_BIN" --timeout=120 "$@"
}

fetch_local() {
  local src="${LOCAL_SRC:-$HOME/.claude/projects}"
  [ -d "$src" ] || { echo "local: no transcript dir at $src" >&2; return 1; }
  "$RSYNC_BIN" -a --include='*/' --include='*.jsonl' --exclude='*' "$src/" "$RAW/"
  echo "local: synced from $src"
}

fetch_air() {
  # rsync over ssh; -a keeps mtimes, which the re-render step keys on.
  remote_rsync -a --rsync-path="$AIR_RSYNC" -e "${SSH[*]}" --include='*/' --include='*.jsonl' --exclude='*' \
    "$AIR_HOST:.claude/projects/" "$RAW/" \
    || { echo "air: rsync failed (host down? key not loaded?)" >&2; return 1; }
  echo "air: synced"
}

fetch_linux() {
  remote_rsync -a -e "${SSH[*]}" --include='*/' --include='*.jsonl' --exclude='*' \
    "$LINUX_HOST:.claude/projects/" "$RAW/" \
    || { echo "linux: rsync failed (host down? key not loaded?)" >&2; return 1; }
  echo "linux: synced"
}

fetch_hn() {
  # No rsync on Windows, and bsdtar aborts the whole archive on the first
  # `memory/` junction it cannot stat (--exclude does not stop the stat). So
  # PowerShell builds the .jsonl list — it skips the junctions with -ErrorAction
  # SilentlyContinue — and tar streams exactly that list. Nothing but the
  # archive may reach stdout on the remote side.
  # A stamp file bounds the list to files written since the last run; delete
  # corpus/.fetch-stamps/hn to force a full pull.
  local stamp="$STAMPS/hn" since='1970-01-01'
  [ -f "$stamp" ] && since="$(python3 -c 'from datetime import datetime,timezone; import os,sys; print(datetime.fromtimestamp(os.stat(sys.argv[1]).st_mtime,timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$stamp")"
  local started; started="$(mktemp)"
  local stage; stage="$(mktemp -d "$STAMPS/hn-stage.XXXXXX")"
  local ps="\$root='$HN_PROJECTS'; \$list=Join-Path \$env:TEMP 'cc-jsonl.lst';"
  ps+=" Get-ChildItem \$root -Recurse -Filter *.jsonl -File -ErrorAction SilentlyContinue"
  ps+=" | Where-Object { \$_.LastWriteTimeUtc -gt [datetime]::Parse('$since').ToUniversalTime() }"
  ps+=" | ForEach-Object { \$_.FullName.Substring(\$root.Length+1) } | Set-Content -Encoding ascii \$list;"
  ps+=" [Console]::Error.WriteLine('hn: ' + (Get-Content \$list).Count + ' file(s) to stream');"
  ps+=" tar -cf - -C \$root -T \$list"
  "${SSH[@]}" "$HN_HOST" "$ps" | tar -xf - -C "$stage" \
    || { rm -f "$started"; rm -rf -- "$stage"; echo "hn: tar stream failed (host down? key not loaded?)" >&2; return 1; }
  "$RSYNC_BIN" -a "$stage/" "$RAW/" \
    || { rm -f "$started"; rm -rf -- "$stage"; echo "hn: archive merge failed" >&2; return 1; }
  rm -rf -- "$stage"
  # Back the stamp off by a day: the two clocks are not the same clock, and a
  # transcript that grows during the pull must be seen again. Re-fetching a day
  # of files is cheap; a missed grown transcript is not.
  python3 -c 'import os,sys; stamp,started=sys.argv[1:]; t=os.stat(started).st_mtime-86400; open(stamp,"a").close(); os.utime(stamp,(t,t))' "$stamp" "$started"
  rm -f "$started"
  echo "hn: synced"
}

machines=("$@")
if [ ${#machines[@]} -eq 0 ]; then
  if [ "$(uname -s)" = Darwin ]; then machines=(local linux hn)
  else machines=(local air hn); fi
fi

failed=()
for m in "${machines[@]}"; do
  case "$m" in
    local) fetch_local || failed+=("$m") ;;
    air)   fetch_air   || failed+=("$m") ;;
    linux) fetch_linux || failed+=("$m") ;;
    hn)    fetch_hn    || failed+=("$m") ;;
    *) echo "unknown machine '$m' (local|air|linux|hn)" >&2; exit 2 ;;
  esac
done

python3 "$REPO/tools/render_corpus.py"
python3 "$REPO/tools/build_corpus_index.py"

if [ ${#failed[@]} -gt 0 ]; then
  echo "NOT refreshed: ${failed[*]}" >&2
  exit 1
fi
