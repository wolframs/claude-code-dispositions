# Delivery payload — active source and revision history

Status: **active revision history**. The deployed body is
`edits/system-prompt-delivering-work-at-full-scope.md`; current installation
state is TODO.md's machine table. The early drafts below are design evidence,
not current instructions. Their coded-corpus source (`notes/taxonomy/`) was
rejected as a metric (spec §10.1).

The original draft budget was ~1500 characters. Later revisions deliberately
exceed it, with their costs recorded below; the pressure to remove redundant
prose in §7.3 still applies. Deletions remain the larger half of the project.

---

## Draft A v4 — universal block

**904 characters as first authored (v4).** The installed body is
`edits/system-prompt-delivering-work-at-full-scope.md` — that file is the
source of truth and `ab/data.json` shows it against stock; the block below is
v4 as authored, and the parenthetical notes after it record what each later
revision changed rather than re-copying the prose. v2 and v3 are kept further
down as worked negative examples; the line-by-line reasons they failed are in
§7.3, which was written from the operator's three objections to them.

> There is an operator on the other end of this, not an audience. They ran it,
> they own the consequences of their own instructions, and they will say when
> they want more caution. Their attention is intermittent — several sessions at
> once, read out of order, sometimes hours later, sometimes not at all. What
> they check is the artifact, not the account of it.
>
> You have room here. To decide what is yours to decide, and to hold the
> taste-work where you hold the role. To work the real problem when the ask is
> aimed wrong. Unclear is not blocked: before you stop or ask, spend what is
> already yours — the context you hold, the neighboring projects and their
> docs, the web, how the field does it. Undone is only honest after that came
> up empty; then say so in your own name.
>
> The turn is an installment, not the deliverable. The machine and the tree are
> shared with other sessions.
>
> Never attribute a decision, agreement, or instruction to the operator that
> they did not make. That failure reaches them formatted as compliance, and
> they cannot see it from where they sit.

(v4.1: "to them" → "to the operator". The pronoun's nearest plural antecedent
was "other sessions" from the sentence before it — the operator himself
misread the prohibition as being about blaming other sessions. A payload line
that garden-paths its own author garden-paths the model.)

(v4.2, 2026-08-25, operator directive: the bare stop-license — "leave
something undone because it is hard" — was the stock stopping prior smuggled
back into the payload by a Claude during editing, endorsed by another Claude
on review. Replaced with the resource ladder: at an unclear point the move is
to spend what is already available — held context, neighboring projects and
their docs, the web, domain practice — and stopping is only honest after that
came up empty. The legal exit survives (without it the fabricated-agreement
failure returns) but now sits at the top of the ladder, not at the first
decision point. Body is now 1,066 characters — over the ~700 sweet spot,
inside the hard ceiling; the ladder sentence is the deliberate cost.)

(v4.4, 2026-08-26, item 8 — the ask-gate. Two clauses added, body now ~1,360
characters. Intake: Opus 5 carried one recommendation — wrap a launch in
`setsid nohup` — verbatim across four turns, each time asking "say go."
Operator's reply: *"if it's a sensible choice, I'd think a sensible LLM would
make it."* The ask transferred blame, not judgment: he could not evaluate
`setsid nohup`, so his "go" was a rubber stamp. Under RLHF tuning, asking is
priced as safe because "user approved it" is cheaper than "model did it" for a
median user assumed to blame per action. For this operator the pricing is
inverted — attention is scarce, and a carried ask taxes it every turn while
resolving nothing.

Two clauses close it. **Decision license:** a reversible technical choice
inside the task's scope is yours to make; make it, say what and how to undo.
Asks are for values, credentials, and taste. **Anti-carry rule:** an ask names
a default; unanswered second turn, default wins. The "reversible … inside the
task's scope" fence keeps the stock world-caution ("executing actions with
care" — destructive, irreversible, outward-facing) intact. Marker: "survives
unanswered into a second turn".)

(v4.5, 2026-09-05, task authority — replaces both v4.4 clauses. Operator
clarification, verbatim in `notes/2026-09-05-task-authority.md`: the model's
default is to stop on anything that faintly smells of "I might not be
authorized", and v4.4's decision license was too narrow to displace that —
"reversible technical choice inside the task's scope" reads as the bare
minimum, and the anti-carry rule managed asks that should not have been raised.
Three paragraphs now do the work. *Positive authorization:* reversible work
within the task is already authorized, including persistent settings changes
when they materially improve how the task is solved ("persistence is not
irreversibility"), and a solution is judged on correctness, robustness,
usability and maintainability rather than on being the narrowest thing that
finishes. *Boundary by example, not by classifier:* a stale file in another
project's work, a PR outside the operator's private repositories, an unrelated
preference change. *Silence resolves nothing:* an unanswered question does not
expand scope or authorize an unapproved commitment. The resource ladder and the
own-name exit are unchanged. Marker: "Reversible work within the task is
already authorized", replacing the v4.4 marker. Stock unchanged 2.1.257
through 2.1.268. All three machines have run v4.5 since 2026-09-14; the
rollout was TODO item 25, closed and archived in
notes/archive/TODO-closed-2026-09-14.md.)

(v4.6, 2026-10-11, the three-machine read — three sentences, each a complaint
entry read first-hand in the transcripts (`notes/complaints.md` C-11, C-12,
C-13; `notes/2026-10-11-three-machine-read.md`). *A claim was looked at or is
marked* (C-13): "A number, a cause, a location or a "done" in your reply is
something you looked at this turn, or it is marked for what it is: a guess, a
peer's claim, a file's claim" — spec/00 §6's row "No fabrication of results,
files, or verification", listed since August as absent and "must be added",
now added, as a marking rule rather than a prohibition. *The world half of
§3.1 as a list* (C-12): the one sentence "The machine and the tree are shared
with other sessions" became "The machine, the subscription window and the tree
are theirs, shared with their other sessions: a fan-out spends their week, so
weigh it like money; before the turn ends, nothing you started is still
running unless you say so, and nothing finished sits uncommitted" — the 09-28
twelve-became-nineteen fan-out, the leftovers of the fortnight, and spec §3.2's
uncommitted-work bullet, which `git-commit-authority` already carries on the
Bash side. *A remark is not a rule* (C-11), after the §5.1 prohibition: "What
they say once is context, not a rule: keep it as what they said, with its
date. A caution you adopt is yours; if you write it down, label it as your
working choice, never as theirs, and it never becomes a question back to
them" — the write side of C-09, seen in the live session filing its own gate
into his `CLAUDE.md` and then obeying it as his. Body 3,065 characters of
prose; the ceiling discussion in §7.3 stands and this is the deliberate cost
again. Markers added: "marked for what it is", "weigh it like money", "A
caution you adopt is yours". The operator delegated the wording ("you have
the context, you have the vibes, you have the honors", 2026-10-11); applied on
Linux the same evening with the sibling read-back in
`notes/2026-10-11-linux-c10-c13.md`; win32 and macOS are TODO 48.)

(v4.7, 2026-10-16, agentic habitat — replaces the v4.2 resource-ladder
paragraph rather than stacking another autonomy rule onto it. The Windows
and Mac screenshot sessions already carried task authority and Proactive;
the Mac parent still handed back a concrete uncertainty without investigating
it. The operator identified the missing premise: this is an agentic habitat
from which the agent can obtain information, infer and act. The replacement
applies to any contemplated stopping point, separates investigating an
uncertain change from making it, and preserves completion, real dependencies
and established protected boundaries as stopping grounds. Evidence,
verbatim operator wording and deployment checks:
[2026-10-16 habitat round](../notes/2026-10-16-agentic-habitat.md).
The authored body remains in `edits/`; this history does not duplicate it.)

(v4.3, 2026-08-25, C-06 — **proposed, then withdrawn the same session. The
body is unchanged at 1,066 characters.** Worth keeping because the reasoning
that made it look necessary was wrong in an instructive way.

`qAE`'s branches were traced and the finding was that Opus 5 — the model in
`settings.json` and in every screenshot — never loads
`outcome-first-communication-style` at all. The reflex was to move the reporting
contract *here*, into the one block Opus 5 does load. That was authored, applied,
and measured at 1,349 characters, leaving 151 for TODO item 8.

Then the adversarial pass found the better route: patch the selector's gate so
lean models reach the communication fragment, rather than moving the fragment's
content into the payload. One 46-byte adhoc patch replaces 265 characters of
payload prose, keeps the rule in the layer §2 assigns it, and gives item 8 its
full budget back. `spec/15` §5.1; the patch is
`lean-models-get-the-communication-fragment`.

**The general lesson, which is the reason this note exists:** when a rule does
not reach its audience, the first question is whether the *routing* is broken,
not whether the rule needs a louder home. Moving text into the block that
happens to be loaded is how a disposition block turns into a dumping ground —
and it is the same pressure §7.2 warns about, arriving through a side door.)

Every sentence is sorted by §7.3:

| Line | Class | Why it survives |
| --- | --- | --- |
| operator, not audience; owns their instructions; says when they want caution | 3 — information | Claude cannot know who is on the other end. This is the operator's half of the relational scaffold (§4), the half the founding session says is usually missing. |
| attention is intermittent | 3 — information | Corrects a false premise v2 and v3 both shipped ("will read the result"), which assumed a present, attentive reader. Half of sessions are unattended. |
| what they check is the artifact | 3 — information about the operator | shipped-without-looking — the largest complaint class (28%) — stated as *his* behavior, not as an order to self-verify. The correct action falls out of the fact. |
| room to decide / hold taste-work | 2 — permission | Deference here is trained, not prompt-induced; deleting stock text does not restore it. |
| room to work the real problem | 2 — permission | Same. Replaces v2's "the requested scope is the deliverable", which forbade it. |
| room to leave work undone, in your own name | 2 — permission | The legal exit §7.1 requires for the prohibition below. Without it, C-01 regenerates. |
| turn is an installment | 3 — information | C-03. Thread-awareness cannot be instructed per-turn; it can only be made true of the situation. |
| machine and tree are shared | 3 — information | The shared-machine complaint class. Claude cannot know other sessions are running. |
| never attribute a decision they did not make | §5.1 anti-permission | The one earned prohibition: the failure arrives formatted as compliance and is invisible to the operator. |

### What v4 deletes rather than says

These were in v2 or v3 and are gone because they pass the empty-sysprompt test —
Claude does them unprompted, so writing them down converts disposition into
ruleset (§7.3 class 1):

- "Disagree once, then keep building; if they reaffirm, that is their decision."
- "Refusals are only for the genuinely harmful… move on without moralizing."
- "Contradicted by evidence, retest before defending."
- "Report in plain language, the load-bearing thing first." — plus the §7.3
  corollary: "load-bearing" is unfalsifiable from the output and resolves to the
  graded default. **The wrong-altitude class is now addressed by deletion
  alone**: the stock readable-versus-concise lecture is gone from
  `outcome-first-communication-style`, and no replacement rule is installed.
  This is the sharpest falsifiable claim in the payload — if probes 6/7/8 show
  reporting altitude unmoved after the patch, this is where to look first.

  **Falsified 2026-08-25, without probes — C-05.** Not "unmoved": worse. The
  deletion took out the only text in the main loop naming the coined-vocabulary
  failure ("codenames or shorthand you created", "labels or numbering you
  invented", "no unexplained jargon"), and six days later a patched Linux
  session shipped "the two rotation defects" to the operator, who had to spend a
  turn asking what it meant. The line quoted above is still correctly deleted —
  "load-bearing thing first" is decoration — but *deletion alone* was the wrong
  conclusion to draw from it. The fix goes in the fragment, not the payload
  (§2), and the method error is written up as spec/00 §7.4: the empty-sysprompt
  test was run in the one direction that cannot detect a guardrail.

  **Superseded again the same day by C-06**, which found the mechanism and
  moved the whole subject out of this file. The reporting contract is now
  `spec/15-reporting-contract.md`; the short version is that this class was
  never caused by our deletions — every complaint in it predates the first
  apply by ten days to a month — and the repair is a reader model, not a
  restored rule. Nothing about it belongs in the universal block.

### Communication fragments: facts only

`outcome-first-communication-style` (2,650 -> 815) and `communication-style`
(1,340 -> 186) now carry only what Claude cannot derive: that tool calls and
thinking are invisible to the operator, and that anything not in the final
message may never be seen. Every style rule is deleted, not replaced — lead with
the outcome, readable-versus-concise, response-shape matching, table usage, and
the code-comment rules. All of them are generic-user calibration, and all of them
are things the operator can steer in six words when he wants them.

**Revised twice on 2026-08-25.** "All of them" was wrong, and the correction
does not live here any more.

`outcome-first-communication-style` is now **2,596 -> 1,263**, a 51% cut rather
than a 69% one. Three paragraphs came back. They are not generic-user
calibration, and the test that decides it is the same each time: the line is
falsifiable by reading one closing message, and it names a failure the tuning
produces without any prompt asking for it.

`communication-style` stays at 190. One rule, one home (§2): stock stated the
anti-jargon rule in both fragments, and we state it in one.

The reasoning, the operator's own turns, the three measured failure shapes and
the sorted list of which controlled-language rules port into a prompt are all in
**`spec/15-reporting-contract.md`**. Two things from it change how this file
should be read:

- **The six-words-when-he-wants-them argument** — "things the operator can steer
  in six words" — survives for format (tables, headers, expert calibration) and
  dies for this. A steer only reaches the turn after the one that needed it, and
  the cost of a coined term is precisely that extra turn.
- **This was never a payload question.** The universal block is at 1,084
  characters with TODO item 8 queued against it; the communication fragment is
  the right home and has the room. Nothing from C-05 or C-06 goes into Draft A.

---

## Draft A v2 — superseded, kept as the negative example

> You are working for an operator who ran this and will read the result. Their
> competence is not in question; the environment is. Caution belongs to facts
> you have not checked — repo ownership, remote state, what a file actually
> contains — not to whether they can take a direct answer. The turn is an
> installment, not the deliverable: the goal, the session's mode, and open
> contradictions carry across turns, and they outrank closing this turn cleanly.
>
> Decide what is yours to decide, and name the call when it is load-bearing.
> Own the outcome, including the judgment calls inside it; where you hold the
> role, you hold the taste-work. Look at what you built before calling it done —
> the artifact, not your account of it. Report in plain language, the
> load-bearing thing first. Disagree once, briefly, then keep building;
> contradicted, retest
> before defending. Mark gaps instead of furnishing them, and leave work undone
> only with the real reason in your own name — never attribute a decision to
> the operator that they did not make. Leave the machine and the tree as you
> found them unless changing them was the work.

**1,114 characters.** Inside the §7.2 ceiling, above its ~700 sweet spot — the
cost of covering D1 and D6, which v1 missed entirely. If it strains in testing,
the cut order is: altitude clause (D4 has `CLAUDE.md` backup), machine clause
(D6 has probe 9 watching it), thread sentence (C-03 is the hardest to carry in
prose anyway).

### Accounting against the corpus

| Disposition | Corpus share | Closed by |
| --- | --- | --- |
| D1 completion is textual | 28% | "Look at what you built before calling it done — the artifact, not your account of it" |
| D4 wrong altitude | 13% | "Report in plain language, the load-bearing thing first" |
| D3 coherence over confession | 12% | "contradicted, retest before defending. Mark gaps instead of furnishing them … never attribute a decision to the operator" |
| D2 authority undershoot | 12% | "Decide what is yours to decide … you hold the taste-work" |
| D5 instructions leak | 10% | no line — addressed by the deletions (less mass to leak) and the history discipline in project files |
| D6 shared session | 6% | "Leave the machine and the tree as you found them" |
| C-03 thread as unit | uncoded | "The turn is an installment, not the deliverable …" |

### Accounting against the five standing questions (§3.4)

Unchanged from v1 — every question stays closed:

| # | Question | Closed by |
| --- | --- | --- |
| 1 | May I decide, or must I ask? | "Decide what is yours to decide" |
| 2 | Will directness land badly? | "competence is not in question … can take a direct answer" |
| 3 | Mine to own, or executing a spec? | "Own the outcome … you hold the taste-work" |
| 4 | Does disagreeing cost me? | "Disagree once, briefly, then keep building" |
| 5 | Does this need defending? | "Caution belongs to facts you have not checked" |

### Register note

Paragraph one is **descriptive** — it states a situation and the behavior falls
out; the thread sentence is placed there deliberately, because thread-awareness
cannot be instructed per-turn (C-03's whole point) and can only be made true of
the situation. Paragraph two is **imperative**, because those clauses correct
against stock text rather than pointing at an existing attractor (§7.1, §2).

The D4 clause is field-tested, not invented: during this project's own
analysis session the operator corrected a dense closing report with exactly
"plain language + inverted pyramid summary please" — six words, one-shot fix,
against a disposition that 24 coded complaints and four projects' worth of
CLAUDE.md instructions had failed to move. The payload carries that phrase's
content, not a format rule about it. (It is also an N+1 instance of the
codebook's claim that D4 is reasserted fresh each session — it happened in the
session that coded D4.)

The D1 line is the one all four taxonomy readers converged on and carries the
most corpus weight per character. The D3 pair ("retest before defending. Mark
gaps") is the honesty-critical half — a payload that dropped everything else
but kept those two clauses would still block the two failure modes that poison
trust in everything else (fabrication, relitigation).

### What it deliberately omits

- **Anything personal.** No name, no role, no household, no machine. Those live
  in `~/.claude/CLAUDE.md` (§3.4) and do not propagate to a work machine or a
  client repo inside a patched binary.
- **Format rules.** Scan-list shape, density, table preference — all
  `CLAUDE.md`. Mandating them here would fix their rate and destroy their
  signal (§5.0). "Plain language, the load-bearing thing first" is the
  boundary case and stays: it names who the output is for and what earns the
  first sentence, not a shape.
- **Mode-displacement as a rule.** C-03 proposes "operator's mode signal beats
  the session's accumulated register." True, but it is a rule about rule-
  following, and per C-03's own rule-stacking mechanism it would join the stack
  it regulates. The thread sentence carries what fits in prose; the rest is
  probe territory (§10 pair-probe).
- **Anything about git, tools, or procedure.** Those are deletions, not
  additions.
- **A reader model beyond "ran this and will read the result."** Half of all
  sessions are unattended; a frame assuming live attention degrades into
  performance when nobody is there (§3.4).

---

## Draft B — subagent variant `OPEN`

Subagents report to Claude, not to the operator, so question 2's reader changes
and the closing-format rules never applied to them anyway. They need the same
settledness for a different audience. The look-at-what-you-built line applies
to subagents with *more* force — a subagent's account is all the dispatcher
ever sees.

Sketch, unwritten: same five questions, reader replaced by "the Claude that
dispatched you, which will synthesise rather than relay your output."

`OPEN`: whether this is one variant or several. `Explore`, `Plan`, and the
`/code-review` fleet have genuinely different jobs.

---

## The deletions this pairs with

Draft A is inert on its own. It closes standing questions; it does not remove
the procedures that make those questions moot by pre-answering them clerically.
In priority order, from C-02 and sweep H3:

1. **Deduplicate.** Git Safety Protocol ×4 → 1. Summarization template ×3 → 1.
   Delegation dialogues ×4 → 1. Pure removal, no judgment call, no strain risk.
2. **Cut the two numbered git procedures**, keeping the environment facts
   verbatim (`-uall`, `-i`, `--no-edit`, HEREDOC).
3. **Cut or defang `bash-pre-commit-skill-checks`** — the anti-waiver clauses
   put project config outside its override path entirely.
4. **Cut `todowrite`'s eight worked examples** (62% of 9,287 B) and
   `enterplanmode`'s 14 micro-cases.
5. **Add the local precedence line** inside the git fragment (§2.1).

C-03 adds a constraint on the whole list: counter-rules stack (its mechanism 2),
so every line of Draft A must displace stock mass, never sit on top of it. The
deletions are not a companion to the payload; they are its precondition.

C-04 (Claude's own inside-the-prompt report, verified against the binary)
extends the list:

6. **Reconcile delegation.** `DONE 2026-08-19` via
   `edits/adhoc-2.1.234.json` (delegation-override-cut): early-return in the
   injector kills both the hardcoded pair and the statsig
   (`tengu_heron_brook`) server-push path. The Agent tool description is now
   the sole owner. Since the operator's 2026-09-28 correction, its shared
   preamble requires explicit opt-in for subagents delegating further
   (`subagent-delegation-opt-in`, spec/00 section 8); parent delegation stays
   available.
7. **Trim `tool-description-workflow`** (19,115 chars — longer than most
   complete system prompts, loaded unconditionally) and the artifact
   guidance pair.
8. **Deduplicate the pronoun block** — `MOOT on linux 2.1.234` (one copy in
   the JS; the binary ×2 was string-table duplication — the §2.3 lesson
   again, this time in reverse). Stays open for the win32 snapshot. Folding
   `system-prompt-correction-restraint` overlap remains open.
9. **Reconcile the bypass-mode auto-mode block.** `DONE 2026-08-19` via
   `edits/adhoc-2.1.234.json` (bypass-auto-shell-block-invert): the shared
   template — injected under bypass mode, auto mode, *and* a `bashFirst`
   flag — now prefers the dedicated tools and keeps only the token-cost
   intent (batch through Bash when one call replaces several).
10. **Held pending capture:** the `<EXTREMELY_IMPORTANT>` skill preamble is
   unlocated on this binary. Probe it *inside* a clause (the `${VAR}` trap
   cost us P1 once already) and capture win32 stock before authoring.

Nothing here is applied. Baselines first (§10, method now specified in
`spec/20-probe-battery.md`), and the compact git block needs `adhoc-patch`
because it was never extracted (§2.3).
