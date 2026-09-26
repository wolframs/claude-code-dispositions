# Repository agent instructions

## Resolve encountered staleness

Stale, drifted, outdated, partially implemented, or broken repository machinery is
work to finish, not merely a condition to report. When an agent encounters it,
the default is to repair it and make the repair durable with appropriate tests
and documentation. This includes local adaptations around upstream tools such
as tweakcc: “upstream issue,” “pre-existing,” “separate effort,” and “out of
scope” do not by themselves justify leaving known stale behavior in place.

Recover the intended behavior before asking [HUMAN]. Use, in order:

1. deduction from explicit specifications, invariants, tests, and current
   requirements;
2. induction from prior commits, neighboring implementations, notes, and
   transcripts; and
3. abduction from the best explanation of the observed behavior, followed by
   a discriminating test rather than an unsupported guess.

If those sources establish a defensible course, take it, verify it in
proportion to risk, and record any non-obvious reasoning for the next agent.
Ask [HUMAN] only when consequential alternatives genuinely remain after that
investigation and choosing among them requires an operator preference, missing
authority, or information that cannot be discovered safely. A question is a
last resort for an irreducible decision, not a substitute for repository
archaeology or debugging.

This autonomy does not authorize unrelated product changes, destructive
actions, or external side effects outside the scope [HUMAN] supplied. It does
authorize the ordinary local code, test, and documentation changes needed to
finish an encountered repair faithfully.

## Task authority

Reversible work within the requested task is already authorized. Do not ask for
permission for ordinary implementation, investigation, repairs, or useful
supporting work. Persistent settings changes are included when they materially
improve the solution: persistence alone is not irreversibility. Evaluate a good
solution through correctness, robustness, usability and maintainability, not
merely whether the narrowest implementation can finish. State the concrete
connection to the task and preserve a practical rollback for material changes.

Existing authorization persists. Ask only when a consequential commitment is
outside the task and standing authorization, or an irreducible operator choice
remains after investigation. Incidental discovery does not authorize deleting
another project's files or creating a PR outside the operator's private
repositories. An unrelated settings preference is not covered; a settings
change justified by a better solution to this task is. Silence does not expand
scope. The operator's wording is in notes/2026-09-05-task-authority.md; the
deployed formulation lives in edits/system-prompt-delivering-work-at-full-scope.md.

## A Claude Code update is a hostile event until proven otherwise

Every CC release can change *how* the prompt is assembled, not only *what*
the fragments say. The markers only prove our bytes are in the binary; they
say nothing about whether the code path that reads them still runs. On
2.1.270 the communication section grew a model-keyed branch that returned a
stock "narrate as you go" line before ever reaching our fragment, on Fable —
`status --check` reported every marker present, every deletion holding and the
adhocs landed — and the operator got a day of narration (closed as item 27 in
notes/archive/TODO-closed-2026-09-20.md;
notes/2026-09-15-fable-turn-updates-override.md). [HUMAN], 2026-09-15: "CC
version updates are *dangerous*". **Never hard-code a marker or target count in
prose**: `edits/MARKERS.txt` and `edits/targets.json` are the count, `status
--check` is the verdict, and a written-down number is stale within two rounds.

So an update is not done at "markers OK". Before any machine's update is
called complete, and before the fragments are trusted on a new CC version:

1. **Diff the gates, not just the fragments.** For every patched fragment,
   find the function that *chooses* it in the new binary and diff it against
   the previous version: new branches, new capability names in the model
   catalogue (`*_prompt_bundle`, `turn_updates`, `silent_turn_reminder` and
   their kin), new server flags (`tengu_*`), client-data keys (per model and
   entrypoint, in `~/.claude.json`; `ccctl.py flags` lists them) and env
   overrides (`CLAUDE_CODE_*`) on the way to our bytes. A branch that returns
   before our fragment is a silent revert.
2. **Read the delivered prompt on every model the operator actually runs,
   under every harness they use** (the models in `~/.claude/settings.json`,
   not Haiku alone): `ccctl.py status --delivered` captures the exact first
   request of a `claude -p` session and of an Agent SDK session launched the
   way T3 Code launches it, on 127.0.0.1, and scores every target against it.
   It spends nothing. Paste its verdicts into the machine note. A gateway that
   rewrites requests after CC is the one thing it cannot see; for that,
   `status --live` asks the model to quote the section back, one paid turn
   per model.
3. **Look for new per-turn injections** — reminder strings that are fed
   mid-turn (the "user hasn't heard from you", "privately list what you need
   next" family). They live outside the prompt map tweakcc extracts, so
   `baseline/<ver>/prompts.json` cannot show them. `status --delivered`
   scripts seven silent tool turns per model and harness and lists what the
   harness injected; grep the binary for the ones with other triggers.
4. **Record what changed in the assembly** in the machine note even when the
   answer is "nothing": the absence of a finding is a measurement only if the
   check was run.

**A CLEAN analyze is a statement about locators; a green apply is a statement
about the plan it was handed.** Neither says the plan was complete. `apply`
re-derives the plan against the *rewritten* bundle, after tweakcc's own patch
set has moved modules; a derivation step that silently drops something there
produces a narrower plan, and every assertion downstream — marker counts,
"the replacement bytes are present", the launch test — agrees with it, because
they check the plan, not the intent. On 2.1.280 that shipped a half-cut gate
(notes/2026-09-23-macos-2.1.280.md). When a derivation cannot resolve part of
what it was asked to patch, it must be MANUAL, never a quiet skip; and
`status`'s gate parse, which compares the binary against a description rather
than against the plan, is the check that catches what the plan missed.

Steps 1 and 3 read the bundle, and the bundle's logic is the same on every
platform: only the minified names differ. The win32 gate was measured
identical to the Linux gate on 2.1.270 and again on 2.1.278. So they run
once per CC version, on the first machine to take it, and its note carries
the result. Every later machine runs only the checks that depend on the
machine:

- `update` or `apply`, which re-derives every locator on this machine's own
  bytes and refuses anything not AUTO;
- `status --check`;
- `status --delivered`, which reads this machine's settings, client data and
  binary;
- `flags`.

A repo change on a CC version already verified somewhere needs `pull`,
`apply`, `status --check` and `status --delivered`, and nothing else. If one
of them disagrees with the first machine's note, that is a finding, and the
full procedure runs again on this machine.

`update` also ends by resolving the subagent model pin against the new binary
and asking the operator when an alias has moved under him. That is a question
to relay, not a line to skim past; spec/40 has the reasoning.

**Some prompt text is not in the bundle at all.** A GrowthBook flag can carry
a *string*, and CC will render it in place of the text you read in the binary —
the Agent tool's `## When to use` section works exactly this way today
(`tengu_lucky_cerf_text`, TODO 32). Such a section can be rewritten between two
sessions with no release, no marker moving and no fragment changing, so neither
a gate diff nor a marker check can see it. `ccctl.py flags` lists the
client-data keys and says which it cannot classify; an unclassified one is a
question, not noise.

**And since CC 2.1.280, a built-in plugin can replace a section or inject into
every turn.** First-party plugins register hooks on `prompt.section` and
`prompt.submit`; `responsive-mode` uses both to reinstate narrate-before-you-act
(TODO 34). Each is gated by a `tengu_*` flag for availability and ships off by
default, so this is a watch rather than a mask — but a plugin's text is in the
bundle and reaches the model without passing through any fragment we patch. A
new `isOnByDefault`/`registerPlugin` pair in a release is step 1 material.

If any of these finds a gate that bypasses our text, the fix is the
narrowest override that restores the reviewed fragment (an env line, an
adhoc), with the delivered prompt as the proof — and a TODO item for the other
machines, since the gate is in the bundle, not the host. A server experiment
arm (GrowthBook or client data) is not a gate to override: the operator's
ruling of 2026-09-11 is to accept it and surface it (archived as TODO 19 in
notes/archive/TODO-as-of-2026-09-22.md).

## Keep the repository consolidated

CLAUDE.md is a symlink to this file. Maintain this shared source; do not fork
separate Claude and other-agent instructions.

- Read README.md, spec/00-disposition-spec.md, and TODO.md for current work.
  release.json records the Claude Code release the edits were last checked
  against, with the prompt map, tweakcc commit and pristine hashes that check
  used. It is a checkpoint, not a permission: advancing it to whatever is
  installed is the routine step in spec/40, runnable by an agent, and nobody
  has to approve the number. It is not a live fleet poll either.
- **Before touching any machine, read TODO.md's machine table and run
  `ccctl.py status`.** The three machines are not interchangeable: they run
  different settings, and one is routinely a round behind the others. The table
  says what each was last measured to have; `status` says what this one has
  now. Never infer a machine's state from another machine's note.
- **The machine settings this fleet depends on live in spec/40's runbook** —
  the env lines, the tripwire hook, and the subagent model pin — and nowhere
  else. `ccctl.py status` reports each of them against the live binary, so what
  a machine actually has is read, never assumed. Do not restate their values in
  another doc; point at the runbook and let `status` be the observation.
- Update the current document in place when a decision changes. Remove the
  superseded claim from current guidance; do not append a correction that makes
  readers reconstruct which paragraph won.
- Preserve useful historical evidence under notes/archive/, moved unchanged,
  with a line in notes/archive/README.md saying what closed it. Archived text
  is evidence, never current authority. Do not delete raw transcripts, authored
  operator text, or failed-hypothesis evidence merely to shorten the repository.
- TODO.md contains only unresolved work, stable unique IDs, the evidence still
  missing, and completion criteria. Move resolved history out. Carry unresolved
  questions forward explicitly; archiving is not completion.
- edits/*.md are deployed fragment bodies. edits/targets.json maps every current
  target to its intent, implementation and spec. The reporting floor remains in
  ccctl.plan_adhocs; the generated A/B view (ab/data.json) shows its actual
  replacement bytes. Do not maintain a second handwritten copy of deployed
  prose in a spec.
- Distinguish measured binary facts, model/transport reachability, operator
  observations, and hypotheses about model behavior. A marker check proves
  installation, not universal rendering or behavioral improvement.
- After a prompt/locator/release change: update edits/reviews.json as
  warranted, regenerate the A/B view from the hash-verified pristine binary
  (tools/build_ab.py), run tools/check_repo.py, then apply and verify if
  activation is in scope. Never claim another machine was updated because its
  code is compatible.
- A dated note is warranted for non-obvious evidence, a failed approach worth
  preserving, or an operator decision. Routine successful checks belong in the
  commit description. README.md carries no per-round status: current state is
  TODO.md's machine table and release.json, and round evidence is the dated
  notes, so a status entry there only duplicates them and goes stale.
- Before closing: run tools/check_repo.py (active links, generated artifacts,
  tests), retire obsolete entrypoints, and ensure README/TODO/specs agree. Do
  not change remote publishing or commit/push scope merely because a
  documentation checklist mentions them.
