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

# --- the binary mask: the reminder switched off at its consumer (2026-09-29) --
#
# Item 31 cut the reminder out of the model clauses and let server arms through.
# 2.1.284 armed it from Sonnet 5.5's catalogue entry and from client data, and
# the operator narrowed the arm ruling: an arm that conflicts with an
# established patch is masked. So the cut moved to the capability's one
# consumer, and every route — env, clause, entry, arm — is behind it.

_PRED = ('function oVt(e){let n=Ue(e);return _2("silent_turn_reminder",n,e,'
         'a.CLAUDE_CODE_SILENT_TURN_REMINDER)}').encode("latin-1")
_PRED_OFF = _PRED.replace(b"return _2(", b"return!1&&_2(")
_rows = lambda env, blob: {c: v for c, _var, _w, v in cc.model_gates(env, blob=blob)[0]}  # noqa: E731


def _off(blob):
    return next(p for p in cc.plan_adhocs(blob) if p["name"] == "silent-turn-reminder-off")


_both_lines = {**_masked, "CLAUDE_CODE_SILENT_TURN_REMINDER": "0"}
check("binary: the stock predicate with no env line is UNMASKED",
      _rows(_masked, _278 + _PRED)["silent_turn_reminder"] == "UNMASKED")
check("binary: the cut predicate is masked, whatever the clauses say",
      _rows(_masked, _278 + _PRED_OFF)["silent_turn_reminder"] == "masked")
check("binary: cut AND the env line is simply masked (the line is redundant, not harmful)",
      _rows(_both_lines, _278 + _PRED_OFF)["silent_turn_reminder"] == "masked")
check("binary: cut beats an env line that would force it on",
      _rows({**_masked, "CLAUDE_CODE_SILENT_TURN_REMINDER": "1"},
            _278 + _PRED_OFF)["silent_turn_reminder"] == "masked")
check("binary: uncut with the env line is the interim 'masked by env'",
      _rows(_both_lines, _278 + _PRED)["silent_turn_reminder"] == "masked by env")
check("binary: a binary with no reminder predicate is UNVERIFIED, and the tripwire says so",
      _rows(_masked, b"no gate here")["silent_turn_reminder"] == "UNVERIFIED"
      and ("silent_turn_reminder", "CLAUDE_CODE_SILENT_TURN_REMINDER", "UNVERIFIED")
      in cc.model_gates_unmasked(_masked, blob=b"no gate here"))
check("binary: the tripwire is silent on the finished state",
      cc.model_gates_unmasked(_masked, blob=_278 + _PRED_OFF) == [])

_off_item = _off(_280 + _PRED)
check("adhoc: switches the predicate off with `return!1&&`, nothing else",
      _off_item["status"] == "AUTO" and _off_item["old"] == _PRED[_PRED.index(b"return "):]
      and _off_item["new"] == _PRED_OFF[_PRED_OFF.index(b"return!1"):], str(_off_item))
check("adhoc: an already-cut predicate is NOOP", _off(_280 + _PRED_OFF)["status"] == "NOOP")
check("adhoc: no predicate at all is MANUAL, never a silent NOOP",
      _off(_280)["status"] == "MANUAL")
check("adhoc: two consumers are MANUAL, not a guess",
      _off(_280 + _PRED + b";" + _PRED.replace(b"oVt", b"xVt"))["status"] == "MANUAL")

# A binary patched by the retired clause cut is still not drift.
_280_cut = _280.replace(b'"silent_turn_reminder","thinking_display_updates"',
                        b'"thinking_display_updates"').replace(
    b'V=new Set(["silent_turn_reminder","quizzical_shore"])', b'V=new Set(["quizzical_shore"])')
check("drift: a gate patched by the retired clause cut is not drift",
      cc.gate_drift(cc.model_gate_clauses(_280_cut), "2.1.280") == [],
      str(cc.gate_drift(cc.model_gate_clauses(_280_cut), "2.1.280")))
check("LIVE_ROUTING: a patched clause still counts as per-model routing",
      cc.LIVE_ROUTING["model-keyed capability clause"][2](_280_cut))

# CC 2.1.283: the same three sets and predicates, but the disjunction is
# computed into a local so client data can turn a model default OFF. The old
# `if(...)return!0;` pattern then matched only the inner client-data test,
# which names no set, and the parse read "no model clause". The statement
# changed; the meaning of a set member did not.
_283_GATE = ("function _2(e,n,r,l){if(l!==void 0)return l;let s=Um(n,e,r);"
             "if(s!==void 0)return s;"
             "let p=U.has(e)&&wle(n)||G.has(e)&&_4t(r)||V.has(e)&&gg(n),u=aRn(r);"
             "if(p){if(u?.data?.[e]!==!1)return!0;return y(O,e,u),!1}"
             "if(u?.data?.[e]!==!0)return!1;return y(W,e,u),!0}")
_283 = (_280_CHAIN + ";" + _280_PREDS + _283_GATE).encode("latin-1")
check("clauses: the 2.1.283 gate (clause in a local) parses to the same three arms",
      cc.model_gate_clauses(_283) == cc.model_gate_clauses(_280),
      str(cc.model_gate_clauses(_283)))
check("drift: 2.1.283's gate matches the table",
      cc.gate_drift(cc.model_gate_clauses(_283), "2.1.283") == [],
      str(cc.gate_drift(cc.model_gate_clauses(_283), "2.1.283")))
# A local that is NOT returned as true is not a model clause, whatever it holds.
_283_unused = _283.replace(b"if(p){if(u?.data?.[e]!==!1)return!0;", b"if(q){if(u?.data?.[e]!==!1)return!0;")
check("clauses: a local the gate never returns on is not read as a model clause",
      cc.model_gate_clauses(_283_unused) == {}, str(cc.model_gate_clauses(_283_unused)))

# CC 2.1.284: Sonnet 5.5's own catalogue entry lists `silent_turn_reminder`,
# and the gate's first rung returns true for a capability the entry lists,
# before any clause. The clause parse cannot see that; the catalogue parse is
# its detector. These fixtures are the entry shape as the bundle spells it.
_CAT_SONNET55 = ('{id:"claude-sonnet-5-5",family:"sonnet",display_name:"Sonnet 5.5",'
                 'pricing:"tier_2_10",capabilities:["effort","max_effort","lean_prompt",'
                 '"refusal_fallback","silent_turn_reminder","org_locked_thinking"],'
                 'default_effort:"medium",advisor_rank:3}')
_CAT_OPUS55 = ('{id:"claude-opus-5-5",family:"opus",capabilities:["effort","lean_prompt",'
               '"opus_5_5_prompt_bundle"],advisor_rank:4}')
_CATALOGUE = ("var x8n={models:[" + _CAT_OPUS55 + "," + _CAT_SONNET55 + "]};").encode("latin-1")
_284 = _CATALOGUE + _283 + _PRED

check("catalogue: the 2.1.284 entry is read as a gate default of Sonnet 5.5 only",
      cc.catalogue_gate_defaults(_284) == {"claude-sonnet-5-5": frozenset(["silent_turn_reminder"])},
      str(cc.catalogue_gate_defaults(_284)))
check("catalogue: 2.1.284 as shipped is described by the table (no drift)",
      cc.gate_drift(cc.model_gate_clauses(_284), "2.1.284", cc.catalogue_gate_defaults(_284)) == [],
      str(cc.gate_drift(cc.model_gate_clauses(_284), "2.1.284", cc.catalogue_gate_defaults(_284))))
_284_done = _284.replace(_off(_284)["old"], _off(_284)["new"])
check("catalogue: 2.1.284 with the predicate cut is masked, not drift, tripwire silent",
      _rows(_masked, _284_done)["silent_turn_reminder"] == "masked"
      and cc.gate_drift(cc.model_gate_clauses(_284_done), "2.1.284",
                        cc.catalogue_gate_defaults(_284_done)) == []
      and cc.model_gates_unmasked(_masked, blob=_284_done) == [])
_284_old_cut = _284.replace(b'"refusal_fallback","silent_turn_reminder"', b'"refusal_fallback"')
check("catalogue: an entry patched by the retired catalogue cut is not drift",
      cc.gate_drift(cc.model_gate_clauses(_284_old_cut), "2.1.284",
                    cc.catalogue_gate_defaults(_284_old_cut)) == [])
# A model the table has never heard of arming a gate capability from its own
# entry is the new-decider finding, exactly as an unknown clause is.
_cat_new = cc.catalogue_drift({"claude-haiku-6": frozenset(["turn_updates"])}, "2.1.290")
check("catalogue drift: an unknown entry arming a gate capability is NEW",
      len(_cat_new) == 1 and "NEW catalogue default: claude-haiku-6" in _cat_new[0], str(_cat_new))
check("catalogue drift: plain model features are never gate capabilities",
      "lean_prompt" not in cc.GATED_CAPABILITIES and "effort" not in cc.GATED_CAPABILITIES)

# The narrowed arm rule (2026-09-29): an arm that conflicts with a patch is not
# accepted, so an env line overriding it is not something to warn about.
check("arm_conflicts: the reminder and a displacing client-data key conflict",
      cc.arm_conflicts("silent_turn_reminder") and cc.arm_conflicts("tengu_cozy_teapot"))
check("arm_conflicts: a consonant or inert key does not",
      not cc.arm_conflicts("tengu_thrifty_sonic") and not cc.arm_conflicts("per_turn_effort")
      and not cc.arm_conflicts("bash_output_audience_note"))
_arm_rec = {"deliveredCapture": {"ccVersion": "2.1.284", "when": "2026-09-29T15:00:00+02:00",
                                 "results": [{"shape": "sdk", "model": "sonnet",
                                              "verdicts": {"x": "DISPLACED"},
                                              "arms": {"x": "tengu_cozy_teapot=\"relaxed\""}}]}}
check("delivered_summary: a target displaced by a server arm is a failure, not accepted",
      "NOT: sdk/sonnet" in cc.delivered_summary("2.1.284", _arm_rec)
      and "accepted" not in cc.delivered_summary("2.1.284", _arm_rec),
      cc.delivered_summary("2.1.284", _arm_rec))

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
          for cap, (_var, _what, how) in cc.MODEL_GATES.items() if how is not None))
# 2.1.293 added gate capabilities that no clause carries: one from Haiku 5.5's
# own catalogue entry, one only a server arm can turn on. Both are left on, and
# both must still be gate capabilities, or catalogue_drift and flags go blind.
check("MODEL_GATES: 2.1.293's catalogue and server-only capabilities are tracked",
      {"haiku_5_5_early_stopping_guidance", "elapsed_time_reminder"} <= cc.GATED_CAPABILITIES
      and cc.CATALOGUE_GATE_DEFAULTS["claude-haiku-5-5"][0] == ("haiku_5_5_early_stopping_guidance",))
check("catalogue_drift: Haiku 5.5's own entry is described, not drift",
      cc.catalogue_drift({"claude-haiku-5-5": frozenset({"haiku_5_5_early_stopping_guidance"})},
                         "2.1.294") == [])


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
