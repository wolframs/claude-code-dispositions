# Update pipeline — analyze-before-update, across three OSes

## Problem

Anthropic ships CC updates often (sometimes twice daily). Staying pinned for
long accumulates risk (they fix bugs as fast as they ship new ones); updating
blindly reverts the tranche and can land on a version where the edit targets
drifted or tweakcc lags. The update path **was** split when this was written:
`update-cc.sh` (linux, updated FIRST then drift-checked — wrong order),
`patch.sh` (linux apply), `apply-win32.py` (win32 apply), `ccctl.py`
(cross-platform control, but its `apply` was fragment-only via
`tweakcc --apply`, which no-ops for fragments on win32, and it never applied
the adhoc patches). The design below replaced all four; `ccctl.py` is now the
single engine and applies every fragment and adhoc on all three OSes.

## Target

One cross-platform pipeline, `ccctl.py update` (+ `ccctl.py analyze`), that:

1. Discovers the incoming CC version (channel-configurable) without installing.
2. Checks whether tweakcc supports it yet (prompt data availability) and
   whether tweakcc itself has a newer release.
3. Downloads the incoming binary to a staging area — the live install is
   untouched until the very last step.
4. Diffs installed→incoming: full stock-prompt diff (added/removed/changed,
   flagging fragments we edit), windowed probes of our edit targets against
   the incoming binary, adhoc anchor re-derivation against the incoming
   binary.
5. Produces a patchability verdict per edit target:
   - `AUTO` — target locates cleanly, upstream text unchanged.
   - `REVIEW` — upstream changed the fragment's text; our whole-span replace
     would silently discard their change. Auto-apply is *possible* but the
     operator must re-review the edit against the new stock text first.
   - `MANUAL` — span not locatable / interpolation structure changed / adhoc
     anchor regex missing. Needs a human or agent to re-derive.
6. Emits a report (stdout + `analysis/<old>-to-<new>.md` + `.json` in the
   ccctl state dir): the verdicts, the relevant diffs, and *concrete
   instructions* for whoever fixes the manual items (which file to edit,
   which command re-checks, what "done" looks like).
7. On a clean analysis (policy-gated): applies everything to the **staged**
   copy, verifies all markers there, launch-tests it, then swaps it into
   place and re-verifies. Rollback is the parked pre-swap binary plus the
   pristine versions dir.

## Non-goals

- Riding every release automatically. The pipeline makes deliberate updates
  cheap; it does not re-enable auto-updating. `DISABLE_AUTOUPDATER=1` stays.
- Replacing the tripwire. cc-doctor / `ccctl status --quiet` remain the
  per-session guard.

## Design decisions

**D1 — extend ccctl.py, no new script.** spec/30 already names ccctl.py the
cross-platform form of this tooling; it has binary resolution (shim-aware),
tweakcc gating, the win32 `.cmd` spawn guard, snapshots, changelog, state.
The update pipeline is two new subcommands plus an apply-engine refactor.
`update-cc.sh` is retired (superseded); `patch.sh`/`apply-win32.py` remain as
thin per-platform manual fallbacks until ccctl's engine has been exercised on
linux too, then get deprecation headers.

**D2 — analyze before touching anything.** update-cc.sh updated first and
drift-checked after, leaving you on the new version with a reverted patch
when drift was found. The new order: stage → analyze → (gate) → patch staged
copy → verify staged → swap. At no point is the live install unpatched or
half-patched.

**D3 — one apply engine, span-based, all platforms.** apply-win32.py's
mechanism (locate each fragment's template-literal byte span via its longest
unique piece, splice edited static text around the original interpolation
bytes, write via `tweakcc adhoc-patch`; re-derive the 2 adhoc patches per
build) is platform-independent — nothing in it is win32-specific except the
swap. It becomes ccctl's default engine everywhere (`applyEngine: "span"`),
because it verifies byte-precisely and does not depend on tweakcc's
fragment-writer (which demonstrably differs between platform builds).
`applyEngine: "tweakcc"` remains available as config for the linux-proven
`--apply` path. The engine always runs against a staged copy, never the live
binary; swap is rename-based on every OS (atomic-enough, and the only option
on win32 anyway).

**D4 — adhoc patches are first-class.** ccctl apply gains the adhoc step on
all platforms (currently only patch.sh/apply-win32 have it). Anchors are
re-derived per build via the regexes (minified identifiers differ per
platform build); `edits/adhoc-<ver>.json` stays as documentation of intent,
the regexes in code are the executable form.

**D5 — configuration over policy guesses.** New ccctl.json keys (all with
defaults, all overridable per machine):

| key | default | meaning |
| --- | --- | --- |
| `channel` | `"stable"` | which release pointer `analyze latest` consults |
| `applyEngine` | `"span"` | `"span"` or `"tweakcc"` (see D3) |
| `updatePolicy` | `"gate-on-clean"` | `"analyze-only"` (never applies), `"gate-on-clean"` (applies only if every verdict is AUTO), `"force"` (applies AUTO+REVIEW, still refuses MANUAL) |
| `keepStaging` | `false` | keep the staged binary after a successful swap |
| `macosCodesign` | `true` | macOS: ad-hoc re-sign the patched binary (mandatory on Apple Silicon — see below) |

**D6 — the report is written for the next agent, not just the operator.**
Manual-fix instructions name the exact fragment file, show the upstream diff
hunk, state the re-check command (`ccctl.py analyze <ver>`), and define done
("verdict flips to AUTO"). This is what makes a lagging-tweakcc morning or a
drifted-fragment morning a 10-minute agent task instead of archaeology.

## Version discovery + download (verified 2026-08-21, first-hand)

Base: `https://downloads.claude.ai/claude-code-releases` (the GCS bucket
mirrors it but the claude.ai name is the documented endpoint).

- Channel pointers, plain text: `<base>/stable`, `<base>/latest`.
- Per-version manifest with SHA256 + size per platform:
  `<base>/<ver>/manifest.json`.
- Binary: `<base>/<ver>/<platform>/claude` (`claude.exe` on win32).
- 8 platforms: `linux-{x64,arm64}[-musl]`, `darwin-{x64,arm64}`,
  `win32-{x64,arm64}`. Platform detection copies install.sh: musl via
  `ldd /bin/ls | grep musl` (or `/lib/libc.musl-*`), Rosetta via
  `sysctl -n sysctl.proc_translated` = 1 → treat as arm64.
- Old versions stay downloadable. Every download is checksum-verified
  against the manifest before it is used.
- `claude update`'s own channel handling has a bad track record
  (anthropics/claude-code#69319 et al.) — the pipeline reads the pointers
  itself and never shells out to `claude update`/`claude install`.

## Install layout (verified from the shipped 2.1.234 code)

Same XDG-derived layout on macOS and Linux — versions at
`~/.local/share/claude/versions/<ver>` (no extension), launcher at
`~/.local/bin/claude`:

- **POSIX: the launcher is a symlink** into versions/. Anthropic activates
  by `symlink(tmp)` + `rename(tmp→launcher)`.
- **win32: the launcher is a full file copy** of versions/<ver> (not a
  hardlink). Anthropic activates by rename-aside + copy + unlink, and
  short-circuits when launcher size == version-file size.
- Nothing is ever written in place on any OS; the running binary can be
  renamed everywhere.

Placement of the *patched* binary therefore differs per platform, each
matching its launcher semantics:
- win32: `versions/<ver>` stays pristine (installer invariant); the patched
  binary is the launcher copy at `~/.local/bin/claude.exe`.
- POSIX: the symlink must resolve to a patched file, so the patched binary
  *becomes* `versions/<ver>` and the pristine one is parked alongside as
  `versions/<ver>.stock` (pristine_source knows to look there).

### Existing sessions and the background daemon

Atomic placement preserves existing executable mappings; it does not prevent
CC's own services from reacting to the new launcher. On the Mac's 2026-09-28
upgrade the daemon detected the .280 to .283 target change and self-restarted,
replacing spare helpers while adopting its existing worker without respawning
it (`notes/2026-09-28-macos-2.1.283.md`). The updater sent no signals, but that
is not proof of zero process effects.

**A resumed session keeps its old prompt** (measured 2026-10-08, CC 2.1.294,
`notes/2026-10-08-win32-2.1.294.md` §6). CC stores the system prompt and tool
descriptions of a session's first request as a `prompt_snapshot` attachment in
the transcript, and on resume replays that snapshot instead of rendering the
prompt from the binary (`UJe`: on unless `CLAUDE_CODE_SIMPLE`, or the host
passes `systemPromptSnapshot:false`). Only a later `prompt_render_point`
supersedes it, and the one cause in the 2.1.294 bundle is a model switch that
changes the system prompt. Instruction files (CLAUDE.md) are re-read on resume;
the prompt is not. So "start a fresh session to use it" means a new session:
`--resume`, `--continue` and a restarted resumed tab still run the prompt they
started with, on any binary. `status --delivered` captures fresh sessions only.

Before a session-preserving upgrade, record PID, parent PID, start time and
process role, including any `claude daemon`. After activation compare those
identities and inspect `~/.claude/daemon.log` for upgrade handover and worker
adoption/death, alongside prompt/signature checks. Distinguish preserved work
from service restarts in the report. A strict requirement that *no process*
change is incompatible with activating a new launcher while a watching daemon
is running; stage and verify first and resolve the activation constraint rather
than promising atomic replacement alone satisfies it. Do not kill or disable
the daemon just to make the process comparison appear clean.

## macOS signing (VERIFIED on real hardware 2026-08-21)

Exercised end-to-end on an M2 MacBook Air, macOS 26.5.2, CC 2.1.232: clean
machine (no tweakcc, no prior patch), `status` → `analyze` (CLEAN, 9/9 AUTO)
→ `apply` → POSIX `place()` → 9/9 verified in the live binary, launches.

**The spec's own remedy was wrong, and only the real run showed it.**
`codesign --force --sign - --preserve-metadata=entitlements` produced a
binary with **zero** entitlements (stock has five). Cause: tweakcc's
`repackMachO` strips the Developer ID signature and ad-hoc re-signs *itself*
during the patch write, so by the time our codesign runs there is no
signature left to preserve from — and the identifier had already been
rewritten to the staging filename. Fixed in `macos_resign()`: identifier and
entitlements are read off the **pristine** binary and passed explicitly
(`--entitlements <plist> --identifier com.anthropic.claude-code`), then the
result is re-read and compared, dying if anything was dropped. Verified
after the fix: `Identifier=com.anthropic.claude-code`, all 5 entitlements
present, `codesign --verify --deep --strict` clean, binary launches.

Hardened runtime is deliberately not restored — ad-hoc signatures have no
Team ID, and without the runtime flag JIT (what the Bun payload needs) is
permitted by default. TCC grants still reset, since ad-hoc has no stable
identity; the entitlements being present means the prompts can appear at all
rather than the capability being denied outright.

## macOS signing — background (research)

The shipped darwin binaries are Developer-ID signed, notarized, hardened
runtime, with entitlements (JIT, unsigned-exec-memory, audio-input,
apple-events). Any byte edit invalidates the CodeDirectory; on Apple
Silicon the kernel SIGKILLs unsigned/invalid binaries (`Code Signature
Invalid`, exit 137). Remedy after patching:

    codesign --force --sign - --preserve-metadata=entitlements <staged>

(`--preserve-metadata=entitlements` because a bare `codesign -s - -f` —
what tweakcc runs — silently drops the entitlements and resets TCC grants.)
Verification: `codesign --verify --deep --strict -vv` plus a launch test;
never `spctl --assess` (rejects even pristine CLI binaries). No quarantine
xattr handling needed (nothing here goes through a browser).

Related upstream hazard: tweakcc's LIEF-based Mach-O repack is lossy
(Piebald-AI/tweakcc#683, open) — one more reason the span engine's
in-place-length-changing-but-LIEF-free writes go through `adhoc-patch`
only for the *string replacement*, applied per platform build. Not observed
on the 2026-08-21 run (patched binary 306,126,416 B vs stock 306,111,312 B,
i.e. it grew by our payload delta rather than shrinking ~1.1 MB), but the
issue is open upstream — compare sizes after any macOS apply.

## tweakcc up-to-date check

Two independent facts, both in the analyze report:
1. **Prompt data for version X exists** — tweakcc downloads
   `https://raw.githubusercontent.com/Piebald-AI/tweakcc/refs/heads/main/data/prompts/prompts-<ver>.json`
   into `~/.tweakcc/prompt-data-cache/` on demand (verified in its source;
   200/404 observed live). The pipeline probes that URL directly (HEAD) and
   can download the JSON itself; running tweakcc is the fallback. 404 ⇒
   tweakcc lags this CC version (upstream says "supported within a few
   hours").
2. **tweakcc itself has a newer release** — `npm view tweakcc version` vs
   installed. Informational only; never auto-updated. (tweakcc has no
   self-update check of its own — verified in source.)

## tweakcc facts the pipeline is built around (source-verified)

- `adhoc-patch -s old new -p <path>` replaces **all** occurrences (no
  index given), writes to `<path>` directly, never touches the backups.
  Success line: `✓ Replaced N occurrence(s)`. The span engine therefore
  verifies span uniqueness *before* every patch. "All occurrences" means
  all occurrences *in the live bundle it extracts* — measured on linux
  2.1.241 with two copies of the target string in the file, it still
  reported `Replaced 1`. Uniqueness must be judged the same way; see the
  live bundle window below.
- `tweakcc --apply` **always restores from `native-binary.backup` first**,
  then re-applies only its config-driven patch set — i.e. it wipes adhoc
  patches. The span engine never uses `--apply`; the legacy `tweakcc`
  engine option orders fragments-then-adhoc for this reason.
- The backup is refreshed once per CC version — a fresh backup snapshots
  *whatever binary is live at that moment*, so a patched binary can poison
  it. `pristine_source()` never trusts a backup blindly: it checks markers
  (must be stock) and version before use.
- The win32 fragment no-op has a likely root cause: tweakcc's PE repacker
  (`repackPE`) lacks the segment-extension logic its ELF/Mach-O paths
  have. Documented in tools/apply-win32.py's `[remark]`.

## The live bundle window (linux; measured 2026-08-24)

CC is a Bun `--compile` executable. The JS blob is framed `[u64 length][blob]`
and ends in a 16-byte trailer `\n---- Bun! ----\n`; ELF carries it in a section
named `.bun`, Mach-O in `__BUN,__bun`, PE in `.bun`. The runtime does not read
the section header — it follows an 8-byte little-endian vaddr (`BUN_COMPILED`)
parked in a 16 KiB-aligned word inside the writable `PT_LOAD`, a placeholder
page Bun's own `--compile` sets up and then repoints. tweakcc replays that
trick.

**Legacy repacker behavior: `repackELFSection` relocates; `repackMachO` and
`repackPE` do not.** On the old Linux repacker path every write — `--apply` and each `adhoc-patch` alike — appends a fresh copy of
the bundle past the end of the writable `PT_LOAD`, repoints both the section
header and `BUN_COMPILED`, extends the segment, and **abandons the previous
copy in the file**. Nothing ever truncates. Measured on 2.1.241: +253 MB per
invocation, 343 MB → 596 MB → 850 MB on two sequential patches; a full update
(1 `--apply` + 8 span patches) lands at 2.6 GB with 10 copies, 1 live.

Current code-split builds take `bun_source_patch` before the legacy adhoc
fallback. It edits the owning module within the bundle window, updates tables
and invalidates stale bytecode without appending a new ELF bundle per target.
The Linux 2.1.283 full patched build measured 214244536 bytes; the old bloat
finding no longer describes this path. Regression coverage is in
`tools/tests/test_ccctl_hardening.py`; measurements are in
`notes/2026-09-28-linux-2.1.283.md`. The live-window rule below still matters
for any file containing old copies.

Consequences the pipeline now encodes:

- **Uniqueness is only meaningful within the live window.** Whole-file, every
  bundle string occurs once per generation, so `locate_span` finds no unique
  anchor and the adhoc regexes go ambiguous. This is why the span engine had
  never run on linux — the failure was read as "targets no longer locate after
  tweakcc's patch set" and blamed on tweakcc's patches rather than on where we
  were looking.
- **Marker verification was accidentally right.** A marker anywhere in a
  multi-copy binary counted as patched, including in an abandoned copy. Only
  markers in the live window mean the running prompt is patched.
- `bun_window()` derives the window from the `.bun` section header and
  **validates** it — `8 + u64_header == sh_size`, and the trailer present at
  the end. A half-parse returns None, and callers fall back to whole-file, so
  macOS and win32 behaviour is untouched. The section header is what tweakcc
  writes and reads back, so it names the live copy; `BUN_COMPILED` is the
  deeper authority if the two ever disagree.
- Two questions stay deliberately whole-file: `_is_real_cc_binary` (asked
  before any bundle can be parsed) and `is_stock` (our text in an abandoned
  copy still disqualifies a binary from being parked as pristine).

**Open.** Nine repacks per update is nine appends. Collapsing the tranche into
a single write — `adhoc-patch --script`, or repeated `-s` if its variadic
option takes pairs — would bring a patched linux binary back to ~600 MB. See
TODO item 7.

## Autoupdater hardening (research finding, applied)

`DISABLE_AUTOUPDATER=1` only stops the *background* check; `claude update`
and `claude install` still work. `DISABLE_UPDATES=1` blocks all update
paths and is the documented switch for exactly this distribute-your-own-
binary workflow. Both are set in `~/.claude/settings.json` env and
exported by every script here before any `claude` invocation. (The
pipeline itself never needs `claude update` — it downloads directly.)

## Runbook

For agent work over SSH, [spec/50](50-agent-ssh.md) provides noninteractive
execution and a same-version deployment wrapper with locking, backups and
automatic binary recovery after failed verification. Release updates still
follow this runbook.

Per machine, once: copy `tools/ccctl.py` somewhere, `ccctl.py init <git-url>`
(sparse clone), optionally edit `ccctl.json` (see the config table above).
All three machines use `~/ccctl/` as the workspace.

Then the two durability guards (spec/30), which the pipeline assumes:

- `~/.claude/settings.json` → `env`: `DISABLE_AUTOUPDATER: "1"` **and**
  `DISABLE_UPDATES: "1"` (the first only stops the background check).
- a `SessionStart` hook running the tripwire, so any session on an
  unexpectedly-stock binary says so:
  `cd ~/ccctl && python3 <repo>/tools/ccctl.py status --quiet`
  Verified both directions on macOS 2026-08-21: silent at 9/9, and
  `[ccctl] STOCK PROMPTS: 9/10 markers … — run ccctl.py apply` when a marker
  is missing. An always-silent tripwire is worthless — test both. Point it at
  the ccctl clone (`~/ccctl/repo`), which `ccctl.py pull` keeps current, not
  at a working clone that can move. `status` prints a `tripwire` line that
  says whether the paths the hook names still exist (spec/30).
- **No** `CLAUDE_CODE_SUBAGENT_MODEL` and no `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`
  in `~/.claude/settings.json` → `env`: since 2026-09-29 the operator runs CC's
  default subagent models, to see how Sonnet 5.5 plays out (it replaced the
  2026-09-22 Opus pin, below). `status` prints a `subagent model` line either
  way; do not assume a machine has dropped the pin, read `status`.

**Sibling read-back, after any change to our text** (operator, 2026-10-08:
"You can also query a sibling opus `claude -p` process to ask them about
details, they needn't re-derive the whole complexity, just confirm some
questions you ask them"). `status --delivered` proves the bytes of a fresh
session's first request; this asks a fresh session what it actually reads.
One turn, on the operator's main model, from the repo:

```
claude -p --model opus "Read-only check, no tools needed. 1) Quote verbatim, in full,
the section of your system prompt headed '<section>'. 2) Answer yes/no for each: does
your system prompt contain the exact phrases (a) '<new phrase>', … (z) '<removed
phrase>'? 3) Does your user CLAUDE.md context contain '<new block>'? Answer compactly."
```

List the round's new phrases and at least one it removed: a "no" on the
removed one is the half that proves the old text is gone. Paste the yes/no
lines into the round note. It needs no ccctl support and stays a procedure:
the questions change every round, which is what `status --live`'s fixed
first-sentence probes cannot follow. The agent that ran the update cannot do
this by reading its own prompt if it was resumed (see "Existing sessions"),
and a fresh sibling is cheaper than asking the operator to restart.

Routine update:

```
ccctl.py analyze              # channel pointer decides the target
                              # exit 0 clean / 2 review / 3 manual / 4 tweakcc lagging
ccctl.py update               # only after analyze is clean
```

Reading the exit code (also the report's `overall`):

- **0 CLEAN** — every target auto-appliable. `update` and move on.
- **2 REVIEW** — upstream changed the stock text of a fragment we edit. The
  report carries the upstream hunk. Fold anything worth keeping into
  `edits/<fragment>.md`, record the exact reviewed stock/replacement hashes and
  reason in `edits/reviews.json` (see edits/README.md), then re-run `analyze`.
  Saving an edit alone is not a review acknowledgement. Deliberately ignoring their change
  is `update --policy force`.
- **3 MANUAL** — a target can't be located: fragment removed/renamed upstream,
  adhoc anchor rewritten, span mis-anchored. The report names the fix per
  target, including candidate successor fragments when one was removed.
  Nothing is applied until every target is back to AUTO.
- **4 BLOCKED** — tweakcc's prompt data doesn't cover the target version yet
  (upstream normally ships it within hours). Wait and re-run, or use the
  maintainer-PR trust gate and cache-seeding procedure in
  `spec/45-pre-merge-tweakcc-data.md`.

Re-apply for the *installed* version (e.g. something reverted the binary —
the tripwire says so at session start): `ccctl.py apply`.

The B arm (see "The stock arm" below):

```
ccctl.py update --stock [VER]   # new version, Anthropic's defaults, verified
ccctl.py apply --stock          # same, without changing version
ccctl.py apply                  # ends the arm: the tranche goes back on
```

`status --check` exit codes: **0** patched and verified, **2** something is
wrong (markers missing, deleted text back, an adhoc not landed, or state and
binary disagreeing), **3** deliberately in the stock arm.

Rollback, strongest first: `ccctl.py restore` (tweakcc's byte-exact backup);
the parked pristine (`versions/<ver>.stock` on POSIX, `claude.exe.pre-swap*`
in the launcher dir on win32); any reinstall.

Tests: `python tools/tests/test_ccctl_hardening.py` and
`test_ccctl_posix_place.py` — sandboxed, no real binaries, meaningful on
every OS (the POSIX one forces the POSIX branch even on win32).

### After a prompt, locator or release change in the repository

Before the apply, make the repository agree with itself; the binary checks
above prove a machine, not the repo.

1. A new CC release: add `baseline/<ver>/prompts.json` from the reviewed
   source (spec/45), point `release.json` at it with its SHA-256, move the
   tweakcc pin and overlay there too (`build_local_tweakcc.py` reads them from
   `release.json`), and record the pristine's SHA-256 per platform as
   `download_binary` measures it.
2. A changed fragment: update `edits/reviews.json` (edits/README), and
   `MARKERS.txt`/`ANTIMARKERS.txt` if a marker sentence moved.
3. `python3 tools/build_ab.py` against the parked pristine, then
   `python3 tools/check_repo.py`. The first refuses a binary `release.json`
   does not name and fails on any non-AUTO target; the second fails on a stale
   `ab/data.json`, a broken active link, a review hash that no longer matches,
   or a failing test.

Then `apply`/`update` on the machine being claimed, and only that machine.

### Bundled copies of Claude Code

`ccctl` patches one binary per machine, the one `~/.local/bin/claude` names.
Several hosts ship their own stock copy instead and launch it unless told
otherwise, and a session started that way carries none of our text. Operator,
2026-10-08: "why not patch *all* the things". The policy is to **redirect,
not patch**: every host that can be pointed at an executable is pointed at
the installed binary, so one `update` covers them all. Patching each bundled
copy is not an option, because our locators are derived per CC release and
those copies lag by dozens of releases.

| host | its bundled copy | how it is redirected |
| --- | --- | --- |
| Python Agent SDK | `claude_agent_sdk/_bundled/claude`, tried **before** PATH | `ClaudeAgentOptions(cli_path=…)` in the calling code |
| TypeScript Agent SDK | `cli.js` or `claude-agent-sdk-<platform>/claude` | `pathToClaudeCodeExecutable` in the calling code |
| VS Code extension | `resources/native-binary/claude` | `claudeCode.claudeProcessWrapper` = `~/ccctl/repo/tools/claude-process-wrapper.sh` (the host calls `wrapper <bundled> args…`; the wrapper drops the bundled path) |
| T3 Code | none; it runs `claude` from PATH, and its server's PATH can lack `~/.local/bin` | `providers.claudeAgent.binaryPath` in `~/.t3/userdata/settings.json` |
| Claude desktop app, VS Code's own agent host | app-managed downloads | no supported override; not redirected |

Neither SDK has an environment variable for this, so the redirect lives in
each project's code, with the bundled copy as the fallback when the path is
missing. Which project does what, per machine, is in that machine's round
note (macOS: `notes/2026-10-08-macos-2.1.294.md`; Linux:
`notes/2026-10-08-linux-2.1.294.md`).

To find a host that slipped through: every session writes its CC `version`
and `entrypoint` into its first transcript lines under `~/.claude/projects/`.
A version other than the installed one is a bundled copy at work.

## tweakcc's own patch set, and the in-CC indicator

**Rule (operator, 2026-08-24): `tweakcc --apply` always runs, and always
before our string patches.** Not for tweakcc's QoL patches — for its
`patches-applied-indication` banner. Because the banner can only reach the
live binary through this pipeline, and the pipeline refuses to swap unless
every one of our markers verifies in the staged copy, the banner becomes a
reliable at-a-glance signal: **banner in CC's opener ⟹ our patches are live;
no banner ⟹ they are not.** `status` cross-checks the two and says so loudly
if they ever disagree.

Note what the banner alone does *not* prove. It lists tweakcc's *configured*
prompt edits with char deltas, computed from `~/.tweakcc/system-prompts/`,
not from the binary — during the win32 investigation it cheerfully reported
all 7 fragments applied while the binary had 0/9. The coupling above comes
from the pipeline's verification, not from the banner's own claims.

Implementation (`tweakcc_apply_to`), both hazards measured on win32:

- `--apply` has no path flag; it patches whatever `ccInstallationPath` in
  tweakcc's config points at, restoring from its backup first. We point it at
  the staged copy and restore the config afterwards, so the live install is
  never a target. Getting this wrong aims tweakcc at the live binary — it
  happened once during development, and only tweakcc's own failure to
  produce a parseable bundle prevented a revert.
- `--apply` also writes any fragments in `~/.tweakcc/system-prompts/`. On
  linux `patch.sh` copies OUR edits there, which would pre-modify the text
  the span patches then look for. The directory is hidden for the duration.

- `--apply` restores from `~/.tweakcc/native-binary.backup` before patching,
  and that file is whatever binary tweakcc last touched — after one of our
  rounds, our own patched output (measured on win32 2026-08-25, byte-identical
  to the previous round's binary). Left in place it replaces the staged
  pristine copy with already-patched bytes and every target stops locating.
  `park_patched_backup()` moves a non-stock backup aside first; the same file
  is refused by `restore`, which would otherwise report "restored to stock"
  while leaving every edit in place.

Because tweakcc rewrites parts of the binary, the plan is re-located against
the staged bytes afterwards rather than trusted from the pristine pass.

**Fallback when a build rejects the patch set.** If `--apply` fails, or the
re-location finds targets that no longer locate, the run does not stop: the
staged copy is discarded, a fresh one is taken from the pristine source, and
the tranche is applied without tweakcc's patches. Ours never depended on them
— the patch set is wanted for its in-CC banner. The fallback is recorded in
`ccctl-state.json` so `status` reports the indicator as *unavailable* rather
than as a disagreement, and it is cleared by the next assemble whose patch set
does land. This tests the build in front of it instead of predicting from the
platform, which is what `tweakccPatches`' default can only do.

**macOS exception (`tweakccPatches: false` there — the platform default since
2026-08-25, derived in `TWEAKCC_PATCHES_DEFAULT`, not written into any
config).** tweakcc 4.3.3 cannot
patch CC 2.1.241 on darwin at all: several patches fail to find their
anchors and the resulting bundle fails to parse, so tweakcc reverts and
applies nothing. The pipeline caught this by its own rule — no banner in the
staged copy meant no swap — and the Mac kept its verified 8/8 binary. So the
Mac runs without the indicator until tweakcc supports that build; `status`
reports the indicator as off rather than as a disagreement. Retry after a
tweakcc release.

## The stock arm — the B side, made first-class (2026-09-03)

The project's only instrument is the operator living with the patch on real
work (spec/00 §10.1, README "Method" step 5), and that is a comparison: this
tranche against Anthropic's defaults, on the same day's work. Until now the
pipeline could build the A arm or refuse to build anything. There was no route
to the B arm that did not mean hand-copying a downloaded binary past every
check the pipeline exists to enforce — so "spend today on the stock prompts"
cost more than it was worth, and the comparison went unmade.

`update --stock [VER]` and `apply --stock` build it: pristine bytes plus
tweakcc's own patch set, **none of ours**. Same staging, same launch test,
same swap, same rollback artifacts. Two things make it a state rather than an
absence of one:

1. **The inverse assertion.** A stock build is verified by every marker being
   ABSENT (`assemble(stock=True)`, `post_verify(stock=True)`). A marker found
   there stops the swap: either the source was not pristine, or upstream has
   converged on wording this repo also uses — both are worth knowing. Skipping
   the patch step is not evidence that nothing was patched; the check is.
2. **The declaration.** Zero markers by intent and zero markers by accident
   are the same bytes. `ccctl-state.json`'s `stockArm` is what separates them,
   and it is what every check consults first. `applied` is left untouched — it
   is the true record of when the tranche last went on, and folding the arm
   into it would make `status` misreport its own history.

Consequences, all of them deliberate:

- **`status --check` exits 3 in the arm**, never 0 and never 2. Not 0: the
  tranche is not live and a script asking "is it patched" must not read yes.
  Not 2: an agent that reads FAIL will helpfully re-apply and end the
  experiment it was hired to run. If state declares the arm and markers *are*
  live, that IS exit 2 — state and binary disagree, and one of them is wrong.
- **The tripwire speaks in the arm rather than falling silent.** Nothing is
  wrong, so silence would be defensible; but the failure this guards against
  is forgetting which arm a session is in, and a stock session does not look
  any different from a patched one. One line per session, naming the arm and
  how to end it.
- **The banner's meaning is suspended for the duration.** Normally banner ⟺
  our patches are live (see above). In the arm it means only that the pipeline
  built this binary — still true, still worth showing, and `status` says which
  of the two it is claiming instead of reporting a disagreement that isn't.
- **Anti-markers inverted.** In the arm the deleted stock sentences should all
  be back; one that is *absent* means upstream deleted it themselves. Reported,
  never gating — that is a finding about their prompt, which is exactly what
  the arm is for.
- **The verdicts go advisory.** Nothing of ours is applied, so a drifted
  fragment cannot break this binary. They still price the *return* trip, which
  is the one thing an operator inside the B arm cannot otherwise learn, so
  `update --stock` prints it: CLEAN means `apply` brings the tranche straight
  back.

A pristine source is mandatory and never synthesised from the live binary: on
a patched machine the live binary IS the tranche, and "stock" assembled from it
would be a no-op that verified nothing.

**Ending the arm is plain `ccctl.py apply`** (or a non-stock `update`), which
clears the declaration as part of putting our text back. A record naming a
version that is no longer installed describes a binary that is gone: it reads
as STALE, is ignored, and does not excuse an unpatched binary nobody chose.

ccctl mirrors the declaration into a one-line `stock-arm` file in its state dir
(`${CCCTL_HOME:-$HOME/ccctl}`): version, timestamp, note. It was written for
`tools/cc-doctor.sh`, which was bash and had to stay honest without a python on
PATH; since 2026-09-05 that script delegates to ccctl (`ccctl_compat.py
status`), so the sentinel has no reader left and survives only as an
out-of-band record. Derived, never
authoritative — rewritten or removed to match `ccctl-state.json` on every
command that reads the arm, so a hand-deleted sentinel heals on the next
`status`.

Tests: `python tools/tests/test_ccctl_stock_arm.py` (sandboxed; pins the exit
codes, the tripwire's two arms, the stale-record rule and the sentinel shape).

## Three guards that make an intermittent upgrade cheap (2026-09-08)

The pipeline was already safe — staged, verified, never half-patched. What it
was not yet was *cheap*: the 2.1.261 round on win32 cost an agent three pieces
of reasoning that nothing in the tooling supplied. Each is now a check, each
with tests in `tools/tests/test_ccctl_upgrade_guards.py`, and each says the
thing an agent would otherwise have to derive.

**1. A CLEAN analysis says nothing about tweakcc's own patch set.** This is the
important one, because it is silent. `analyze` reports "tweakcc 4.3.3 installed
(current)" and "prompt data available", both true, and then reads CLEAN — while
`analyze` never *runs* tweakcc's feature patches, so none of that is evidence
about them. On 2.1.261 the analysis was CLEAN 11/11 AUTO and the patch set
still needed a source overlay, because Anthropic had rebuilt the model menu
around a server-aware option builder. The only reason win32 got it right was
that an agent read another machine's note.

`tweakcc_build_state()` now derives the answer from disk. The overlay filenames
in `tools/tweakcc-overlays/` are the record (`<twkver>-cc-<ccver>.patch`), the
pin is `EXPECTED_SHA` in `build_local_tweakcc.py`, and the clone is *found*, not
configured: npm links a global package by symlink, so the module tree resolves
into the clone, and `tweakcc_clone()` walks up from the resolved entry point to
the directory holding both `.git` and a tweakcc `package.json`. That matters
because the fleet keeps its three clones in three different places (spec/45),
and one of them is a decoy — this repo's own `.work/tweakcc-dev`, owned by
another Windows SID. The ladder of verdicts:

| verdict | meaning |
| --- | --- |
| `ok` | clone is at the pin and an overlay covers the target |
| `stale-clone` | clone HEAD ≠ pin — the overlay is not in this build; the fix is the exact fetch/detach/rebuild line |
| `dirty-clone` | tracked changes — what is built there is not the reviewed tree |
| `published` | tweakcc is an ordinary npm install, so no overlay is in it |
| `uncovered` | the target CC version is newer than every overlay — the patch set is UNPROVEN here |
| `overlay-behind-tweakcc` | installed tweakcc is newer than the overlay's — the repair may be upstream now; verify and retire |
| `no-overlays` / `unknown` | nothing to say (quiet) |

**It reports and does not gate**, deliberately. A rejected patch set costs the
in-CC banner (a `tweakcc-fallback` assemble), never a wrongly-patched prompt,
so `overall` keeps meaning "our targets apply" and the analyze exit codes keep
their contract. It is loud in three places instead: an `ADVISORY` line under
the analyze header, its own section in the report, and — where it actually
bites — the `tweakcc-fallback` changelog entry and console note now name the
likely cause and its fix rather than leaving a stack trace to be correlated
with a build nobody can see. The diagnostic is wrapped so it can never raise
on that path: it is called on the failure path already, and a diagnostic that
turns a survivable fallback into no binary is exactly backwards.

**2. A `--ff-only` refusal is how a fleet round arrives.** Another machine
pushes while this one sits still, this one has commits of its own, and `update`
stops at step one with git's own hint — which ends at "you need to either merge
or rebase" and does not know that *this* repo reconciles machine rounds by
MERGE, recording the resolutions in the merge commit. `divergence_message()`
answers it at the refusal: ahead/behind counts, both one-line logs, the runnable
`merge --no-ff` command, the precedent to read (`git show bad7880`), and the
reason to read the incoming commits first — a fleet round often carries pipeline
repairs this machine needs. Only a genuine two-sided divergence gets it; an
ahead-only or behind-only failure leaves git's words to stand.

**3. The configured channel can point backwards.** `stable` served 2.1.236 on
2026-09-08 while win32 ran 2.1.261, so a bare `ccctl.py update` would have
staged, verified, launch-tested and swapped its way 25 releases *backwards*,
with every step reporting success — it is a legitimate update onto a real
release, which is why nothing downstream objects. `downgrade_refusal()` refuses
an older target, names the channel that served it, and offers both ways on:
name the version, or `--allow-downgrade` for a deliberate rollback.

The standing consequence for this fleet: **name the version.** `update <VER>`
is the normal form, not `update`.

## The subagent model pin, and why an upgrade has to ask about it (2026-09-22)

**Current state (2026-09-29): no pin.** With Sonnet 5.5 out, the operator reset
subagent models to CC's defaults "so I can see how that plays out". Unpinned,
an agent follows its definition's model, else the parent (so under an Opus
parent, `general-purpose` still runs Opus unless the spawning model names
another per spawn), and `update` has no alias to ask about. The rest of this
section is the pin's rationale and mechanics, kept for when one is set again.

**The choice from 2026-09-22 to 2026-09-29:** Opus-tier subagents for everything, set as
`CLAUDE_CODE_SUBAGENT_MODEL=opus` in `settings.json` env, with
`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` deliberately **not** set. His reason was
about accuracy, not cost: Sonnet confabulated over the large input sweeps an
explorer takes in a complex project, so a cheaper default bought tokens with
wrong answers. Leaving FORCE off kept the per-spawn override available, so a
caller could still name a model for one agent without unpinning anything.

CC resolves a spawned subagent's model in a fixed order, and names the rungs
itself in its own telemetry: **per-spawn (`tool`) → the agent definition
(`frontmatter`) → this variable (`env`) → inherit the parent.** So the pin is
the default for every agent that does not carry one, and loses cleanly to a
deliberate per-spawn choice.

**Why it needs a question on every upgrade rather than a one-time setting.**
The pin is an *alias*, and the alias is resolved by the bundle's own table
(`opus` → `claude-opus-5` on 2.1.278). A release can move what `opus` means.
When it does, no marker moves, no fragment changes, no gate drifts and no
server arm flips — every existing check in this repo stays green while the
thing he actually chose has changed underneath him. It is the model-gate
failure shape one layer further out: the setting is intact, the meaning is not.

**This happened one day later.** CC 2.1.280 shipped Opus 5.5 and `opus` became
`claude-opus-5-5`; `update` asked, and the answer was to keep the alias, since
the standing choice is "Opus latest". The move was not academic: 2.1.280 also
gave Opus 5.5 a **model clause of its own in the capability gate**, arming
`silent_turn_reminder`, so the pin alone put every unpinned subagent on a model
that had the silent-turn reminder back on by default until the adhoc was
widened (`notes/2026-09-23-macos-2.1.280.md`). The pin and the gate are
separate mechanisms that a single new model moves at the same time — when the
alias moves, re-read the gate before assuming the move is cosmetic.

So `ccctl.py update` ends by resolving the pin against the new binary and
comparing it with the resolution recorded at the previous update
(`ccctl-state.json`, `subagentModel`). An unchanged answer is one line of
statement. A changed one is an `ASK THE OPERATOR` block naming the old model
and the new. `status` prints the current resolution on every run, so the state
is visible between upgrades; only `update` writes the baseline, because
otherwise a status run between two upgrades would absorb a move and the
upgrade would never ask. Asked once per real change, per the ask-once rule.

Two further states are asks in their own right: a pin this build has **no model
for** (CC does not fail on one, it silently falls back to the parent, which
from outside is indistinguishable from no pin at all), and FORCE being on
(it overrides a per-spawn model, which is the one thing he asked to avoid).

**What the pin reaches, MEASURED** (2026-09-22, by capturing each subagent's
own outgoing request — `tools/probe_subagent_models.py`, free). A first pass
derived this from the binary and got it wrong, which is why the table below is
keyed to observation:

| built-in agent | its model field | what the subagent actually sends |
| --- | --- | --- |
| `general-purpose`, `claude`, `workflow-subagent` | *absent* | **the pin** |
| `Plan`, `comment-thread-analyst` | `"inherit"` | **the parent — pin ignored** |
| `Explore` | `"inherit"`, never read | opus when the parent is off-ladder, else the parent |
| `statusline-setup` | `"sonnet"` | sonnet |
| `claude-code-guide` | `"haiku"` | haiku |

**An explicit `"inherit"` is a choice, not a default, and it outranks the pin.**
It sits on the `frontmatter` rung, above `env`; an *absent* field falls through
to `env` and takes the pin. So `Plan` follows the parent however the pin is set
— on a Fable session, `Plan` is a Fable agent — while `general-purpose` obeys.
The measured matrix from a Fable parent, with and without the pin:

```
                      no pin            pin=opus
  Explore             claude-opus-5     claude-opus-5     <- the cap, not the pin
  Plan                claude-fable-5-1  claude-fable-5-1  <- pin never applies
  general-purpose     claude-fable-5-1  claude-opus-5     <- pin works
  claude              claude-fable-5-1  claude-opus-5     <- pin works
```

**The Explore cap is a cost ceiling and it is deliberate.** `EBn()` tests the
parent id against `["haiku","sonnet","opus"].slice(0, indexOf("opus")+1)` — a
cost-ordered ladder sliced at the ceiling — so anything *above* opus is pulled
down to it. Fable is `tier_10_50` against opus's `tier_5_25`: **double the
input and output price**, and `advisor_rank` 5 against opus's 4. An explorer is
the worst possible place to spend that, since it reads large volumes of
uncached new content. Appending `"fable"` to that array would not change the
behaviour, because the slice stops at the cap — which is the proof it is a
ceiling and not a stale list. A per-spawn `model` still beats it in both
directions (measured: `fable` gives Fable, `haiku` gives Haiku).

**Consequence for tweakcc:** its own `subagentModels` patch is a **silent
no-op for Explore** on 2.1.278 — it rewrites a field no code path reads, while
matching, applying and reporting success. Plan and general-purpose are
genuinely patchable that way. Anyone reaching for that patcher should read this
first.

Coverage: `tools/tests/test_ccctl_subagent_model.py`; the measurement is
re-runnable at any time with `tools/probe_subagent_models.py` and spends nothing.

## Residual risks (reviewed 2026-08-21, accepted)

An adversarial review (Opus subagent) of the implementation produced 22
findings; all blocking ones are fixed (non-atomic POSIX swap, dropped macOS
entitlements, `.stock`/`.pre-swap` shadowing binary resolution, silent
REVIEW-blindness on machines without a base snapshot, `cc_version` /
`claude_binary` split-brain, adhoc anchor ambiguity, mis-anchored-span
excision). Accepted residuals:

- **No cross-process lock.** Two concurrent ccctl runs, or a ccctl run racing
  a CC session's own updater, are unguarded. Mitigation: `DISABLE_UPDATES=1`
  in settings env kills the session-side updater; ccctl runs are operator- or
  agent-driven, one at a time in practice.
- **Rollback artifacts accumulate, and the agent prunes them.** `*.pre-swap*`
  (win32 launcher dir), `versions/<ver>.stock` and `.pre-swap` (POSIX) gain a
  file per swap. After a round is verified (`status --check` and `status
  --delivered`), the agent that ran it deletes every parked patched binary
  that is superseded and can be rebuilt from a pristine plus a repo commit. It
  keeps the pristines and any file a running process still maps (win32 refuses
  the delete anyway). It records each kept file's reason and deletion
  condition in the round note. Operator, 2026-09-29: backups are not left for
  him to delete (global CLAUDE.md).
- **Windows dual versions-dir layout** (`~/.local/share` vs `%LOCALAPPDATA%`)
  is resolved by preferring `~/.local`; a machine that genuinely uses the
  LOCALAPPDATA layout gets its files written to `~/.local`.
- **The instruction scan is recall-oriented and noisy** (~1-1.5k sentences per
  release step); it informs, never gates.

## Failure modes closed

- Update-then-discover-drift (update-cc.sh's order): closed by D2.
- Fragment no-op on win32 (`tweakcc --apply`): closed by D3.
- Adhoc patches skipped by ccctl apply: closed by D4.
- Half-applied live binary on any failure: impossible — failures before the
  swap leave the live install untouched; the swap itself is two renames.
- tweakcc lag discovered mid-update: discovered in analyze, before anything
  moved.
- Silent clobber of an upstream fragment improvement: REVIEW verdict blocks
  `gate-on-clean` and is called out in the report with the diff hunk.
