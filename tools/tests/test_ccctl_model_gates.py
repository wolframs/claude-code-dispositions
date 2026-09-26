"""Tests for the model-keyed capability gates (ccctl `model_gates`).

Run from anywhere:  python tools/tests/test_ccctl_model_gates.py
No real binary is read; env dicts are passed in directly.

Why this file exists, and what it is guarding:

  * CC 2.1.270 gave the capability gate a MODEL clause. On a model carrying
    `fable_5_1_prompt_bundle`, `turn_updates` returns the stock
    narrate-as-you-go line and our communication fragment is never loaded —
    while every marker still verifies, because the bytes ARE in the binary and
    the code path that reads them simply is not taken. Found 2026-09-15 on
    macOS after a day of narration; notes/2026-09-15-fable-turn-updates-override.md.
  * `ccctl flags` cannot see this: it is not a GrowthBook arm. So neither of
    the two checks that existed before this table could ever have caught it.
  * The gate reads the env var FIRST and returns the value ITSELF
    (`if (s !== undefined) return s`). That makes the var a three-state switch,
    not a boolean: absent = the model decides, off = our fragment, and anything
    CC reads as true = the stock text forced on for EVERY model, including the
    ones the model clause never touched. Presence is therefore not the test;
    that is what `FORCED ON` is for, and it is the one state an operator can
    reach by "setting the recommended variable" and be worse off than before.
"""
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "ccctl", str(Path(__file__).resolve().parent.parent / "ccctl.py"))
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def verdicts(env):
    rows, _ = cc.model_gates(env)
    return {cap: verdict for cap, _var, _what, verdict in rows}


# --- the table itself ------------------------------------------------------

check("MODEL_GATES: every must-mask gate names a real CLAUDE_CODE_ var",
      all(var and var.startswith("CLAUDE_CODE_")
          for var, _what, must in cc.MODEL_GATES.values() if must),
      str({k: v[0] for k, v in cc.MODEL_GATES.items() if v[2]}))

# The two that displace our text. If a future edit drops either from the table
# the machines go back to having no check at all, silently.
check("MODEL_GATES: turn_updates is masked by env, silent_turn_reminder by the binary",
      cc.MODEL_GATES["turn_updates"][2] == "env"
      and cc.MODEL_GATES["silent_turn_reminder"][2] == "binary")
# Item 31: the env line masked the reminder for EVERY model, so it also
# overrode a server arm assigning it to Opus 5. The binary cut takes out the
# model default in every clause that arms it — Fable's since 2.1.270, Opus
# 5.5's since 2.1.280 — and nothing else, so an arm still reaches its model.
# Pinned so nobody "simplifies" it back to the env line.
check("BINARY_MASKED: exactly the reminder", cc.BINARY_MASKED == {"silent_turn_reminder"})
# And the two that must NOT be masked, for opposite reasons: one agrees with
# our fragment, the other is not a prompt at all. Pinned so a later reader does
# not "finish the job" by masking all four.
check("MODEL_GATES: the consonant note and the API param are deliberately left on",
      cc.MODEL_GATES["bash_output_audience_note"][2] is None
      and cc.MODEL_GATES["thinking_display_updates"][2] is None)
# MODEL_GATE_SET is the FABLE clause's literal — the LIVE_ROUTING detector for
# "does this binary route sections by model at all". It is not the roster of
# everything we track: since 2.1.280 the capabilities are spread over three
# clauses, and a capability of another clause has no business here.
check("MODEL_GATE_SET: names exactly the Fable clause's capabilities",
      all(f'"{cap}"' in cc.MODEL_GATE_SET
          for cap in cc.MODEL_GATE_CLAUSES["fable_5_1_prompt_bundle"][0])
      and cc.MODEL_GATE_SET.count('"') // 2
      == len(cc.MODEL_GATE_CLAUSES["fable_5_1_prompt_bundle"][0]))

# --- the three states ------------------------------------------------------

_masked = {"CLAUDE_CODE_TURN_UPDATES": "0"}
_both_lines = {**_masked, "CLAUDE_CODE_SILENT_TURN_REMINDER": "0"}
check("masked: the env line this round keeps reads as masked",
      verdicts(_masked)["turn_updates"] == "masked")
check("masked by env: the reminder's old line, with no binary read, is the interim state",
      verdicts(_both_lines)["silent_turn_reminder"] == "masked by env")
check("masked: 'false' masks too (verified live on macOS alongside '0')",
      verdicts({"CLAUDE_CODE_TURN_UPDATES": "false"})["turn_updates"] == "masked")
check("masked: the check is not fooled by case or stray whitespace",
      verdicts({"CLAUDE_CODE_TURN_UPDATES": " False "})["turn_updates"] == "masked")

check("UNMASKED: an absent var is the Linux/win32 pre-fix state",
      verdicts({})["turn_updates"] == "UNMASKED")
check("unverified: a binary-masked capability with no binary read is not a verdict",
      verdicts({})["silent_turn_reminder"] == "unverified")

# The footgun, and the reason this is not a presence check: `Ose` returns the
# env value, so "1" does not mask the gate — it arms it everywhere.
check("FORCED ON: '1' is reported as worse than absent, not as masked",
      verdicts({"CLAUDE_CODE_TURN_UPDATES": "1"})["turn_updates"] == "FORCED ON")
check("FORCED ON: 'true' likewise",
      verdicts({"CLAUDE_CODE_TURN_UPDATES": "true"})["turn_updates"] == "FORCED ON")

check("n/a: the two we do not mask never report a fault, set or unset",
      verdicts({})["bash_output_audience_note"] == "n/a"
      and verdicts({})["thinking_display_updates"] == "n/a"
      and verdicts({"CLAUDE_CODE_BASH_OUTPUT_AUDIENCE_NOTE": "1"})[
          "bash_output_audience_note"] == "n/a")

# --- what the tripwire says ------------------------------------------------

check("model_gates_unmasked: silent when the env line is in place",
      cc.model_gates_unmasked(_masked) == [])
check("model_gates_unmasked: names the env-masked capability and its var when absent",
      sorted((c, v) for c, v, _x in cc.model_gates_unmasked({}))
      == [("turn_updates", "CLAUDE_CODE_TURN_UPDATES")],
      str(cc.model_gates_unmasked({})))
check("model_gates_unmasked: reports a FORCED ON var rather than passing it",
      cc.model_gates_unmasked({**_masked, "CLAUDE_CODE_TURN_UPDATES": "1"})
      == [("turn_updates", "CLAUDE_CODE_TURN_UPDATES", "FORCED ON")],
      str(cc.model_gates_unmasked({**_masked, "CLAUDE_CODE_TURN_UPDATES": "1"})))

# --- the binary probe ------------------------------------------------------

check("model_gates: set_intact is None when no binary is offered",
      cc.model_gates(_masked)[1] is None)


# --- the model CLAUSES, parsed out of the gate -----------------------------
#
# Until 2.1.278 this was one byte check against one set literal. 2.1.278 added
# a SECOND clause — `bison_cairn` and `larch_cistern` on `opus_5_prompt_bundle`
# — and the byte check passed throughout, because the literal it knew was still
# byte-identical. The two sections that clause decides are the ones that carry
# our text. A check that cannot see a new arm arrive is not a check, so these
# fixtures are the minified gate shapes themselves.

_FABLE_SET = ('var U=new Set(["turn_updates","bash_output_audience_note",'
              '"silent_turn_reminder","thinking_display_updates"])')
_OPUS_SET = 'var G=new Set(["bison_cairn","larch_cistern"])'
_PREDS = ('function wle(e){return Um(We(e),"fable_5_1_prompt_bundle",e)===!0}'
          'function _4t(e){if(e===void 0)return!1;'
          'if(Um(We(e),"opus_5_prompt_bundle",e)!==!0)return!1;return!x(k,!1)}')


def _gate(clause):
    """A 2.1.278-shaped gate whose model clause is `clause`."""
    return (_FABLE_SET + ";" + _OPUS_SET + ";" + _PREDS
            + "function _2(e,n,r,l){if(l!==void 0)return l;let s=Um(n,e,r);"
              "if(s===!1)return!1;if(s===!0" + clause + ")return!0;let p=aRn(r);"
              "if(p?.data?.[e]!==!0)return!1;return!0}").encode("latin-1")


_278 = _gate("||U.has(e)&&wle(n)||G.has(e)&&_4t(r)")
_270 = _gate("||U.has(e)&&wle(n)")


# CC 2.1.280's shape: a third clause for Opus 5.5, and all three sets declared
# as ONE comma chain in a single `var` statement rather than three statements.
# Both matter — the chain is what the cut has to rewrite in one span, and the
# third clause is the one that handed `silent_turn_reminder` back.
_280_CHAIN = ('var U=new Set(["turn_updates","bash_output_audience_note",'
              '"silent_turn_reminder","thinking_display_updates"]),'
              'G=new Set(["bison_cairn","larch_cistern"]),'
              'V=new Set(["silent_turn_reminder","quizzical_shore"]),Y=64')
_280_PREDS = _PREDS + ('function gg(e){return!zz9()&&'
                       'Um(e,"opus_5_5_prompt_bundle")===!0}')
_280_GATE = ("function _2(e,n,r,l){if(l!==void 0)return l;let s=Um(n,e,r);"
             "if(s===!1)return!1;if(s===!0||U.has(e)&&wle(n)||G.has(e)&&_4t(r)"
             "||V.has(e)&&gg(n))return!0;let p=aRn(r);"
             "if(p?.data?.[e]!==!0)return!1;return!0}")
_280 = (_280_CHAIN + ";" + _280_PREDS + _280_GATE).encode("latin-1")

check("clauses: the 2.1.280 gate parses to all three arms, each with its model",
      cc.model_gate_clauses(_280) == {
          "fable_5_1_prompt_bundle": frozenset(
              ["turn_updates", "bash_output_audience_note",
               "silent_turn_reminder", "thinking_display_updates"]),
          "opus_5_prompt_bundle": frozenset(["bison_cairn", "larch_cistern"]),
          "opus_5_5_prompt_bundle": frozenset(["silent_turn_reminder", "quizzical_shore"])},
      str(cc.model_gate_clauses(_280)))
check("drift: 2.1.280's gate matches the table",
      cc.gate_drift(cc.model_gate_clauses(_280), "2.1.280") == [],
      str(cc.gate_drift(cc.model_gate_clauses(_280), "2.1.280")))

# The two ways 2.1.280 broke the parser, both from resolving a minified name by
# "first match in the whole bundle": a one-letter SET name and a two-letter
# PREDICATE name are each defined in dozens of unrelated modules. Resolving
# them globally read the Fable clause as some other module's set and left the
# new clause with an unresolvable predicate — a loud finding, but the wrong
# one, and it named nothing the operator could act on.
_far = b"z" * 200000
_decoy_names = (b'var U=new Set(["document","index","untitled"]);'
                b'function gg(e){return e.tmuxControlModeProbed}' + _far)
check("clauses: a distant module's `U` and `gg` are not the gate's",
      cc.model_gate_clauses(_decoy_names + _280) == cc.model_gate_clauses(_280),
      str(cc.model_gate_clauses(_decoy_names + _280)))

# The other half of the same lesson, and the one that actually shipped a
# half-cut gate on 2026-09-23. A SET is declared beside the gate; a PREDICATE
# is imported, and how far its chunk lands from the gate is a property of the
# build. In 2.1.280's pristine Fable's predicate sits 23 KB before the gate;
# after tweakcc's own patch set rewrites the bundle the two land ~95 MB apart.
# Windowing the predicate the way the set is windowed drops the clause in
# silence, and a dropped clause is a narrower patch that every later check
# calls OK. The name is resolved bundle-wide, and a same-named helper in
# another module loses because it does not test a model capability.
_split = (_280_PREDS.encode("latin-1") + b";var q" + b"q" * 300000 + b"=1;"
          + (_280_CHAIN + ";" + _280_GATE).encode("latin-1"))
check("clauses: a predicate whose chunk moved MB away is still resolved",
      cc.model_gate_clauses(_split) == cc.model_gate_clauses(_280),
      str(cc.model_gate_clauses(_split)))

# A clause that arms the reminder and cannot be read at all: the apply must
# stop, not quietly cut the clauses it could read.
_opaque = _280.replace(b'function gg(e){return!zz9()&&Um(e,"opus_5_5_prompt_bundle")===!0}',
                       b'function gg(e){return nobodyKnows(e)}')

check("clauses: the 2.1.278 gate parses to both arms, each with its model",
      cc.model_gate_clauses(_278) == {
          "fable_5_1_prompt_bundle": frozenset(
              ["turn_updates", "bash_output_audience_note",
               "silent_turn_reminder", "thinking_display_updates"]),
          "opus_5_prompt_bundle": frozenset(["bison_cairn", "larch_cistern"])},
      str(cc.model_gate_clauses(_278)))
check("clauses: the 2.1.270 gate parses to the Fable arm alone",
      list(cc.model_gate_clauses(_270)) == ["fable_5_1_prompt_bundle"],
      str(cc.model_gate_clauses(_270)))

check("drift: 2.1.278's gate matches the table",
      cc.gate_drift(cc.model_gate_clauses(_278), "2.1.278") == [],
      str(cc.gate_drift(cc.model_gate_clauses(_278), "2.1.278")))

# The regression that motivated all of this: an arm the table has never heard
# of must be loud, and must name what it decides.
_new = cc.gate_drift(cc.model_gate_clauses(
    _gate("||U.has(e)&&wle(n)||G.has(e)&&_4t(r)||Q.has(e)&&zz(r)")
    .replace(b"function _2(", b'var Q=new Set(["delivering_work_max"]);'
                              b'function zz(e){return Um(We(e),"sonnet_6_prompt_bundle",e)}'
                              b"function _2(")), "2.1.279")
check("drift: an UNKNOWN clause is a finding that names its capabilities",
      len(_new) == 1 and "NEW clause: sonnet_6_prompt_bundle" in _new[0]
      and "delivering_work_max" in _new[0], str(_new))

# A known clause that grew a capability is the same class of finding.
_grew = cc.gate_drift({"fable_5_1_prompt_bundle": frozenset(
    list(cc.MODEL_GATE_CLAUSES["fable_5_1_prompt_bundle"][0]) + ["brand_new_thing"]),
    "opus_5_prompt_bundle": frozenset(["bison_cairn", "larch_cistern"])}, "2.1.278")
check("drift: a known clause that GREW is reported with both lists",
      len(_grew) == 1 and "brand_new_thing" in _grew[0], str(_grew))

# ...and the asymmetry that keeps the fleet quiet: a machine on an older CC
# legitimately lacks a newer clause. macOS ran 2.1.270 while this table already
# described 2.1.278's second arm; that must not read as drift.
check("drift: an older binary missing a newer clause is NOT drift",
      cc.gate_drift(cc.model_gate_clauses(_270), "2.1.270") == [],
      str(cc.gate_drift(cc.model_gate_clauses(_270), "2.1.270")))
check("drift: with no version to compare, absence is still not drift",
      cc.gate_drift(cc.model_gate_clauses(_270)) == [])
check("drift: but a clause GONE from a build new enough to have it is reported",
      any("GONE" in line for line in
          cc.gate_drift(cc.model_gate_clauses(_270), "2.1.278")),
      str(cc.gate_drift(cc.model_gate_clauses(_270), "2.1.278")))

# The shape this whole mechanism is blind to if it ever stops parsing: silence.
# An unrecognised gate must be a finding, never an empty pass.
check("drift: a gate that does not parse is a finding, not a pass",
      cc.gate_drift({}, "2.1.278")
      and "reshaped" in cc.gate_drift({}, "2.1.278")[0])
check("drift: a clause whose model predicate cannot be resolved is reported",
      any("unresolved predicate" in line
          for line in cc.gate_drift({None: frozenset(["turn_updates"])}, "2.1.278")))

# --- the binary mask (item 31) -----------------------------------------------
#
# `fable-silent-turn-reminder-model-cut` rewrites Fable's clause set minus the
# reminder. What the table reads off a binary then has four states, and the
# tripwire must tell them apart: the interim one (between pull and apply) must
# not tell the operator to delete the env line that is still doing the work.

_278_cut = _278.replace(b'"silent_turn_reminder",', b"")
_rows = lambda env, blob: {c: v for c, _var, _w, v in cc.model_gates(env, blob=blob)[0]}  # noqa: E731
check("binary: an unpatched clause with no env line is UNMASKED",
      _rows(_masked, _278)["silent_turn_reminder"] == "UNMASKED")
check("binary: the patched clause with no env line is masked",
      _rows(_masked, _278_cut)["silent_turn_reminder"] == "masked")
check("binary: patched AND the env line is ENV OVERRIDE (it masks the server's arms too)",
      _rows(_both_lines, _278_cut)["silent_turn_reminder"] == "ENV OVERRIDE")
check("binary: unpatched with the env line is the interim 'masked by env', not an override",
      _rows(_both_lines, _278)["silent_turn_reminder"] == "masked by env")
check("binary: a binary with no Fable clause at all is UNVERIFIED, and the tripwire says so",
      _rows(_masked, b"no gate here")["silent_turn_reminder"] == "UNVERIFIED"
      and ("silent_turn_reminder", "CLAUDE_CODE_SILENT_TURN_REMINDER", "UNVERIFIED")
      in cc.model_gates_unmasked(_masked, blob=b"no gate here"))
check("binary: the tripwire is silent on the finished state",
      cc.model_gates_unmasked(_masked, blob=_278_cut) == [])
check("drift: our own cut of the Fable clause is not drift",
      cc.gate_drift(cc.model_gate_clauses(_278_cut), "2.1.278") == [],
      str(cc.gate_drift(cc.model_gate_clauses(_278_cut), "2.1.278")))
check("LIVE_ROUTING: a patched clause still counts as per-model routing",
      cc.LIVE_ROUTING["model-keyed capability clause"][2](_278_cut))


def _cut(blob):
    return next(p for p in cc.plan_adhocs(blob) if p["name"] == "silent-turn-reminder-model-cut")


_item = _cut(_278)
check("adhoc: derives the cut from the gate's own clause",
      _item["status"] == "AUTO" and _item["old"] == _FABLE_SET.encode()[4:]
      and _item["new"] == b'U=new Set(["turn_updates","bash_output_audience_note",'
                          b'"thinking_display_updates"])', str(_item))
check("adhoc: works on the 2.1.270 shape (one clause, the macOS build)", _cut(_270)["status"] == "AUTO")
# A one-letter minified name is defined in many modules. The set the gate
# reads is the nearest one BEFORE the gate, not the first in the bundle.
_decoy = b'var U=new Set(["silent_turn_reminder","other"]);' + b"x" * 5000 + _278
check("adhoc: an earlier module's `U` is not the gate's",
      _cut(_decoy)["old"] == _FABLE_SET.encode()[4:], str(_cut(_decoy).get("old")))
check("adhoc: a clause without the reminder is NOOP",
      _cut(_278_cut)["status"] == "NOOP")
check("adhoc: no gate at all is MANUAL, never a silent NOOP (the env line is gone)",
      _cut(b"no gate here")["status"] == "MANUAL")

# 2.1.280: the cut follows the CAPABILITY, not the model that carried it first.
# Fable's clause and Opus 5.5's both lose the reminder in one span, the Opus 5
# clause between them is carried through untouched, and `quizzical_shore` —
# which is not prompt text — stays.
_280_item = _cut(_280)
check("adhoc: cuts the reminder out of EVERY model clause, in one span",
      _280_item["status"] == "AUTO"
      and _280_item["new"] == b'U=new Set(["turn_updates","bash_output_audience_note",'
                              b'"thinking_display_updates"]),'
                              b'G=new Set(["bison_cairn","larch_cistern"]),'
                              b'V=new Set(["quizzical_shore"])', str(_280_item))
check("adhoc: the span stops at the chain, not at the statement",
      b"Y=64" not in _280_item["old"] and not _280_item["old"].startswith(b"var "),
      str(_280_item["old"]))
check("adhoc: the patched 2.1.280 gate is not drift, and the tripwire is silent",
      cc.gate_drift(cc.model_gate_clauses(
          _280.replace(_280_item["old"], _280_item["new"])), "2.1.280") == []
      and cc.model_gates_unmasked(
          _masked, blob=_280.replace(_280_item["old"], _280_item["new"])) == [])
# If upstream ever separates the sets, swallowing whatever lands between them
# would be the worst possible repair. Stopping the apply is the right answer.
_280_split = _280.replace(b'G=new Set(["bison_cairn","larch_cistern"]),',
                          b'G=new Set(["bison_cairn","larch_cistern"]);var Zq=nope(),')
check("adhoc: sets that are no longer one chain are MANUAL, not a wide span",
      _cut(_280_split)["status"] == "MANUAL"
      and "one comma chain" in _cut(_280_split)["reason"], str(_cut(_280_split)))
check("adhoc: the cut still covers both clauses when a predicate's chunk is far away",
      _cut(_split)["old"] == _280_item["old"], str(_cut(_split).get("old")))
check("adhoc: an unreadable clause that arms the reminder is MANUAL, never a partial cut",
      _cut(_opaque)["status"] == "MANUAL"
      and "cannot resolve" in _cut(_opaque)["reason"], str(_cut(_opaque)))

# The speed-up that brought the tripwire under its 10 s hook timeout must not
# change a single match.
_sample = (b'a("context_management",()=>b),xx("context_management",()=>yy),'
           b'q.r("context_management",()=>z1),$w("context_management",()=>k)')
_pat = rb'([\w$]+)\("context_management",\(\)=>([\w$]+)\),'
check("ident_led: same matches as re.finditer",
      [m.group(0) for m in cc.ident_led(_pat, _sample, b'("context_management",()=>')]
      == [m.group(0) for m in __import__("re").finditer(_pat, _sample)])

check("MODEL_GATE_SET: still names the Fable clause's capabilities (LIVE_ROUTING)",
      all(f'"{cap}"' in cc.MODEL_GATE_SET
          for cap in cc.MODEL_GATE_CLAUSES["fable_5_1_prompt_bundle"][0]))
check("MODEL_GATE_CLAUSES: every capability we mask is in a clause",
      all(any(cap in caps for caps, _since in cc.MODEL_GATE_CLAUSES.values())
          for cap in cc.MODEL_GATES))


# --- the live read-back (`status --live`) ---------------------------------
#
# The expensive check. Nothing here spends a turn: the two rules the operator
# set are about WHEN it may spend, and both are decisions made before any
# process is launched, so both are testable offline.

# Rule 2, the one that guards the money: no per-model routing in this binary
# means no turn, full stop.
_no_routing = {k: (v[0], v[1], lambda blob: False) for k, v in cc.LIVE_ROUTING.items()}
_real_routing = dict(cc.LIVE_ROUTING)
try:
    cc.LIVE_ROUTING = _no_routing
    cc.live_blob = lambda binary: b""
    check("live: no routing identified means an empty plan and nothing spent",
          cc.live_plan("x", {"repoPath": "."}) == ([], []))
finally:
    cc.LIVE_ROUTING = _real_routing

# Rule 1, the one that keeps a turn from becoming four: the two communication
# fragments are alternative replacements of ONE section, so they are one
# question, and a model spelled two ways is one model.
_found = [("a", "w", ("system-prompt-outcome-first-communication-style",
                      "system-prompt-communication-style")),
          ("b", "w", ("system-prompt-outcome-first-communication-style",))]
_qs = cc.live_questions({"repoPath": "."}, _found)
check("live: both communication fragments collapse into ONE question",
      len(_qs) == 1 and len(_qs[0][1]) == 2, str(_qs))
check("live: the prompt asks each question exactly once",
      cc.live_prompt(_qs).count("first sentence") == 1, cc.live_prompt(_qs))

# Scoring: the fragments are alternatives, so quoting EITHER is a pass; and a
# model that re-wraps or re-punctuates its quote has still quoted it.
_exp = [("t1", "Your text output is what they read; the thinking usually is not.", "e1"),
        ("t2", "Text between tool calls may never be shown to them at all.", "e2")]
check("live: quoting the first alternative passes and names it",
      cc.live_score("Your text output is what they read; the thinking usually "
                    "is not.", _exp) == ("PASS", "t1"))
check("live: quoting the OTHER alternative also passes",
      cc.live_score("Text between tool calls may never be shown to them at all.",
                    _exp)[0] == "PASS")
check("live: re-punctuated and re-wrapped quotes still pass",
      cc.live_score("your text output is what they read -- the thinking\n"
                    "usually is not", _exp)[0] == "PASS")
# The failure this whole mechanism exists to catch: the stock line, quoted
# faithfully, by a model whose binary passes 15/15. The fixture is a synthetic
# line of the same kind, so the published test does not carry the stock one.
check("live: the stock narrate-as-you-go line FAILS",
      cc.live_score("Before any work, a synthetic narrate-first line announces the "
                    "plan and promises updates.", _exp)[0] == "FAIL")
check("live: an empty answer is NO ANSWER, not a pass",
      cc.live_score("", _exp)[0] == "NO ANSWER")

# The instrument's own blind spot, found the first time it ran (2026-09-15):
# a one-turn `-p` session does not carry every tool an interactive one does, so
# the model truthfully answered ABSENT for the TodoWrite description. Scoring
# that as FAIL would have manufactured a finding against a fragment that is
# fine. ABSENT is the session failing to be asked, not the fragment failing to
# arrive, and it must never reach the exit code.
check("live: ABSENT is inconclusive, distinct from both PASS and FAIL",
      cc.live_score("ABSENT", _exp)[0] == "ABSENT")
check("live: a wrong quote is still a FAIL, so ABSENT is not a blanket excuse",
      cc.live_score("A synthetic narrate-first line, quoted wrong.",
                    _exp)[0] == "FAIL")
_mixed = {"liveReadback": {"ccVersion": "2.1.270", "when": "2026-09-15T16:00:00+02:00",
                           "results": [{"model": "claude-fable-5-1",
                                        "targets": {"q1": "PASS", "q2": "ABSENT"}}]}}
check("live_summary: an unasked question does not make the model a failure",
      "1/1" in cc.live_summary("2.1.270", _mixed)
      and "did NOT" not in cc.live_summary("2.1.270", _mixed),
      cc.live_summary("2.1.270", _mixed))
check("live_summary: but it does say how many went unasked",
      "1 question(s) unasked" in cc.live_summary("2.1.270", _mixed),
      cc.live_summary("2.1.270", _mixed))

# Model resolution: settings.json spellings are not catalogue ids.
_cat = {"claude-opus-5": {"lean_prompt"}, "claude-fable-5-1": {"fable_5_1_prompt_bundle"},
        "claude-haiku-4-5": set()}
check("live: 'opus[1m]' resolves to the catalogue id, context variant stripped",
      cc.resolve_model("opus[1m]", _cat) == "claude-opus-5")
check("live: a bare family alias resolves to the newest member",
      cc.resolve_model("opus", _cat) == "claude-opus-5")
check("live: an unknown model resolves to nothing rather than to a guess",
      cc.resolve_model("gpt-4", _cat) is None)

# The free line `status` prints afterwards, so a spent turn is not spent twice.
check("live_summary: says so plainly when no turn has ever been spent",
      "never run" in cc.live_summary("2.1.270", {}))
_rec = {"liveReadback": {"ccVersion": "2.1.270", "when": "2026-09-15T16:00:00+02:00",
                         "results": [{"model": "claude-fable-5-1", "targets": {"q": "PASS"}}]}}
check("live_summary: reports a pass against the version it was measured on",
      "1/1" in cc.live_summary("2.1.270", _rec), cc.live_summary("2.1.270", _rec))
check("live_summary: a version bump makes the proof STALE, not reusable",
      "STALE" in cc.live_summary("2.1.271", _rec), cc.live_summary("2.1.271", _rec))
_bad = {"liveReadback": {**_rec["liveReadback"],
                         "results": [{"model": "claude-fable-5-1", "targets": {"q": "FAIL"}}]}}
check("live_summary: names the model that did not quote our text back",
      "did NOT" in cc.live_summary("2.1.270", _bad), cc.live_summary("2.1.270", _bad))

print()
print(f"{'FAILED: ' + ', '.join(fails) if fails else 'all model-gate checks pass'}")
sys.exit(1 if fails else 0)
