# Tool inventory

| Tool | Purpose |
| --- | --- |
| `ccctl.py` | Single-file lifecycle: bootstrap, stock download, analysis, staged patches, verification, recovery (`spec/40`) |
| `ccctl_compat.py` | Resolves the matching ccctl state directory for repository-root and legacy entrypoints |
| `patch.sh`, `apply-win32.py`, `apply_adhoc.py`, `cc-doctor.sh` | Compatibility wrappers over ccctl; see `spec/40` |
| `build_local_tweakcc.py`, `patch_tweakcc.py` | Pinned tweakcc overlay build and the code-split Bun adapter; the pin is read from `release.json` |
| `build_ab.py` | Generates `ab/data.json` — every current target's stock and replacement — from the release's prompt map and pristine binary; `--check` detects staleness offline |
| `check_repo.py` | One offline command: active links, release/review consistency, generated-view freshness, syntax, and every `tests/test_*.py` |
| `refresh_corpus.sh`, `extract_transcript.py`, `transcript.py`, `build_corpus_index.py` | Explicit capture and rendering of real session evidence |
| `extract_human_turns.py`, `build_complaint_set.py`, `merge_sort.py` | Reading aids over the corpus; outputs are not behavioral acceptance scores |

From the repository root:

```sh
python3 tools/check_repo.py                       # offline consistency + all regression suites
python3 tools/build_ab.py                         # regenerate ab/data.json from the parked pristine
python3 tools/build_ab.py --binary <pristine>     # ... or from an explicit pristine binary
python3 tools/ccctl_compat.py status --check      # this machine's patch state
python3 tools/ccctl_compat.py status --delivered  # what each configured model is handed, per harness (free)
python3 tools/closing_lengths.py 2026-09-01       # closing-reply length and form, per model and prompt regime
python3 tools/closing_lengths.py 2026-09-13 --by prompt   # ... split by the prompt each session recorded
python3 tools/extract_human_turns.py --src ~/.claude/projects --since <utc> --prefix PT --out /tmp/x
                                                  # his turns from live transcripts, for a period the corpus lacks
python3 tools/probe_subagent_models.py            # which model each spawned subagent actually gets (free)
```

`probe_subagent_models.py` borrows the same listener trick to answer a question
the rest of the pipeline can only infer: it scripts the parent's first turn as
an `Agent` tool_use, lets Claude Code spawn the subagent for real, and reads the
`model` off the subagent's own outgoing request. Pass a parent model and a
subagent type for one cell, or nothing for the standard matrix. It is what
established that `Plan` ignores the subagent pin while `general-purpose` obeys
it (spec/40).

`status --delivered` points CC at a listener on 127.0.0.1 through
`ANTHROPIC_BASE_URL`, so nothing reaches the API. The listener plays the model:
it answers seven turns with one silent `Read` of a file in the capture's own
temporary directory, then "done". The first request carries the prompt; the
later ones show what the harness injects per turn, the silent-turn reminder
among them. It runs the installed binary with the settings layers a fresh
session builds, once as `claude -p` and once as an Agent SDK host launched the
way T3 Code launches it. It cannot see an interactive terminal session (the
trust dialog) or a gateway that rewrites requests after CC.

`check_repo.py` uses no model calls, no network and no binary activation. It
needs Node for the `ab/index.html` syntax check and the Bun serializer fixture.
For tweakcc source changes additionally run the pinned build helper. For
binary or locator changes additionally stage, apply and check launch, markers
and adhocs on the platform being claimed — a passing `check_repo.py` proves
the repository is consistent, not that a machine is patched.

`build_ab.py` refuses a binary whose SHA-256 is not the one `release.json`
records for this platform, so it can never describe a patched binary as stock.
`ccctl.py analyze <ver>` parks a manifest-verified pristine at
`~/ccctl/staging/claude-<ver>-pristine`; `update` leaves one at
`~/.local/share/claude/versions/<ver>.stock`. Both are found automatically.

The probe battery under `probes/` is retired by operator decision
(`spec/20-probe-battery.md`). Do not run its runners; the captures stay as
evidence.
