# claude-code-dispositions

<!-- DRAFT: needs author -->
Claude Code's system prompt is written for a user it has never met. On my own
machines I rewrite parts of it with [tweakcc](https://github.com/Piebald-AI/tweakcc):
the calibration for that unknown user comes out, and a stated disposition for
both parties goes in, Claude's role and mine. This repository holds the spec
that argues for each change, the replacement text in tweakcc's own file format,
and the tooling that keeps it installed from one Claude Code release to the next.

<!-- DRAFT: needs author -->
The installed replacement for Claude Code's "Delivering work" section opens
like this
([`edits/system-prompt-delivering-work-at-full-scope.md`](edits/system-prompt-delivering-work-at-full-scope.md)):

> There is an operator on the other end of this, not an audience. They ran it,
> they own the consequences of their own instructions, and they will say when
> they want more caution.

## Why

Claude Code ships a large, layered set of prompts: a main loop prompt, per-tool
descriptions, per-subagent prompts, and per-turn injected reminders. Much of it
is audience calibration written for an unknown user — verbosity floors,
restate-before-answering, confirm-before-acting, stacked negative instructions.
For an operator who runs several sessions a day, that calibration is wrong in a
specific and consistent direction.

The goal is **not** to strip character or to remove epistemic hygiene
(disagreeing when warranted, reporting failures faithfully, not fabricating).
Those are load-bearing. The goal is to replace generic-user calibration with a
stated disposition for both parties — Claude's role and mine — on the working
theory that dispositions regenerate correct micro-decisions across a long
trajectory where enumerated rules degrade.

## What is here

<!-- DRAFT: needs author -->
| Path | What it is |
| --- | --- |
| `spec/` | The disposition spec, written before any stock prompt was touched, so each edit is a diff against an intent rather than a reaction to a block of text. `00` is the argument, `10` the text that is installed, `15` what the closing message owes the operator, `30` and `40` how the edits survive updates. |
| `edits/` | The change itself, in tweakcc's per-component format: one whole replacement body per changed prompt fragment, `targets.json` (every target with its intent and the spec line behind it), `MARKERS.txt` and `ANTIMARKERS.txt` (what a patched binary must and must not contain), `reviews.json`. |
| `tools/ccctl.py` | One file, standard library only, for Linux, macOS and Windows: fetch a release, analyse it against the edits, patch a staged copy, verify, swap, roll back. `tools/README.md` lists the helpers around it. |
| `tools/tweakcc-overlays/` | Patches against tweakcc's own source for Claude Code builds its feature patches did not yet cover. |
| `probes/` | Nine small synthetic repositories built for a probe battery that has since been retired. |
| `release.json` | The Claude Code release the edits were last checked against, and the stock binary hashes for it. |
| `AGENTS.md` | The working agreement for an agent operating on this repository. |

<!-- DRAFT: needs author -->
What this tree leaves out, how much stock wording the edits still carry, and
what the licence covers: [`ABOUT-THIS-REPOSITORY.md`](ABOUT-THIS-REPOSITORY.md).

## Using it

<!-- DRAFT: needs author -->
**Read.** Start with [`spec/00-disposition-spec.md`](spec/00-disposition-spec.md).
Every edit has to be justified by a line in it, and an edit that is not gets
reverted.

**Compare.** Each `edits/<id>.md` is named after tweakcc's id for the component
it replaces. To see the stock text next to it, let tweakcc fetch the prompt data
for the release the edits were last checked against, the `ccVersion` in
`release.json`:

```sh
tweakcc --list-system-prompts <version>
```

That lists every component by id and caches the full text in
`~/.tweakcc/prompt-data-cache/prompts-<version>.json`. Once `ccctl.py` is set up
(below), `python3 ccctl.py diff custom <version> <id>` prints stock against the
replacement as a unified diff.

**Apply with tweakcc alone.** tweakcc reads user modifications from
`~/.tweakcc/system-prompts/`:

```sh
cp edits/system-prompt-*.md edits/tool-description-*.md ~/.tweakcc/system-prompts/
tweakcc --apply
```

This installs the replacement fragments and nothing else. The rest of the
change is text tweakcc does not extract, such as the reporting floor under every
output style and the Bash tool's compact git section (`spec/00` §2.3), and gates
rather than text, such as the model clauses of the silent-turn reminder. Those
are adhoc patches, and only `ccctl.py` applies them. On Windows,
`tweakcc --apply` did not write fragments when this was built (`spec/40`); use
`ccctl.py` there.

**Apply with `ccctl.py`.** It needs git, node with tweakcc 4.3.3 or newer, and
Claude Code's native install. It patches a staged copy and swaps it in only
after every marker verifies; the live binary is never edited in place.

```sh
mkdir ~/ccctl && cd ~/ccctl            # config, snapshots and changelog live here
cp <your clone>/tools/ccctl.py .
python3 ccctl.py init <git-url>        # sparse-clones the repository into ./repo
python3 ccctl.py apply                 # pull, patch a staged copy, verify, swap
python3 ccctl.py status --check        # 0 patched and verified, 2 fault, 3 stock on purpose
python3 ccctl.py restore               # byte-exact rollback to stock
```

`apply` pulls from the `init` URL before every run, so point it at a fork you
can edit. A Claude Code update replaces the patched binary; for a new release,
`ccctl.py analyze <version>` gives every target a verdict before anything
changes, and `ccctl.py update <version>` patches and swaps only on a clean
verdict. The exit codes, the settings that stop Claude Code from updating
itself, and the session-start tripwire are in the runbook in
[`spec/40-update-pipeline.md`](spec/40-update-pipeline.md). `status --check`
proves the edits are in the binary; start a session to see that the binary
still runs.

**What assumes my setup.**

- The runbook in `spec/40` is written for three machines of mine, one per
  operating system, that pull each other's rounds from one repository. The
  merge advice `update` prints comes from that.
- The subagent model pin that `status` reports and `update` asks about is my
  standing choice, not something the edits need.
- `spec/45` and `tools/tweakcc-overlays/` pin tweakcc to a reviewed commit with
  local patches. With a published tweakcc the edits still apply and verify;
  `analyze` says the overlay is missing, and tweakcc's "patches applied" banner
  may not appear in Claude Code's opener.
- `tools/build_ab.py` and `tools/check_repo.py` read stock text that this tree
  does not carry, so they do not run from here.
- The edits address one reader: an operator running several sessions at once,
  who checks the artifact rather than the account of it. `spec/00` §3.4
  separates what is universal from what belongs in your own
  `~/.claude/CLAUDE.md`.

## Reading order

1. `spec/00-disposition-spec.md` — the intent. §3.1b (locus of evaluation) and
   §3.4 (settledness, five standing questions) are the load-bearing sections.
   <!-- DRAFT: needs author -->
   One of the five: *Will a direct statement land badly?* Default answer: *No.
   The reader ran the command.*
2. `spec/10-draft-payload.md` — the universal disposition block that is actually
   installed, plus the deletion list it depends on.
3. `spec/15-reporting-contract.md` — what the closing message owes the operator.
   The reader model, three measured failure shapes, and which of a controlled
   language's rules survive inside a prompt fragment. Read §1 first: it traces
   how a stated requirement was dropped in five defensible steps.
4. <!-- DRAFT: needs author -->
   `edits/README.md` — the file format, the target list, and how a fragment
   that changed upstream is reviewed before it is applied again.
5. `spec/40-update-pipeline.md` — how `analyze`/`update`/`apply` work, the
   runbook of settings each machine needs, and the subagent model pin.
6. `spec/30-durability.md` — the four layers that keep a CC update from
   silently reverting the tranche, and the tripwire that reports when one does.
7. `spec/45-pre-merge-tweakcc-data.md` — the tweakcc pin, its trust gate, and
   the per-machine clone inventory.
8. <!-- DRAFT: needs author -->
   `tools/README.md` — every tool in one table, and `status --delivered`, which
   shows the prompt each model is actually handed without sending anything to
   the API.
9. <!-- DRAFT: needs author -->
   `spec/20-probe-battery.md` — nine probes, run once against stock Claude Code
   and then retired: Claude judging Claude was not helpful, and it cost too
   many tokens. What replaced them is the operator living with the patched
   binary, with new complaints as the measurement.

## Licence

MIT (`LICENSE`), covering the specs, the edits' authored text and the tools;
it does not cover Anthropic's prompt text or any stock wording quoted inside
them, and it does not cover tweakcc, which is MIT from Piebald-AI.
