# About this repository

<!-- DRAFT: needs author -->
What this repository carries, what it leaves out of the working repository it
is cut from, whose text is still inside it, and what the licence covers.

## What is not here

<!-- DRAFT: needs author -->
The working repository carries more than this tree. Two kinds of material
stay out.

Anthropic's text:

- `baseline/` — stock inputs, never edited: tweakcc's prompt map per Claude Code
  version and an early per-fragment extraction. Anthropic's text, not mine to
  redistribute.
- `ab/` — a generated review view of every target's stock and replacement bytes,
  built from the pristine binary. It carries the same stock text as `baseline/`.

Private working material:

- `corpus/` — real session transcripts, the evidence complaints are drawn from.
  Continuous prose about whatever I was doing that day.
- `notes/` and `TODO.md` — dated evidence, operator decisions, the complaint log,
  per-machine round notes and open operational items. Written to myself about my
  own machines.

The specs cite these paths as evidence, and those citations are left as written.
The tools that read them (`tools/build_ab.py`, `tools/check_repo.py`, and the
corpus scripts listed in `tools/README.md`) have nothing to read here. Where the
working files name me, my machines or my home directories, this tree reads
`[HUMAN]`, `[LINUX_WORKSTATION]`, `[MACOS_LAPTOP]`, `[USER_HOME]` and similar.

## Getting the stock text yourself

<!-- DRAFT: needs author -->
For any component in `edits/`, tweakcc supplies Anthropic's text for a given
Claude Code version:

```sh
tweakcc --list-system-prompts <version>
```

It lists every component by id and caches the prompt map at
`~/.tweakcc/prompt-data-cache/prompts-<version>.json`. Each entry stores its
text as `pieces` with the interpolations between them; `ccctl.stock_body(entry)`
joins them into the body the edits replace, and `ccctl.py diff custom <version>
<id>` prints stock against the replacement. The adhoc targets are not tweakcc
components, so they are not in that map; `tweakcc unpack <out.js>` extracts the
JavaScript from a native Claude Code binary, and `ccctl.plan_adhocs` shows what
each adhoc looks for in it.

## Stock wording the edits still carry

`edits/<component>.md` uses tweakcc's documented on-disk format for a user
modification: a full replacement body for the component of that name under
`~/.tweakcc/system-prompts/`, with the frontmatter tweakcc generates. The file
stem is tweakcc's component id, the same key used in
`baseline/<ver>/prompts.json`. tweakcc has no patch, diff or overlay form for a
prompt edit — a modified component is a whole file — so none of these is a diff
and none of them carries context lines.

Because a replacement is a whole body, any stock sentence still in it was kept
on purpose rather than quoted to anchor a patch. Measured against the release
`release.json` names, re-measured 2026-09-25:

| Fragment | Stock body | This body | Kept | Longest kept run |
| --- | ---: | ---: | ---: | ---: |
| `system-prompt-delivering-work-at-full-scope` | 337 w | 375 w | 0 w | — |
| `system-prompt-communication-style` | 220 w | 81 w | 9 w | 9 w (the section heading) |
| `tool-description-todowrite` | 1410 w | 239 w | 17 w | 12 w (the opening line) |
| `system-prompt-outcome-first-communication-style` | 442 w | 99 w | 47 w | 21 w |
| `system-prompt-correction-restraint` | 201 w | 121 w | 53 w | 23 w |
| `system-prompt-subagent-delegation-examples` | 343 w | 254 w | 146 w | 50 w (a worked example) |

The measurement is exact, so it is worth saying which one it is: split
`ccctl.stock_body(entry)` and `ccctl.edit_text(path)` on whitespace — so
frontmatter is out and punctuation stays attached to its word — mark every
position of the replacement that starts or continues a four-word sequence also
present in the stock body, and report the marked positions as maximal runs.
A `difflib.SequenceMatcher` block count reads lower on the same two texts,
because its blocks are non-overlapping and chosen greedily; that is a different
quantity, not a correction of this one.

Stock wording appears in two other published places, and both are as short as
the job allows rather than as short as they look. `edits/ANTIMARKERS.txt` has to
write out the sentence whose absence it checks; its active entries are
trimmed to the shortest form whose count still comes out 1 in stock and 0 in a
correctly patched binary — 3 to 6 words each, measured on each release that
added one. And the adhoc patches are not tweakcc components — they sit outside the
prompt map tweakcc extracts (`spec/00` §2.3) — so they are located against the
binary in `ccctl.plan_adhocs` rather than written as files. One of them,
`bypass-auto-shell-block-invert`, used to spell out about fifty words of the
stock shell-tool block to find it; it now carries a SHA-256 of that block
instead (`ccctl.SHELL_BLOCK_SHA256`, taken over the template literal with its
interpolations normalised, so the digest survives minification), and no copy of
the prose. `git-commit-authority` and `bash-audience-note-result-not-output`
find the one sentence they replace the same way (`ccctl.StockSentence`: a few
opening words to narrow the scan, then length and SHA-256). `edits/targets.json`
indexes every target either way.

## Licence

MIT (`LICENSE`), covering the specs, the edits' authored text and the tools;
it does not cover Anthropic's prompt text or any stock wording quoted inside
them, and it does not cover tweakcc, which is MIT from Piebald-AI.

<!-- DRAFT: needs author -->
Two further pieces of text in this tree are tweakcc's rather than mine: the
`name:` and `description:` frontmatter of each `edits/*.md` file and the prompt
ids throughout, which come from tweakcc's published prompt data, and the context
lines of the patches in `tools/tweakcc-overlays/`, which are diffs against
tweakcc's source. The probe rigs under `probes/rigs/` are synthetic
repositories written for the probes, including the vendored `tinytoml` in
`p6-shopcli` and its licence notice.
