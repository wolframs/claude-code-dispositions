# Durability — applicable, reversible, update-surviving

The threat model is Anthropic's release cadence: sometimes five CC versions in
a week, each update silently replacing the patched binary, with tweakcc's
prompt data lagging each new version by hours to days. Unmanaged, that means
recurring windows of silently running stock. The design closes every silent
path.

## The four layers

**1. Repo as source of truth.** `edits/*.md` (tweakcc fragment format) plus
`edits/MARKERS.txt` — one distinctive literal substring per edit. Machine-
independent, versioned, portable to the work machine by `git clone`.

**2. Verified apply — `ccctl.py apply` / `update`.** Refuses to run when
tweakcc lags the installed version; applies every fragment and adhoc to a
*staged* copy; parses every changed module; then greps the *binary* for each
marker in `edits/MARKERS.txt` and asserts every anti-marker absent, before the
swap. "Patched" always means verified-in-binary, never "apply exited 0".
(`tools/patch.sh` and `tools/apply-win32.py` are retired; the files that remain
are three-line shims that exec `ccctl_compat.py`.)

**3. Per-session tripwire.** A `SessionStart` hook in `~/.claude/settings.json`
runs `ccctl.py status --quiet`: silent when the binary carries all markers
(or no edits exist), one warning line into the session when it does not. This
is the layer that makes the update race harmless — the failure mode changes
from "running stock for days without knowing" to "told at the next session
start". Settings survive CC updates; the binary does not.

The tripwire cannot report its own death. If the script it names is gone,
python exits 2, CC drops the failed hook without a word, and every session
starts silent, exactly like a patched one. On macOS the hook pointed at
`~/cc-dispositions/tools/ccctl.py` for an unknown number of rounds after that
directory went away (found 2026-09-11). So the plain `ccctl.py status` checks
the hook from outside and prints a `tripwire` line (`tripwire_state()`): `ok`,
`BROKEN` when any path the command names (the script, or the `cd` target that
becomes ccctl's workspace) does not exist, or `NONE` when no SessionStart hook
runs ccctl at all. It is advisory, like `server arms`, and does not gate
`--check`.

It cannot report its own timeout either. A hook that runs past its `timeout`
is killed, CC records `hook_cancelled`, and the session starts silent. On Linux
that happened at every session start from 2026-08-26 to 2026-09-21: the check
took 14 s against 10 s, because three adhoc regexes scanned the whole bundle
byte by byte (notes/2026-09-21-length-regime.md). Three views watch for it now:

- the quiet run times itself and prints `[ccctl] TRIPWIRE SLOW` into the
  session once it passes half the hook's timeout, while it can still speak;
- `status` times a quiet run from outside and reads `SLOW` or `KILLED`
  against the timeout;
- `status` reads CC's own record of kills from the newest transcripts, with
  the date and how many session starts have passed since.

Half the timeout is the margin because a session start is the slowest moment
to run the check: plugins, MCP servers and the first request all start at
once.

Since 2026-09-03 it has a second thing to say. In the **stock arm** (spec/40)
the binary is stock on purpose, and the tripwire names the arm instead of
telling the operator to re-apply — silence would be defensible, since nothing
is wrong, but the failure being guarded against is forgetting which arm a
session is in, and a stock session looks exactly like a patched one. The
declaration lives in `ccctl-state.json`, and ccctl also mirrors it into a
one-line `stock-arm` file in its state dir (`${CCCTL_HOME:-$HOME/ccctl}`) —
originally because the doctor was bash and had to stay honest without a python
on PATH. Since 2026-09-05 the doctor delegates to ccctl itself, so nothing
reads the sentinel any more; it is kept as an out-of-band record an operator
can `cat` without running anything. Both directions were tested on win32 that
day, which is the only way a tripwire is ever known to work: arm declared → the
arm line; sentinel hidden → `STOCK PROMPTS: 0/N … run ccctl.py apply`, where N
is whatever `edits/MARKERS.txt` holds at the time.

**4. Deliberate updates.** The auto-updater is off (`DISABLE_AUTOUPDATER=1` in
settings env — the `autoUpdates` settings key has not existed since 2.1.234). **Incident 2026-08-19:** settings env covers *sessions* only. A bare
`claude --version` from patch.sh itself pulled 2.1.236 and flipped the
`~/.local/bin/claude` symlink mid-apply — the exact silent path this document
claims to close. Closed for real: every tool that shells out to `claude`
exports `DISABLE_AUTOUPDATER=1` first, and the symlink was re-pinned at the
time to 2.1.234. Any script anywhere that runs `claude` bare is an update
trigger; ccctl.py carries the same guard on all three OSes.

Updates go through `ccctl.py analyze` then `ccctl.py update` (spec/40): the
drift analysis happens *before* anything on the machine changes, the incoming
binary is staged and checksum-verified, every target's locator is re-derived
against it, and the swap happens only after every marker verifies in the staged
copy. Riding every release is exactly what makes the 5-versions-a-week cadence
a problem; updating on purpose makes it a non-event. (`tools/update-cc.sh`
updated first and drift-checked after — the wrong order — and was retired
2026-08-21.)

## Reversibility

Three independent paths, strongest first:

1. `tweakcc --restore` — byte-exact restore from `~/.tweakcc/native-binary.backup`.
2. Any CC update or reinstall — ships a stock binary by construction.
3. The repo — every edit is versioned; the payload can be re-derived or
   partially re-applied at any time.

Settings side: remove the `SessionStart` hook block and the
`DISABLE_AUTOUPDATER` env entry from `~/.claude/settings.json`.

## Status and sequencing

Infrastructure is live and tested (hook pipe-tested both paths; doctor reads
true state). `edits/` carries the authored fragments and `edits/targets.json` the full target list. The probe-battery
apply gate is dissolved — the battery is retired (operator decision
2026-08-19, `spec/20`), and with it the stock-window argument: there is no
patched arm to protect. Order is now: author edits (fragments + adhoc) →
patch → the operator lives with it, and new complaints are the measurement.

Known residual risks, accepted:

- tweakcc lag: with auto-update off this costs waiting, not silent stock.
- A CC version that *rewrites* an edited fragment: caught by `analyze`'s
  per-target verdicts (it reads `REVIEW`), resolved by re-reviewing that edit against the new text.
- Mode- and platform-divergent prompts (C-04): the stock prompt is not one
  text. The auto-mode shell block is injected only under bypass-permissions
  mode, on every OS, and one C-04 point remains unlocated outside win32. Consequence: per-machine `ccctl snapshot` is the authority
  for what each binary says, edits must be marker-verified on *each*
  machine, and a marker that fails on one platform must fail loudly there,
  never skip. Any win32-only edit gets its own fragment file, not a variant
  of a shared one. Stock captures must state their permission mode; a
  capture taken in default mode does not describe a bypass-mode session.
- Other machines: `tools/ccctl.py` is the cross-platform form of all of this —
  one stdlib-only Python file for Kubuntu, macOS and Windows. Copy it once per
  machine, `ccctl.py init <git-url>` (sparse clone, ~1 MB — corpus and
  baseline stay on the server), then `apply` / `status` / `check-update` /
  `diff stock A B` / `diff custom` / `restore`, with a per-machine changelog
  and per-version stock snapshots in the folder it runs from. `status --quiet`
  is hook-compatible on all three OSes. The script validates its own
  toolchain: git and tweakcc presence, tweakcc >= 4.3, and that the resolved
  `claude` is the real native binary (size + content) — seeing through
  launcher shims by falling back to the versions directory, and failing
  loudly on a broken or ancient tweakcc.
