# Pre-merge tweakcc prompt data

This is the escape hatch for the interval between a Claude Code release and
the corresponding prompt-data file reaching tweakcc's `main` branch. It is
not permission to guess prompt fragments or to force a MANUAL analysis. The
source of truth remains prompt data prepared by tweakcc; this procedure uses
an in-review contribution from a trusted upstream maintainer before merge.

## Current build pin (2026-09-20, merge-verified 2026-09-21)

`release.json` pins **`f5aaf1e`** (`fetchRef` `pull/1005/head`), which carries
prompt data for CC 2.1.278. Taken pre-merge on 2026-09-20; the trust gate that
day read head repository `Piebald-AI/tweakcc`, author association MEMBER, one
added generated file (`data/prompts/prompts-2.1.278.json`, blob
`68eab8bd450b160b142df66d0754cf16e16aa861`), parent `main`'s merged PR 1003.
**Unsigned — recorded as a risk signal, not a veto**, on the same footing as
PRs 982 and 993. `git diff 2a4ac735 f5aaf1e7 -- . ':(exclude)data/prompts'` is
**empty**: tweakcc is still 4.3.3, and the five commits between the pins add
nothing but generated maps for 2.1.274 through 2.1.278.

**PR 1005 merged on 2026-09-21 as `54048e9`, and the check below was run:** the
pin's tree and `origin/main`'s are both `6a2fac35`, and `main`'s published
`prompts-2.1.278.json` is the reviewed blob `68eab8bd` byte for byte. Equal
trees, so — per "After upstream merges" — **nothing moves**: the pin stays at
`f5aaf1e`, builds keep going through `build_local_tweakcc.py`, and the
prompt-data cache entry is now simply the normal one. `fetchRef` is still
required for another machine to reach the pin, because the merge was a squash
and `f5aaf1e` is not an ancestor of `main`. Nothing else has landed on `main`
since.

**Re-run independently on win32's own clone, 2026-09-21** — a tree equality
asserted in one session is not a fact on another machine's disk. Same two
trees (`6a2fac35`), same published blob (`68eab8bd`), and the seeded cache
entry's `git hash-object` round-trips to it, so Windows' CRLF conversion stayed
out of the file. All three machines have now built from this pin (macOS 2026-09-22).

The overlay (`4.3.3-cc-2.1.278.patch`) covers CC 2.1.278 and is **identical in
content to the 2.1.273 one**, which was identical to 2.1.270's: the patched
code structures have not moved across those five releases, only minified names.
The filename is the coverage record `analyze` reads, so it is copied rather
than reused in place. Verified on Linux 2026-09-20 — lint, tests and build all
pass, tweakcc's own patch set applied with the banner present on the 2.1.278
pristine, and every marker verified after the swap. (Marker and target counts
move as the tranche grows; `edits/MARKERS.txt` and `edits/targets.json` are the
count, and `status --check` is the verdict. Do not hard-code either here.)

Note that this pin is on tweakcc's source, which is a real build input. The
Claude Code version beside it is not the same kind of thing: it records what
the edits were last checked against, and advancing it is the routine step in
`spec/40`.

Previous pin history: `main` at `2a4ac73` (PR 998, 2026-09-16) carried data
through 2.1.273; PR 995 head `0860c14` (2026-09-13, pre-merge) carried 2.1.270
with the session-memory and dollar-sign overlay repairs documented in that
round's notes. The 2.1.268 overlay's model-selector repair is still carried.
Older overlays stay as historical provenance.

## When to use it

Use this procedure when all of the following are true:

1. the desired Claude Code version is already published;
2. `ccctl.py analyze <version>` reports BLOCKED only because
   `data/prompts/prompts-<version>.json` is absent from tweakcc `main`; and
3. an open PR in `Piebald-AI/tweakcc`, authored by a Piebald team member,
   adds the missing prompt data.

Otherwise wait for upstream. In particular, a PR from an unknown fork is not
made trustworthy merely by having the expected filename or a large generated
diff.

## Trust gate: inspect, then pin

Before running any code from the PR, verify on GitHub that:

- the PR head repository is `Piebald-AI/tweakcc`, not a contributor fork;
- the author's association is `MEMBER` or `OWNER`;
- the changed files are limited to the expected generated prompt-data file
  (or every additional change has been understood separately);
- the JSON is for the exact Claude Code version requested; and
- the checkout is pinned to the PR's exact commit SHA, not merely its mutable
  branch name.

The GitHub web UI is sufficient. With `gh`, useful checks are:

```sh
gh api repos/Piebald-AI/tweakcc/pulls/<PR-number>
gh api repos/Piebald-AI/tweakcc/pulls/<PR-number>/files --paginate
gh api repos/Piebald-AI/tweakcc/commits/<exact-SHA>
```

An unsigned commit is a risk signal to record, but it is not by itself a veto
when the commit is on an official-repository branch, the author is an upstream
member, and the diff contains only the generated data expected. That was the
case for PR 982 / commit
`38b24c2bf82a79a5c72db851d76ffafd867ab6d7`, used for Claude Code 2.1.257.

## Build an ordinary local clone

Keep a normal clone of `https://github.com/Piebald-AI/tweakcc.git`. Do not make
or push local tweakcc commits for this procedure. Fetch the PR and detach at
the reviewed SHA:

```sh
git -C <tweakcc-clone> fetch origin pull/<PR-number>/head
git -C <tweakcc-clone> switch --detach <exact-SHA>
python3 tools/build_local_tweakcc.py <tweakcc-clone>
npm install --global <absolute-path-to-tweakcc-clone>
tweakcc --version
```

Read tweakcc's `AGENTS.md` before building. At the 2.1.257 exercise the stated
development baseline was Node 24 and pnpm 11.0.9 or newer; Node 24.19 and pnpm
11.25 completed lint, 532 tests (5 skipped), and the production build.
`build_local_tweakcc.py` pins and checks the reviewed SHA, temporarily applies
this repository's version-specific overlay, runs install/lint/test/build,
reverses the overlay, verifies that the upstream checkout is clean, then adapts
the built `dist/` for the current Bun blob format. Use it instead of manually
applying the overlay. It is deliberately exact-versioned: when a future release
changes tweakcc or Claude Code, derive and test a new overlay rather than forcing
this one. `npm install --global <local-path>` warned
that prepare/Husky scripts were not allowed, but this was harmless because
`pnpm build` had already populated `dist/`.

Prefer the package manager's global local-path install over a hand-written
wrapper script. On Windows it creates a junction from the global npm package
directory to the clone, which also satisfies ccctl's checks of the installed
package layout.

## Seed the prompt-data cache

This step is easy to miss: **the local build does not consume the prompt JSON
from its own checkout.** `src/systemPromptDownload.ts` still downloads from
tweakcc's `main` branch. Until the PR merges, copy the reviewed file into the
cache yourself:

```sh
mkdir -p ~/.tweakcc/prompt-data-cache
cp <tweakcc-clone>/data/prompts/prompts-<version>.json \
  ~/.tweakcc/prompt-data-cache/prompts-<version>.json
sha256sum <tweakcc-clone>/data/prompts/prompts-<version>.json \
  ~/.tweakcc/prompt-data-cache/prompts-<version>.json
```

PowerShell equivalent:

```powershell
New-Item -ItemType Directory -Force "$HOME/.tweakcc/prompt-data-cache"
Copy-Item "<tweakcc-clone>/data/prompts/prompts-<version>.json" `
  "$HOME/.tweakcc/prompt-data-cache/prompts-<version>.json"
Get-FileHash "<tweakcc-clone>/data/prompts/prompts-<version>.json"
Get-FileHash "$HOME/.tweakcc/prompt-data-cache/prompts-<version>.json"
```

The two hashes must match. For PR 982's 2.1.257 file, the canonical Git/raw
file (LF endings) has SHA-256
`17AC1A2B0F4C1EA6977B93D7A381A3ED88C7E1C912F7C804056FC7DC764AF9C3`.

**Seed the canonical LF bytes, not the working-tree copy.** On Windows the
checkout is CRLF-converted, so `cp` from the clone writes a cache entry that
differs from what tweakcc itself downloads from `main` — which is what the
2.1.257 entry on win32 still is. It parses fine (it is JSON), but it has to be
replaced after the merge, and it makes the hash in these notes ambiguous.
Prefer

```sh
git -C <tweakcc-clone> cat-file -p <SHA>:data/prompts/prompts-<version>.json \
  > ~/.tweakcc/prompt-data-cache/prompts-<version>.json
```

which reads the blob unconverted on every platform. Then check the blob SHA-1
against what GitHub reports for the PR's file (`gh api
repos/Piebald-AI/tweakcc/pulls/<N>/files --jq '.[].sha'` vs
`git -C <clone> rev-parse <SHA>:data/prompts/prompts-<version>.json`) — that
proves the bytes are the reviewed ones, which a SHA-256 of a local copy cannot.
For PR 993's 2.1.268 file: blob `835f17bef8161d4154cfcfa3e051a46276a87920`,
content SHA-256
`B6C6951610372B8D3E7B1EDA12B8AF8ADA6424BA68F389E138E0EF5A32B0FA05`.
Seeded that way the entry is already identical to the post-merge download and
needs no replacement.
A Windows checkout with Git's CRLF conversion has SHA-256
`266C166124678E1E5C90BD2B9D4B6C412C6790B8DADFB16E4732115C01CB306D`.
Do not edit tweakcc's downloader merely to redirect this one file; a cache
entry is the smallest reversible intervention.

## Analyze, repair drift, and update

Run the normal pipeline against the explicitly requested version:

```sh
python3 tools/ccctl.py analyze <version>
```

If any target is MANUAL, re-derive its anchor from the new prompt data, update
`tools/ccctl.py`, and add a regression fixture that reproduces the exact new
shape. Do not use `--policy force` to turn a failed locator into an update.
Then run:

```sh
python3 tools/tests/test_ccctl_hardening.py
python3 tools/tests/test_ccctl_posix_place.py
python3 tools/ccctl.py analyze <version>     # must now be CLEAN
python3 tools/ccctl.py update <version> --no-pull
python3 tools/ccctl.py status --check
```

Use `--no-pull` while the necessary ccctl changes exist only in the local
working tree. Commit and push those changes to this repository promptly so
the other machines can use the same reviewed locators.

For 2.1.257, two stock shapes moved:

- delegation suppression now covers the adjacent
  `opus5_reduced_delegation`, `heron_brook`, and `brook_heron` sections at
  their shared registration site; and
- the lean-model communication condition derives and includes the new
  `LN(e)` predicate used by the nearby `Write code...` branch.

Both shapes have regression coverage in
`tools/tests/test_ccctl_hardening.py`.

For 2.1.268, one stock shape moved and one writer invariant surfaced:

- the `output_style` and `language` **sections are gone** from the shared
  prompt list; both now ship only as attachment messages, which is the
  behaviour `staticSystemPromptEnabled` used to gate. The floor moved onto an
  always-present section instead — spec/15 §5.2 and
  `notes/2026-09-11-win32-2.1.268.md` carry the derivation and what it costs.
- `bun_source_patch` had to start honouring **4-byte alignment**. Bytecode and
  builtin regions are aligned; byte runs are not; the writer shifts everything
  after the cut by the edit delta. Until 2026-09-11 every tranche's cumulative
  delta happened to be divisible by 4, so this never bit. When it does bite,
  the symptom is not a failed patch: every target reports OK, markers verify,
  and the binary exits `0xC0000409` with no output. If a future round ever sees
  that exit code, suspect a layout invariant the writer does not model — not
  the patch set, and not the anchors.

For 2.1.278, no locator moved — every target was AUTO on the first
`analyze`, and the six extracted fragments are byte-identical to 2.1.273 in the
prompt map. What moved was a **gate**, which no locator would have caught:

- `bison_cairn`, `larch_cistern` and `amber_astrolabe` stopped being
  `tengu_`-prefixed GrowthBook flags and became **capabilities** resolved by
  the same gate that carries the model clause. Two of them decide the sections
  that hold our text, and the gate grew a **second model clause** — a set
  `["bison_cairn","larch_cistern"]` keyed on `opus_5_prompt_bundle` — while the
  Fable clause's literal stayed byte-identical. `ccctl`'s single-literal byte
  check therefore passed throughout. It now parses the gate and compares every
  clause against `MODEL_GATE_CLAUSES`; an unknown clause is a finding.
  `notes/2026-09-20-linux-2.1.278.md` has the derivation.

The lesson generalises: **a CLEAN analyze is a statement about locators, and a
gate is not a locator.** Diff the deciders as well as the text.

On code-split Claude Code releases (2.1.243 and later), do not accept a stale
optional-feature patcher merely because prompt extraction succeeded. First
derive a module-local locator from the exact release, add it to the checked-in
overlay, and prove it against the local build. For 2.1.257 the overlay repaired
all applicable optional patchers and `ccctl.py apply --no-pull` verified the
tweakcc banner plus every marker this repository had at the time. The fallback remains a
safety net only when a local repair is genuinely impossible; record why and
continue to repair it when evidence becomes available.

## Fleet clone inventory

These are discovery locations for local tweakcc builds. They are ordinary
clones of the official GitHub repository: never add fleet-only commits or push
from them. Fetch the chosen PR, detach at its reviewed SHA, build, and seed the
prompt cache on the machine that needs the early update.

| Machine | Endpoint | Clone path | State, each row verified on its own date |
| --- | --- | --- | --- |
| Win11 work laptop | local | `[WINDOWS_HOME]\repos\tweakcc` | **2026-09-21:** detached at PR 1005 head `f5aaf1e7c78359c20ecbac9fbe5dcaf699d80cf4` (fetched via `pull/1005/head`), tracked source clean, `dist/` rebuilt with the 2.1.278 overlay (lint, 543 passed, 5 skipped, `patch_tweakcc.py` adaptation ✓); npm's global `tweakcc` package is still a junction to this clone. 0 failures on the CC 2.1.278 pristine, banner present after the swap. Was main `2a4ac735` (PR 998, 2026-09-16, CC 2.1.273). Do not mistake this repo's `.work/tweakcc-dev` for it — that is a sandbox artifact owned by another Windows SID. |
| Kubuntu 26.04 workstation | remote | `[USER_HOME]/apps/tweakcc` (`~/apps/tweakcc`) | **2026-09-20:** detached at PR 1005 head `f5aaf1e7c78359c20ecbac9fbe5dcaf699d80cf4` (fetched via `pull/1005/head`), tracked source clean, `dist/` rebuilt with the 2.1.278 overlay (Node 24.16, pnpm 10.33, lint + tests + build); npm's global `tweakcc` is a symlink to this clone's `dist/index.mjs`. Was PR 995 head `0860c14` (2026-09-13), PR 993 head `f760b88`, `main` at `4d78df3` before that. |
| macOS 26 MacBook Air | remote | `[USER_HOME]/apps/tweakcc` (`~/apps/tweakcc`) | **2026-09-22:** detached at PR 1005 head `f5aaf1e7c78359c20ecbac9fbe5dcaf699d80cf4` (fetched via `pull/1005/head`), tracked source clean, `dist/` rebuilt with the 2.1.278 overlay (Node 22.23, lint, 543 passed, 5 skipped, `patch_tweakcc.py` adaptation ✓); npm's global `tweakcc` symlinks into this clone. Was PR 995 head `0860c14` (2026-09-13), `f760b88` before that. |

A future agent should
inspect the existing clone and its origin before changing branches; do not
reclone over it. `mkdir -p ~/apps` followed by a normal official-repository
clone is the provisioning convention for any additional POSIX machine.

## After upstream merges

First check that what merged is what was reviewed:

```sh
git -C <tweakcc-clone> fetch origin main
git -C <tweakcc-clone> rev-parse <pin>^{tree} origin/main^{tree}
```

**Equal trees: do nothing.** Stay at the pin and keep building through
`build_local_tweakcc.py`. A merge does not retire the overlay — the overlay
adapts tweakcc to the *CC bundle*, and PR 993's merge brought no source change
at all. Until 2026-09-11 this section said to switch to `main` and run a plain
`pnpm build`; while an overlay exists that is a regression, not a return to
normal. It drops the overlay (on 2.1.268 that is the model-menu repair) and
skips `patch_tweakcc.py`'s `dist/` adaptation. `build_local_tweakcc.py` also
refuses `main`'s SHA, and `analyze` reports the clone as `stale-clone`. The pin
moves to a `main` commit at the next round that needs one, under the same
trust gate.

**Different trees: upstream changed the file before merge.** Replace the cache
entry from the reviewed new SHA and repeat the analysis. Do not silently keep
the earlier candidate.

The cache file may remain either way: once upstream publishes the identical
data it is just the normal cache entry.

Only when no overlay covers the current CC version and tweakcc's published
release carries the fix, go back to an ordinary install
(`npm install --global tweakcc`, or a plain build of `main`).
