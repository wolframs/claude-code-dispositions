# Reporting contract — what the closing message owes the operator

Status: **first version, 2026-08-25.** Written after the operator said the
ASD-STE100 requirement had been dropped early in this project. He is right.
§1 names the five commits that did it.

The clauses in §5 were drafted by five perspectives, then attacked by three
adversarial lenses each. Most died. §5.0 lists the kills, two of which killed
text that had already been applied to a binary. Read §5.0 before adding
anything here: it is a better guide to what does not work than §5 is to what
does.

**The authority for what "plain" means here is the operator's own words in the
complaint corpus.** Second to that: `~/Projects/jspace-probes/PLAIN-LANGUAGE.md`,
the house standard his lab runs on — **written by Claude (Fable 5) on
2026-07-31**, not by him, and signed as such on its last line. He commissioned
it (HT-322), kept it, and enforces it with a checker, so it is adopted rather
than authored. That makes it strong evidence about the *rules*, and no evidence
at all about what he wants; for that, only his turns count. Where the two
disagree, his turns win. The ASD-STE100 book ranks below both.

This file decides which of those rules can live inside a system prompt fragment,
and says plainly which cannot.

ASD-STE100 is Simplified Technical English, the aerospace controlled-language
standard: 53 writing rules plus a dictionary of about 875 approved words.
Issue 9 is the current edition.

---

## 1. What was dropped, and how

Five steps. Each one was defensible on its own. None of them was a decision to
drop the requirement.

1. **`0b9e95a`** — the triage keeps HT-320 as a complaint. Its own summary line
   reads `explanation too technical/dense to understand, asked for ASD-STE100`.
   The operator's turn, verbatim, after a dense findings report:

   > "Okay, and now... translate that to ASD-STE100 simplified technical english
   > so my poor brain can comprehend it :D"

2. **`0d1d5b0`** — `notes/taxonomy/my-read.md` codes a class for it:
   `unreadable-density`, defined as *"Output optimised for defensibility rather
   than for the operator being able to act."* Fifteen complaints. The same file
   names it under a standing disposition, **"Being right beats being useful"**,
   and says: *"This one I had not seen stated in the spec and I think it is the
   most under-served."*

3. **`aa61b7e`** — unification renames it. `unreadable-density` becomes
   `wrong-altitude` plus `wrong-register`. The definition that named *the
   operator's ability to act* does not survive the rename.

4. **`c3a0b8c`** — the sort makes it **D4, 24 complaints, 13%** — the second
   largest class of the year, behind only D1's 28%.

5. **spec/10** — D4 gets one payload line in v2 ("Report in plain language, the
   load-bearing thing first"), and v4 deletes it as unfalsifiable decoration.
   The deletion is correct about that line. The conclusion drawn from it was
   not: *"The wrong-altitude class is now addressed by deletion alone."*
   Then **`d6deed8`** archives the codebook, so the 24 complaints stop being
   live evidence.

An explicit request became a code, the code became a bucket, the bucket became
one line, the line was deleted, and the evidence was archived. Nobody decided
anything. That is what "silently dropped" means, and it is worth having a name
for, because this project's method makes it easy: every step is a defensible
compression, and compressions compose.

## 1.1 What he actually asks for, in his own words

Six turns. They are the specification; everything below is derived from them.
All quoted from `corpus/raw/` — the copies in `corpus/pushbacks/` are clipped at
900 characters by `tools/extract_human_turns.py`, which is why earlier readings
of these turns were partial.

> **HT-178** — "*stares at that* *tries to process it* *kinda fails* — Uhm...
> Can I get the "explain like I was just elsewhere with my thoughts" version of
> that real quick? The one without the "I actually visited a data science and ML
> seminar once" vocab? :P"

> **HT-320** — "Okay, and now... translate that to ASD-STE100 simplified
> technical english so my poor brain can comprehend it :D"

> **HT-567** — "my reaction to your response just now is: 'Cool story bro, nice
> u-numbers, cool N-numbers, amazing stochastics vocab, now what does this
> mean?'"

> **HT-582** — "neat, but I'm not looking at the diff in an editor, so if you
> want me to follow along, you'll have to drop the "ac-0018" style referenes and
> "the verified split" style pointers, and **actually explain what the impact of
> your action is**"

> **HT-556** — "**where to look?** I'm managing a few parallel agent sessions on
> two machines and need concrete path + line no. data pls"

> **HT-568** — "that kind of response is more helpful than the highly detailed
> concrete points enumerations (**I'll ask for concrete details when it matters
> to me** ;))"

Read together they are sharper than "plain language", and they contradict a
naive reading of it in one useful place.

- HT-582 and HT-556 are **not** the same complaint pointed twice. He wants
  *fewer* internal labels and *more* external locators. `ac-0018`, `Band 1`,
  `the verified split`, `rotation defects` are pointers into a record only the
  writer has read. `path:line`, a commit hash, a filename, a command are things
  he can act on. The rule is not "less reference" — it is **swap the label for
  the thing, and say the impact**.
- HT-568 praises a reply for *not* being "the highly detailed concrete points
  enumerations", and says why: he asks for detail when he wants it. So the
  detail he did not ask for stays out of the reply. (Until 2026-09-21 this
  file read the same turn as "nothing needs cutting; it needs ordering", and
  the floor said so. §5.4 has what that cost.)
- HT-178 names the register better than this project could: *the version for
  someone who was just elsewhere with their thoughts.*

## 2. The reader

These are facts about the operator, not preferences. Under spec/00 §7.3 they are
class 3 — things Claude cannot know — which is the only class not bounded by the
strain argument.

- He works in Claude Code **4 to 12 hours a day, 5 to 7 days a week**, with
  **2 to 6 sessions running in parallel**. His words.
- **He reads the closing message. He does not scroll back.** His words:
  *"what I see when they finish 30 minutes of work is not what they have seen,
  I see their finishing report. I don't go scrolling back through 30 verbose
  printed bash calls to catch what they might have thought mid run."*
- Half of his sessions are unattended. The closing message is read hours later,
  or on a phone, or never.
- He was able to work this way before. His words: *"that used to be possible in
  times when Claude didn't think that I'd be with them in every step and that
  I'd follow their highly project specific vocab."*

The consequence is one sentence long. **The closing message is not the end of a
conversation the reader had. It is the only thing the reader gets.** Everything
above it — including Claude's own mid-run text — is scrollback, and scrollback
is not read.

### 2.1 The honest version of that claim

Measured over the 755 human turns, not asserted:

- His **median** gap between turns is 9 minutes and 7 tool calls. Most of the
  time he is *there*. "30 minutes of verbose bash calls" is the p90 case:
  26% of intervals carry ≥25 tool calls, 14% ≥50, 5% ≥100.
- **55% of his turns have two or more sessions live within the hour; 15% have
  three or more; the peak is five** (2026-08-08). That is a floor — it counts
  only sessions he typed into.
- 17 turns say outright that he was away, asleep, travelling or on another
  machine. 17 more are "are you still going?" pings, because run state is not
  visible to him. 10 turns discover something hours late that was on screen the
  whole time (HT-490: *"I only just now realized: You didn't actually use the
  emotion vector lens at all for Unit 20?"*).
- **Counter-evidence, and it is real.** HT-214: *"I'll check in every now and
  then after you updated the page, I'll read backscroll :)"* He says he scrolls
  back. HT-537 is the one turn in 755 where he demonstrably read a mid-run
  sentence. HT-391: *"nothing of any importance is happening on this system at
  all… only this kitty terminal session"* — he is not always multitasking.

**So the target is the tail, not the median**, and the tail is where it gets
worse rather than better: the longest runs produce the longest closers — 2,500
to 3,800 characters, two screens at his font size — so the report grows exactly
as his attention shrinks. HT-351 is that collision stated:

> "Okay you just printed two screen's length of output at full 1440p kitty
> screen real estate, fullscreen, font size 11, and i want to deal with none of
> it."

Two of the three long closers measured got no engagement at all. After one he
compacted and pasted the message back in as context; after another he replied
only *"Continue from where you left off."*

`system-prompt-outcome-first-communication-style` already tells Claude that
everything needed must be in the final message. That half is covered and it is
not the failure. The failure is that the final message is *written from inside
the session* for a reader who was not in it.

### 2.2 The artifact the harness already gets right

Claude Code has a feature called the session recap (`awaySummary`). It fires when
the window loses focus for five minutes or more, and it is generated from
`agent-prompt-away-summary-generation`, which is 40 words long. Read it in
`tweakcc --list-system-prompts` under that id (2.1.273 and unchanged since
2.1.233); in outline it says: the reader stepped away and is coming back, so
recap in under 40 words and one or two plain sentences without markdown, lead
with the overall goal and the current task, then the single next action, and
skip the root-cause narrative, the fix internals, the secondary to-dos and the
tangents.

On 2026-08-25 that produced, for a session the operator screenshotted:

> *recap: Six attempts to launch the gemma-4-31B training run all failed on bad
> vast.ai boxes, costing $0.83 with $9.55 credit left; nothing is rented now.
> Your call: retry later, widen the GPU class, or fix offer-rotation first.*

That is the target shape, it is 45 words, and Anthropic wrote it. It also uses
a better term — *offer-rotation* — than the same session's closing message,
which said *rotation defects* and cost a round trip (C-05).

**Two findings follow.** First, the specification we want already exists in the
binary; it is simply not applied to the closing message. Second, the recap is
skipped exactly when this operator needs it. Read from the live bundle:

```
if(!M?.force){let{pendingAgents:W,pendingWorkflows:V}=Afn({...});
  if(W>0||V>0){E("[awaySummary] skipped: background work pending");return}}
```

Background agents or workflows pending means no recap.

**Corrected before this was filed anywhere.** The first version of this note said
that gate fires because he runs 2 to 6 parallel *windows*. It does not. `Afn`
iterates `Object.values(tasks)` from **this session's** state — backgrounded
agents (`IG(i)&&i.isBackgrounded`) and `local_workflow` entries. Separate Claude
Code windows are separate processes with separate state and are not in that map.
The 55% parallel-window figure has nothing to do with this gate. What does reach
it is his use of background agents and monitors *inside* one session, which the
screenshots show ("1 monitor still running"), and there are six other skip
reasons besides — draft input present is a likelier one. Still worth
`/feedback`; the reasoning had to be right first.

## 3. Three failure shapes, measured

All three are from the operator's own screenshots on 2026-08-25. Each one names
a falsifier: something checkable by reading one message.

### S1 — the first sentence carries evidence, not state

Closing message of a long unattended run, opening sentence:

> "Attempt 7 running on 48693766 — offer 47792407, the box the corrected sort
> surfaced: $1.833/h effective, up=7151, down=8974, cuda 13.3."

Two identifiers and four numbers before anything the reader can act on. The
message is accurate, specific and unpadded. It fails on one axis only: what the
reader needs first. Compare the recap for the same session in §2.1.

**Falsifier.** Read the first sentence alone. Does it say what state things are
in and whether anything needs the operator? An identifier or a measurement in
that position is the defect.

### S2 — metaphor and coinage as load-bearing terms

Closing message from a graphics session:

> "the cats now hop on bass transients (raw_kick, threshold 0.05 measured
> against Parameter People itself, 0.25 s refractory), each hop a 0.34 s
> ballistic half-sine with seeded per-cat stagger and strength-scaled height"
> … "Tracker wraps … are demoted to a soft head nod."

*Cats hop*, *ballistic half-sine*, *seeded per-cat stagger*, *soft head nod*,
*tracker wraps*. Some are real terms. Some were built during the session. The
reader cannot tell which from the message, and that is the whole problem.

His own standard bans this in the plain layer and tells its writers to read the
rule twice:

> "Read R11 twice. The lab's voice is built on metaphor — *furniture*, *weak
> king*, *elephant tax*, *the mouth says No*, *the sub-unit of shame*. Every one
> of those is banned in the plain layer and belongs in the research notes."

**Falsifier.** Take each multi-word term in the opening. Is it in the code, in
the docs, or in the operator's own words? If not, it was coined this turn.

### S3 — the coined noun

*"fix the two rotation defects first"* → *"what are rotation defects"* → *"My
coinage, and one of the two doesn't deserve the name."* Full intake is C-05.

**Falsifier.** Same as S2. C-05 installed the clause.

## 4. His standard, and what ports into a prompt

His `PLAIN-LANGUAGE.md` §2 lists 17 machine-checked rules. Not all of them can
live in a system prompt: he enforces his with `probes/ste.py` and a controlled
vocabulary in `plain/terms.json`, and a prompt fragment has neither.

The test for porting a rule is spec/00 §7.3: **can a reader falsify it by
looking at one message?** A rule with a number passes easily, which is exactly
why he reaches for this standard. His own reason:

> "We follow it because it gives **checkable numeric limits** where 'write
> clearly' gives none."

| His rule | Ports? | Why |
| --- | --- | --- |
| R1/R2 sentence length ≤ 25 / ≤ 20 words | **yes, scoped** | A number. Applies to the opening only — see §6 on why a whole-message cap is wrong. |
| R5 multi-word noun ≤ 3 words | **yes** | Countable. This is his "no noun stacks" from `CLAUDE.md`, with a limit attached. |
| R10/R12 one referent one term; terms of art registered on first use | **installed (C-05)** | The anti-coinage clause. Registration becomes "it exists in the code, the docs, or his words". |
| R11 no metaphor, idiom, irony, in-joke | **yes, scoped** | S2. Scoped to the opening: his own standard confines it to the plain layer and keeps metaphor in the research notes. |
| Spell out an abbreviation at first use | **yes** | He asks for this directly in his own preferences. Costs one clause. |
| R13 numbers carry their scale | **yes** | "$5.68 against $9.55" not "$5.68". Cheap, and it is what makes a number actionable at a glance. |
| The 30-second rule (≤ 90 words, any entry point, no prior reading) | **yes, as structure** | This is the inverted pyramid with a number and a reader model. It is the single most valuable line in his file. |
| "Nothing is deleted" — dense text moves to a container | **yes, as structure** | Resolves the whole tension. See §6. |
| R3 ≤ 6 sentences per paragraph, R4 one topic per sentence | partial | Fold into the length rule rather than spending separate clauses. |
| R6 active voice, R9 no contractions, R14 no semicolons, R15 no banned modals, R16 no Latin abbreviations, R7 no -ing forms, R8 simple tenses | **no** | Each is one more rule to satisfy at a fixed rate, and none of them appears in a single complaint in this corpus. Under spec/00 §5.0 they would be tics. |
| The approved dictionary (~875 words) | **no** | Needs `terms.json` and a checker. Cannot be carried in a fragment. |
| §3.1 the uncertainty ladder | **no, but recorded** | A closed set of confidence phrases. Installing it would fix a rate and destroy the signal (§5.0) — the *occurrence* of a hedge is the information. Kept here because it is the right answer for a written artifact, and it is available to any project that wants it in its own `CLAUDE.md`. |

### 4.1 The line that does the most work

> "**Nothing is deleted.** The original text stays exactly as written and moves
> into a **Research notes** container on the same page. The plain layer is added
> on top; the lab's record is not edited."

And the operator, in HT-322, asking for the same thing before that file existed:

> "that kind of content belongs into a separately visible container on each part
> of the website, not in the output text."

This is why "be shorter" is the wrong rule and has always been the wrong rule.
He does not want less. He wants the plain layer first and the dense layer after
it. His preference block says the same thing from the other side: *"ASD-STE100-
style wording. Simple sentences, tough content is fine."*

A terminal has no sidebar. The container is **position**: the first sentences
are the plain layer, everything after them is the research notes, and the reader
decides where to stop. That costs no characters to say and it is checkable.

## 5. What is installed

**One fragment, plus the patch that makes his model load it.**

| target | stock | ours | what it does |
| --- | --- | --- | --- |
| adhoc `lean-models-get-the-communication-fragment` | 39 B | 46 B | adds `\|\|ek(e)` to the selector's branch-2 condition, so lean models reach the fragment below instead of returning early |
| `edits/system-prompt-outcome-first-communication-style.md` | 2,596 | **1,318** | the contract |

`delivering-work-at-full-scope` is **unchanged at 1,084**. A version of this
contract was authored into it and applied, then withdrawn when the gate patch
made it unnecessary — see spec/10's v4.3 note for why that reflex was wrong.
Item 8 keeps its full budget.

> The longer you work, the less of it they see. After a run of many tool calls,
> assume they were elsewhere. The last message is the only part they read, often
> hours later, against several other windows. Every name, number and piece of
> shorthand that arrived during the work is new to them.
>
> So open with where things stand, and name the thing you worked on. They have
> several of these windows open and yours is not labelled. If they read two
> sentences and stop, they should know whether to act. What comes after that is
> for whoever wants it; nothing has to be cut, it has to be second.
>
> *(Replaced 2026-09-21 by the selection sentence in §5.4.)*
>
> A phrase you coined this turn is not a name. Say what it means the first time
> you use it, or say what changed instead of naming it. The same goes for an
> abbreviation, and for a label that lives only in your record of the run.

Plus one sentence restored from stock — the *"Write code that reads like the
surrounding…"* line, in full in `edits/`. Lean models
get that today as the *whole* of their communication block. The gate patch routes
them away from it, so it has to come along or they lose text they currently have.

| Line | Class (§7.3) | Answers | Falsifier |
| --- | --- | --- | --- |
| "The longer you work, the less of it they see" … "new to them" | 3 — information | §2.1: the closers get longest exactly where his attention is shortest, and no version of the prompt has ever carried this. | The message refers to something as already known that only appeared mid-run. |
| "open with where things stand, **and name the thing you worked on**" | 1 — repair | HT-402, and the parallel-window count in §2.1. The naming half is what survives the audit below; leading with an outcome does not. | Read sentence one with no window title. Does it say which thing, and whether the run is over? |
| — *"and yours is not labelled"* | **cut** | Shipped in the first apply and **false**: his `statusLine` runs `~/.claude/statusline-command.sh` and prints cwd, git branch, model and context percent on every window, which the screenshots confirm. The window is labelled; the *message* is not. Replaced with "the message is what tells them which one this is". | — |
| "If they read two sentences and stop, they should know whether to act" | — | The lab standard's 30-second rule, converted from 90 words to a sentence count. | Read two sentences cold. |
| "nothing has to be cut, it has to be second" | **retired 2026-09-21** | Read HT-568 backwards: the turn praises a reply for leaving the enumeration out. With turn_updates masked it was the only length signal on the lean models, and it pointed the wrong way. §5.4. | — |
| "A phrase you coined this turn is not a name. **Say what it means the first time you use it**" | 1 — repair | C-05, S2, S3, HT-582. | Take each coined term and each abbreviation. Is its meaning in the same message? |

**Every sentence in the fragment is 25 words or under**, which is the lab
standard's R1 limit for descriptive text. Checked, not asserted.

### 5.0 What the adversarial pass killed, and why it was right

Five independent perspectives drafted clauses; three adversarial lenses per
clause then tried to refute them. Most died. The kills are more useful than the
survivors, and two of them killed text that was **already applied to a binary**.

- **"If it is not in the code, the docs, or their own words, say what changed
  instead of naming it."** Killed 3/3, and correctly. A thirty-minute run
  *writes* code, docs and commit messages, so by the time the closing message is
  written, every label minted mid-run is greppable — the exit clears the exact
  tokens the rule exists to catch. Checked directly: `ac-0018` is in
  `47tripwire-analysis/PROJECT_STATE.md`, `arm-B` is in
  `jspace-probes/probes/audit02.py`. The clause reliably switched itself off.
  **Replaced by expansion-in-place:** the harm was never that a term was
  invented, it is that its meaning is not in the message. Greppability is
  orthogonal.
- **"Point at what they can open: a path, a line number, a command, a commit."**
  Killed. It duplicates a line already in the lean base prompt, which Opus 5
  loads unconditionally, the one telling it to *"Reference code as
  `file_path:line_number`"*. Verified in the live bundle. Under C-03's stacking mechanism a
  duplicate is worse than nothing.
- **"Ordinary words, not an ID and not a measurement."** Killed on his own
  counter-evidence. HT-544: *"since this is strictly important, I cannot
  evaluate the outcome based on reading prose. Please provide strictly terse,
  human PII data focused only status reports."* He has asked for the measurement
  first. A prohibition with no exit (§7.1).
- **"…and whether anything needs them."** Killed under §5.0: it mandates an
  ask-status field on every closing message. The *presence* of an ask is the
  signal — it is how he learns a decision is his, and it is TODO item 8's whole
  subject. Mandated, it becomes "Nothing needs you." at a fixed rate.
- **"Lead with the outcome" as its own clause.** Killed by a count rather than
  an argument, which is the strongest kind. Nine of the twelve rejected closing
  messages already lead with a stated outcome. Recorded as a third corollary in
  spec/00 §7.3: a rule the failing output already passes cannot be a fix.
- **"Give every number its scale."** Killed by construction: *"13 of 13 checks
  pass, 6 of 6 panels render at 2 of 2 window sizes"* obeys every letter and
  manufactures denominators. It is the lab standard's R13 and it belongs in
  `CLAUDE.md`'s format layer, not here.

### 5.0b The per-model split, and what it does to the causal story

**Nobody read the `model` field until the completeness pass, and it inverts the
round's own argument.** The density complaints are not one population. Resolved
against the producing model in `corpus/raw/`, they split roughly evenly between
`claude-fable-5` and `claude-opus-5` — and the two models were in completely
different prompt situations:

- **Fable 5 took stock branch 2.** It *had* the anti-jargon text, including
  its *"they don't know the codenames or shorthand…"* clause and its
  *"Don't make the reader cross-reference labels…"* one. Every Fable-5 complaint here predates the 2026-08-19 deletion. So
  that instruction was live, on the right model, and lost.
- **Opus 5 took branch 3.** It had no communication block at all — 94 characters
  about code comments. It could not have obeyed a rule it never received.

**What that means for what shipped.** The coinage clause is *not* the new thing
for Fable 5; a version of it was already there and failed. What is new for Fable
5 is the reader model and lead-with-state. What is new for Opus 5 is everything,
because the gate patch is the first time it receives any of this at all. Those
are two repairs to two different situations, and this round ships them as one
fragment. That is defensible — the text is true for both — but the *evidence*
for it is not uniform, and the C-05/C-06 story that "we deleted the guardrail"
does not survive contact with this table.

**Method note, and it is the reusable part.** Three sessions in a row now, the
causal claim has been wrong in the same direction: something was deleted,
therefore the deletion caused the failure. Each time the correction came from
reading dates or fields that were already in the corpus. The complaints carry a
`model`, a timestamp and a session id; use them before proposing a mechanism.

**Caveat on the numbers.** Two independent passes over the same question
disagreed on at least one turn (HT-582), because one walked back to the nearest
preceding assistant record and the other took the session's last model. The
*split* is solid; per-turn attribution needs the careful method, and TODO item 15
is where that gets done properly.

### 5.1 Routing — and the reason none of this reached him

**The prompt is model-divergent, and nobody had checked.** `qAE` returns exactly
one of four communication blocks, chosen by model capability. Traced through
`dAt`, `zAE`, `ek`, `cHv`, `lHv` and the baked model catalog, then verified three
independent ways: the catalog bytes, the gate functions, and `~/.claude.json`.

| model | what decides it | branch | block | ours? |
| --- | --- | --- | --- | --- |
| **Opus 5** | `lean_prompt`, `opus_5_prompt_bundle`; no `fable_5_mitigations` | 3 (`ek`) | one sentence, about code comments | **no** |
| **Fable 5** | `lean_prompt` *and* `fable_5_mitigations` | 2 (`zAE`) | `outcome-first-communication-style` | yes |
| Sonnet 5, Haiku, Opus 4.x | neither | 4 | `communication-style` | yes |

Branch 1 (`turn_updates`, the `WAE` recap sentence) needs an env var or a
capability the catalog does not grant. It never fires.

**So on Opus 5 — the model in `~/.claude/settings.json` and in every terminal
screenshot — the entire "# Communicating with the user" section was absent.** Not
overridden: absent. Every anti-jargon sentence stock ever had, and every clause
C-05 put back, lived in branches this model never reached. This project had been
editing a fragment his main model does not load.

**The fix is one disjunct.** `if(zAE(t)||xyp(t))` becomes
`if(zAE(t)||xyp(t)||ek(e))`. Opus 5 and Fable 5 then share one fragment; Sonnet
and Haiku are untouched. The patched span deliberately stops at the backtick —
one byte further and it would overlap the fragment's own span, making the two
patches order-dependent. Both anchors derive their minified identifiers by regex
and are unique in stock and live.

The route not taken: deleting branch 3's early return, which drops lean models
to branch 4. That reaches a 190-character fragment instead of the one holding the
contract, and would mean maintaining the prose twice.

**Known gap.** The adhoc has no marker of its own. A marker must be stable across
builds, and every string this patch touches is minified. `span_apply` asserts the
exact replacement bytes are present after the write, so a silent failure is
caught at apply time — but `status --check` afterwards cannot see it. The only
end-to-end test is a live Opus 5 session: ask it to quote its own communication
section. If it can only produce the code-comment sentence, the patch did not land
and nothing else in this file matters.

**One more thing the trace settled.** *"Your responses should be short and
concise"* lives in `dHE`, and the section list forks on the lean flag —
`...o?[pHE(c,t)]:[...,dHE()]` — so Opus 5 never assembles it. His stated premise,
*"the be concise restriction placed upon them by the CC sysprompts"*, is false
for the model he runs. The compression pressure is post-training plus his own
`CLAUDE.md`, whose Length section opens with *"Prefer short"* and continues *"no
noun stacks"*. That is the C-06 contradiction relocated into his own file, and on
Opus 5 it is the only instance of it in play.

**Not the CLAUDE.md layer.** It already says *"Prefer short"*, *"Lead with the
answer"*, *"no noun stacks"* and *"Name things exactly"*. Three of the four
things this contract wants are already there, in his own words, and they have not
worked. §2.1's usual explanation — prose loses to prompt-layer procedure — does
**not** apply, because on Opus 5 there was no competing prompt-layer text at all.
What is missing is not another rule. It is the fact that makes the rules make
sense: *they read the last message and nothing else.*

**The unpatched lever, recorded and not taken.** A custom output style
(`~/.claude/output-styles/*.md`) is injected on every model, lean or not, and
re-stated every turn as a `<system-reminder>`. The built-in `Concise` style ends
by claiming precedence over the instructions' own *"more general
communication or formatting guidance"* — *"these rules win."* That layer outranks
everything this project patches, and it needs no binary. It is also a
settings-layer change on three machines rather than one repo commit, and it sits
*above* the payload rather than inside it. Worth a decision. Not worth an
assumption.

### 5.2 The floor — one patch that survives every output style

**Added after the round closed, when the operator surfaced `/config`'s output
style picker and asked the right question: how do we keep the flexibility and
still have our dispositions reach whatever it selects?**

**Through CC 2.1.261.** `rHE(e)` rendered the selected style into the prompt,
and it was called from exactly one place — `I2("output_style",()=>rHE(c))` in
the **shared** section list. Not the lean fork. Not a model-gated branch. Every
model, every session. With no style selected it returned null and the section
was dropped.

The adhoc `disposition-floor-under-every-output-style` makes it always return,
with the reader facts appended after whatever style body is present:

```js
function rHE(e){return(e===null?"":"# Output Style: "+e.name+"\n"+e.prompt+"\n\n")
  +"# How to report back\n\n...")}
```

Executed against `node` before shipping, with `null` and with a style object, to
confirm both paths render.

**From CC 2.1.268 there is no such renderer.** Upstream deleted the
`output_style` and `language` sections outright; both now ship only as
attachment messages, which reach the model **after the entire system prompt**.
That is the behaviour `staticSystemPromptEnabled` (`CLAUDE_CODE_CARVED_SLATE`)
used to gate, promoted to the only behaviour. The obvious replacement target —
the attachment renderer — is the wrong one: the generator emits that attachment
only when the style *changes*, so a session on the default style never calls it,
and a floor with a gate is not a floor. The derivation and the measurements are
in `notes/2026-09-11-win32-2.1.268.md`.

So on 2.1.268 the floor rides `context_management` instead: a bare string
constant in the same shared list, no predicate in front of it, spliced into both
the lean and full branches, sitting where the output_style section used to sit.
The older renderer shapes are still tried first, so a machine on 2.1.251–2.1.261
keeps the stronger placement.

**Why this is the strongest edit in the tranche:**

- **It is not model-gated.** Everything else in §5.1 is a fight with `qAE`'s
  branches. This one sits above them. Still true on 2.1.268, and measured
  rather than assumed: on the patched binary, default style, Haiku 4.5,
  `claude --print` asking whether `# How to report back` is in the system prompt
  answers *Yes. "The person reading this was elsewhere while"*.
- **A built-in style can no longer displace it.** ~~`Concise` ends by claiming
  precedence over the instructions' own *"more general communication or
  formatting guidance"* — *"these rules win"* — which would otherwise
  outrank every fragment this project patches. The floor now sits after it.~~
  **No longer true from CC 2.1.268.** The style body arrives after the whole
  system prompt, so nothing in the prompt can sit after it; that `these rules
  win` line is now positionally *after* our floor, not before it. Reach
  survived the upstream change, precedence did not, and the next bullet's
  closing sentence carries that weight alone now. Worth revisiting if a style
  ever visibly overrides the reporting contract in practice — this is the one
  property of the tranche that a CC release took away rather than moved.
- **The operator keeps the knob.** This is the part he asked for. The style
  selects the **mode** — how much narration, how proactive, how much teaching.
  The floor states **who is reading**, and since 2026-09-21 the operator's
  default for what a reply keeps and in what form (§5.4). The closing sentence
  draws that line rather than escalating: *"A style may set how much you
  write, in what form, and how proactive you are. It does not change who is
  reading."* A mode cannot outrank a fact, so no precedence war is needed and
  none is declared. (It read *"A style above"* until 2026-09-11;
  on 2.1.268 the style is not above anything, and a sentence that asserts a
  false position invites the model to discount the rest. Dropping the word was
  the whole edit — an intermediate draft wrote *"An output style"*, which trades
  a locator for the vendor label the operator has explicitly rejected
  ("output style [which should be called BEHAVIOR STYLE…]", TODO 23). Bare "a
  style" claims no position and is true on all three shapes. No marker or
  anti-marker quotes this sentence.)
- **It reaches Sonnet and Haiku**, whose communication fragment is 190
  characters and says none of this.

**Consequence for §5's fragment.** With the floor carrying the contract, saying
it again in `outcome-first-communication-style` would be C-03 rule-stacking
against ourselves. That fragment dropped from 1,318 to **516** characters — an
80% cut against stock — and now keeps only what the floor does not say: that
mid-turn text may never be seen, and the code-comment line lean models would
otherwise lose to the gate patch. Its main job is once again **deletion**.

**Constraints on the text, from the engine.** Adhoc replacements are built with
`.encode("latin-1")`, not through `enc_new`, so the floor is ASCII only — no em
dashes. It is built by string concatenation rather than a template literal, so
it carries no `${` and no `$` at all for JS `replace()` to reinterpret as a
capture-group reference (TODO item 5's footgun).

**Measured 2026-09-21, and it moved the floor** (TODO 30,
notes/2026-09-21-length-regime.md). Presence was confirmed 2026-09-11 — the
floor is in a live session's prompt on a non-Opus-5 model under the default
style. Effect was confirmed ten days later: closing replies had a median of 60
words under upstream's recap line and 299 under ours, which is what put length
and form into the floor itself. What remains unmeasured is whether the rewrite
holds over weeks of real sessions; `tools/closing_lengths.py` counts it.

### 5.3 The channel facts — reinstated after a live loss (item 21)

2026-08-29, macOS, patched 2.1.241, Fable 5, headless gateway turn: a full
mid-turn answer never became an assistant text block. The API recorded a
`narration`-tagged thinking *summary* where the text belonged; headless
transports drop that channel, so the message was gone. 4 occurrences across
2 turns. Forensics: `notes/2026-08-29-narration-channel-loss.md`.

**The first version of this section got the causation half wrong, and the
correction is the reason the restate clause below exists.** It read: "the
cause was ours ... Fable 5 sometimes resolved the tension by leaving whole
answers in thinking." Measured against the binary on 2026-08-29, that is not
what happens.

The narration channel is not ours and is not promptable. It is a platform
feature with first-class client support, verified three ways in 2.1.251:

- `isNarrationTaggedBlock` decodes the thinking block's signature (protobuf
  field 2 → 1 → 8) and compares it against the constant `"narration"`; the
  sibling constant is `"summarized"`.
- The TUI renders those blocks through a dedicated component,
  `AssistantNarrationSummaryMessage`. Interactive readers see a paraphrase,
  which is why this failure is nearly invisible outside headless transports.
- `narrationBlockCount` is a field in per-request API telemetry.

No system prompt creates that channel, and none of our text can stop the
model using it. What a prompt can still reach is what the *closing* message
does, because the closing message is the one part that did arrive.

**What was actually ours.** Stock's outcome-first fragment carries this,
inside a block gated on the model capability `fable_5_mitigations` — which
`claude-fable-5`'s entry in the baked model catalog carries, and which is by
its own name a mitigation Anthropic ships for this model. Its four sentences in
outline — read them under `system-prompt-outcome-first-communication-style` in
`tweakcc --list-system-prompts`, 2.1.273, rather than here: text between tool
calls may not reach the reader; everything the turn owes them belongs in the
final message, with no tool calls after it; text in between stays brief; and —
the sentence that matters here — anything that surfaced only mid-turn or in
thinking has to be **"restate[d] in that final message"**.

The 2026-08-25 whole-span replacement kept the first half and dropped the
last sentence. That sentence is the one that blocks the inference "I already
said it, so I need not repeat it" — and the lost turn ended with exactly that
inference, a final message reading "nothing new beyond the counts above",
pointing at a message the reader never received. Counted on 2.1.251: the
sentence appears twice in the stock binary (JS template plus Bun constant
pool) and once in the patched one (constant pool only, inert). The
"deliberately NOT reinstated" list below never named it; it was missed, not
refused.

What is installed. The channel facts, in both communication fragments,
phrased differently so neither can regress behind the other's marker
(markers 13 and 14):

- `communication-style`: "Thinking is a workspace, not a channel: nothing
  composed there reaches the operator, and from the inside the loss is
  invisible. A result meant for them — an answer, a finding, a change of
  direction — exists only once it goes out as assistant text."
- `outcome-first`: "Thinking is not a fallback channel: prose left there
  reaches no one."

Both aim at the emit side, which the evidence above says the model may not
control. They stay: they are true, they cost four lines, and they are the
right thing to say to a model that *can* choose. But they are not the fix.

The fix is the restate clause, added 2026-08-29 to the **floor** adhoc
(marker 15), not to either fragment:

> The final message always reaches them. Prose written earlier in the turn
> may go out on a channel their transport drops, and you cannot tell from
> here which happened. So say what they need in the final message, once, and
> never point back at earlier output as though they read it.

**It is a delivery fact, not a restatement quota, and that is the operator's
ruling on the first draft** (2026-08-29). That draft read "Anything you said
earlier in the turn may never have reached them, and nothing tells you when
it did not arrive. Restate the conclusions here..." Three defects, all of
which stock's own version shares:

- "Restate the conclusions" is a standing output-token tax, charged on every
  turn whether or not anything was lost.
- A blanket restate rule reads as *carry your mid-turn conclusions with you*,
  spending attention on bookkeeping for the length of the turn.
- "may ... and nothing tells you" describes the harness as a mystery. A model
  cannot act on a hedge.

The rewrite states the mechanism and lets the model derive the rest, and the
rule it derives is a **prohibition** (never point back) rather than a
**quota** (always restate). A prohibition costs nothing to obey and needs no
state held across the turn: at the moment of writing the final message,
everything it needs to check is already in context. The one surviving "may"
names its variable — whether the channel is dropped genuinely depends on the
transport, since interactive Claude Code renders narration blocks and
headless drops them. That is a conditional, not a hedge.

This is the general form of the lesson, and it is worth applying wherever
else this project explains the harness: **describe the mechanism, then let
the rule fall out of it.** Prompt text that hedges about what the model can
observe buys nothing, because the model cannot act on the hedge.

It lives in the floor because `system-prompt-outcome-first-communication-style`'s
own note says the reporting contract does not live in that fragment, and
saying it in both places is C-03 rule-stacking against ourselves. The floor
also reaches further than stock's version ever did: every model, every output
style, and headless sessions — confirmed by asking a headless `claude-fable-5`
on the gateway's own flags to quote its system prompt back.

What is deliberately NOT reinstated: the pre-announce rule ("Before your
first tool call, state…") and the always-narrate floor
("Brief is good — silent is not") stay deleted and stay anti-markered.
These are channel facts, not narration quotas: they say where intended
output must go, not how often to produce it.

**Scoring — and one criterion retired.** The original test here read: "a
fixed build shows intended narration as real text blocks, not
narration-tagged thinking summaries." Retired 2026-08-29, operator's call.
Narration blocks are the platform's to emit, so that test can fail forever on
a correctly patched build and would have sent whoever ran it hunting a
prompt regression that does not exist.

Score the closing message instead, which is what the patch actually governs.
Against the two archived turns in the forensics note: a fixed build's final
message restates the conclusions it reached mid-turn rather than referring to
them, and contains no pointer ("as above", "the counts above", "as noted") to
content that only ever existed mid-turn. The multi-parallax gateway
independently hardened its dispatch prompt to demand the same thing
(self-contained finals + dedupe), so a clean turn *there* proves nothing
about this patch — score it on a headless session that does not carry the
gateway's prompt.

### 5.4 Length and form — the floor's default since 2026-09-21, layered since 2026-09-25 (item 30, C-08)

On the lean models, nothing in the prompt said how long a reply should be once
item 27 masked `turn_updates`. That mask took away stock's *"Close with a short
recap"* along with the narration it was masked for. The floor filled the gap
the wrong way: *"nothing has to be cut, it has to be second"*, and *"a style
may set how much you write"*, on a machine running no style. Fable's closing
replies went from a median of 60 words to 299, mostly bold-labelled bullets,
with his `CLAUDE.md` in context saying the opposite. §7.4 of spec/00 describes
this case: the suppressive clause was load-bearing. Measurements and confounds
are in `notes/2026-09-21-length-regime.md`.

The 09-21 selection sentence and two clauses added to it on the morning of
2026-09-25 (`notes/2026-09-25-win32-length-recount.md`) were superseded the
same day by the operator's AMA on closing messages
(`notes/2026-09-25-report-contract-ama.md`). The morning clauses aimed at the
right failure, method narrated at length, but framed it as categories to keep,
banned the one list he wants, and never named the footprint or an order.

**The contract, as derived 2026-09-25.** A closing message is four layers, in
this order, and it must work at minimum attention: the first line and the
bullets alone suffice.

| metric | layer 1: state | layer 2: footprint | layer 3: consequences | layer 4: suggestions |
| --- | --- | --- | --- | --- |
| height | 1–2 sentences | ≤ 3 sentences | as many bullets as there are real consequences | a line or two |
| width | — | — | one line per bullet | — |
| density | enough to know whether to act | what changed; which repos, remotes (pushed or not), live systems | one consequence of doing nothing per bullet; no method, no explanation | — |
| actionability | — | — | no fix attached | fixes and offers only here, skippable |

How the work was done, checked or diagnosed is in none of the layers. The
outlet for an analysis worth keeping is `reports/` in the active repo, on a
fix ask, with one pointer line; the default is no report. On a find-out ask
the analysis is the reply.

What is installed, in the floor (the second paragraph and the first sentence
of the last; the deployed bytes are in `ab/data.json`):

> Then, in a few sentences, what changed and what it touched: which repos,
> which remotes, which live systems. Then, as terse bullets, what happens if
> they do nothing — each one a consequence, not an explanation, and none
> carrying the fix. Suggestions and offers come last, where they can be
> skipped, and as statements, not questions. How the work was done, checked
> or diagnosed is not in the message:
> a turn of fifty tool calls that ended well is reported in the same few
> sentences as a turn of two. When the ask was to find out, the analysis is
> the answer; when it was to fix, an analysis worth keeping goes in
> `reports/` in the active repo with one line pointing at it — and most turns
> need none. They will ask for detail when it matters to them.
>
> A list is for content that is one; a paragraph does not open with a bold
> label.

| Line | Class (§7.3) | Answers | Falsifier |
| --- | --- | --- | --- |
| "what changed and what it touched: which repos, which remotes, which live systems" | 1 — repair | AMA: *"a short description of the change and which repos, remotes and, if applicable, systems were touched"*. | Does the reply name every repo, remote (and whether it was pushed) and live system the turn touched, and nothing about how? |
| "as terse bullets, what happens if they do nothing — each one a consequence, not an explanation, and none carrying the fix" | 1 — repair | AMA: *"terse bullets that don't necessarily explain the underlying situation but the consequences if I don't act"*. Replaces the 09-21 category keep-list, whose "with the output" invited the account behind each item. | Is each bullet one line, a consequence of inaction, with no cause story and no remedy? |
| "Suggestions and offers come last, where they can be skipped, and as statements, not questions" | 1 — repair | AMA: *"consequences only, and any suggestions were offered towards the end"*. The statement form is for CC's session-state classifier (§5.5). | Does any suggestion or offer appear before the last bullet, or end in a question mark? |
| "How the work was done, checked or diagnosed is not in the message: a turn of fifty tool calls …" | 1 — repair | The 09-25 recount: closers of 200–400 words narrating verification. A default, not a cap: "they will ask" still follows it. | Does the report's length track the turn's length rather than what changed? |
| "When the ask was to find out, the analysis is the answer; when it was to fix, … `reports/` … and most turns need none" | 2 — outlet | AMA: *"`reports/` is probably a better to-be-created subdir if the need arises. That should not lead to a ton of output tokens being spent on reports by default though."* Without an outlet, the pull to explain lands in the closer. | On a fix ask, is there an analysis in the reply? Is there a report the turn did not need? |
| "They will ask for detail when it matters to them" | 2 — the legal exit (spec/00 §7.1) | HT-568, in his words. When he asks, the detail is the answer. | — |
| "A list is for content that is one; a paragraph does not open with a bold label" | 1 — repair | His "no house format" rule; the 09-25 count found two-thirds of closers made of bold-labelled paragraphs. The morning's "write the reply as prose" banned the consequence bullets he asked for. | Does any paragraph open with a bold label? Is each list a list? |

**It is a shape, not a cap, so §6 still holds.** It fixes an order and a
per-bullet width and drops method; it compresses nothing, which is why it
does not create the coinage pressure a brevity rule does (spec/00 §7.3,
C-06). A style may still change it. That is the knob §5.2 promises, and it is
why the floor's closing sentence names form.

Two sentences in this paragraph run past the 25-word limit (34 and 40 words,
the method and outlet sentences); the wording settled after the AMA was kept
over the limit. The change grew the floor from 1,591 to 1,888 characters; the
§5.5 statement clause took it to 1,922.

### 5.5 Stock lines that pulled against the contract (2026-09-25 audit)

A read-only audit of the stock 2.1.280 prompts against §5.4 found two stock
lines and one of our own clauses asking for what the contract leaves out, and
a fact about how the closing message is read. The `action_caution` section on
the lean models said *"say so with the output"*, the same
account-of-the-evidence the floor's keep-list lost that morning; it now says
*"say so and where the output is"* (adhoc `action-caution-outcome-line`). The
per-turn `bash_output_audience_note` ended by asking that anything the user
needs be *"put in your reply"*, which moves command output into the closer; it now
ends *"Say what it means for them, not what it printed"* (adhoc
`bash-audience-note-result-not-output`, gate untouched). The delivery fragment
ended *"report material choices and how to undo them"*, which put rollback
steps in the body; it now says *"leave the way back where the change lives"*.
And CC's session-state classifier reads the closing message: its rubric counts
a closing offer phrased as a question as a question to the owner, so the
thread shows as waiting on him. The floor now asks for offers as statements.
The audit's inventory, including the stock texts left alone because they are
masked, off or server-armed, is in `notes/2026-09-25-report-contract-ama.md`.

## 6. What is deliberately not installed

- **A word or sentence cap on the whole message.** It would break every turn
  where he asked for more, and there are such turns. His own standard caps the
  plain layer and leaves the research notes alone. Scope the limit to the
  opening, or do not install it. The 2026-09-25 layered contract (§5.4) is
  consistent with this: it is a shape — an order and a one-line bullet — not a
  length, and a turn that needs more still gets more.
- **A mandated structure with headings.** His `CLAUDE.md` is explicit: *"There
  is no house format. Don't reach for a numbered list, a table, or headers
  unless the content is genuinely a list, a table, or sectioned."* His
  PLAIN-LANGUAGE.md §4 headings are for a website with 600 records, not for a
  terminal message. The §5.4 layer order is an order of content, not headings;
  its consequence bullets are a list because the content is one.
- **The uncertainty ladder**, per §4's table.
- **Anything that assumes he is present.** Half of these sessions are read
  hours later or not at all.

## 7. How to test it

Three checks, all on one closing message, none needing a transcript:

1. **First sentence.** Does it name the state and whether anything needs him?
   (S1)
2. **First 90 words, read cold.** Hand them to someone who was not in the
   session. Can they say what happened and what to do? (his 30-second rule)
3. **Every multi-word term in those 90 words.** In the code, the docs, or his
   own words — or coined this turn? (S2, S3)

The instrument is the operator living with the patch. There are no probe
sessions and no Claude-judging-Claude batteries; that was tried and retired.
