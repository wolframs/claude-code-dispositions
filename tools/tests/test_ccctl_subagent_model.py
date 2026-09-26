"""Tests for the subagent model pin (ccctl `subagent_model_state` / summary).

Run from anywhere:  python tools/tests/test_ccctl_subagent_model.py
No real binary is read; env dicts and a fake alias table are passed in.

Why this file exists, and what it is guarding:

  * The operator runs Opus-tier subagents on purpose (2026-09-22): Sonnet
    confabulates over the large input sweeps an explorer takes in a complex
    project, so a cheaper default would be a false economy. The pin is
    therefore `CLAUDE_CODE_SUBAGENT_MODEL=opus`, and FORCE is deliberately NOT
    set so a caller can still name a model for one spawn.
  * The pin is an ALIAS, and the alias is resolved by the bundle's own table.
    A CC release can move what `opus` means with no marker moving, no fragment
    changing and nothing else in this repo noticing — the same failure shape as
    the model-gate clause, one layer further out. That is the whole reason for
    a per-upgrade question rather than a one-time setting.
  * The question is asked ONCE per real change. An unchanged resolution is a
    statement, not a prompt; only a moved one is an ASK. Re-asking a settled
    question is the thing the ask-once rule exists to stop.
  * `resolved: None` is its own finding: CC does not fail on a pin it cannot
    resolve, it silently falls back to the parent model — which looks exactly
    like no pin at all from the outside.
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


# ---------------------------------------------------------------- state
# subagent_model_state reads the binary only to resolve the alias, so the
# resolution is stubbed and the env handling tested on its own.
_TABLE = {"opus": "claude-opus-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5"}
cc.alias_table = lambda _binary: dict(_TABLE)
cc.catalogue_models = lambda _binary: {m: set() for m in _TABLE.values()}


def state(env):
    return cc.subagent_model_state("/nonexistent", env=env)


_pinned = state({"CLAUDE_CODE_SUBAGENT_MODEL": "opus"})
check("state: the alias resolves through the bundle's own table",
      _pinned["resolved"] == "claude-opus-5", _pinned)
check("state: FORCE is off unless it is actually set",
      _pinned["force"] is False)
check("state: no pin reads as no pin, not as an empty string",
      state({})["pin"] is None)
check("state: whitespace around the pin does not create a bogus model",
      state({"CLAUDE_CODE_SUBAGENT_MODEL": "  opus  "})["resolved"] == "claude-opus-5")
check("state: FORCE=0 is OFF — presence is not the test",
      state({"CLAUDE_CODE_SUBAGENT_MODEL": "opus",
             "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "0"})["force"] is False)
check("state: FORCE=1 is on",
      state({"CLAUDE_CODE_SUBAGENT_MODEL": "opus",
             "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"})["force"] is True)
check("state: a pin this build has no model for resolves to None, not to a guess",
      state({"CLAUDE_CODE_SUBAGENT_MODEL": "gpt-4"})["resolved"] is None)

# ---------------------------------------------------------------- summary
_line, _moved = cc.subagent_model_summary(_pinned, None)
check("summary: a first observation is not an ask",
      _moved is False, _line)
check("summary: names both the pin and what it resolves to",
      "opus" in _line and "claude-opus-5" in _line, _line)
check("summary: says a per-spawn model still wins when FORCE is off",
      "per-spawn model still wins" in _line, _line)

_same = {"resolved": "claude-opus-5"}
check("summary: an UNCHANGED resolution is a statement, never a re-ask",
      cc.subagent_model_summary(_pinned, _same)[1] is False)

_prev = {"resolved": "claude-opus-4-8"}
_line2, _moved2 = cc.subagent_model_summary(_pinned, _prev)
check("summary: a MOVED alias is the ask the operator asked for",
      _moved2 is True, _line2)
check("summary: a move names what it used to be, so the change is legible",
      "was claude-opus-4-8" in _line2, _line2)

_forced = state({"CLAUDE_CODE_SUBAGENT_MODEL": "opus",
                 "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"})
check("summary: FORCE is called out — it silently overrides a per-spawn model",
      "FORCE" in cc.subagent_model_summary(_forced, _same)[0].upper())

_unres = state({"CLAUDE_CODE_SUBAGENT_MODEL": "gpt-4"})
_line3, _moved3 = cc.subagent_model_summary(_unres, None)
check("summary: an unresolvable pin is an ask even on a first observation",
      _moved3 is True, _line3)
check("summary: an unresolvable pin says CC falls back rather than failing",
      "fall back" in _line3, _line3)

_none, _none_moved = cc.subagent_model_summary(state({}), None)
check("summary: no pin explains the default instead of reporting a fault",
      _none_moved is False and "inherits the parent" in _none, _none)
check("summary: no pin names the variable to set",
      cc.SUBAGENT_MODEL_VAR in _none, _none)

_inherit, _inherit_moved = cc.subagent_model_summary(
    state({"CLAUDE_CODE_SUBAGENT_MODEL": "inherit"}), None)
check("summary: an explicit `inherit` is a deliberate choice, not an ask",
      _inherit_moved is False, _inherit)

# ------------------------------------------------- the agents that ignore it
# Explore is one trap (its model field is never read, so tweakcc's own
# subagentModels patch is a silent no-op for it). Plan is the other.
# MEASURED 2026-09-22 with tools/probe_subagent_models.py, from a Fable parent:
#   Explore opus-5 / Plan fable / general-purpose fable->opus-5 with the pin.
# A first static reading had Plan inside the pin's reach; it is not, because an
# explicit model:"inherit" sits on the `frontmatter` rung ABOVE `env` while an
# ABSENT field falls through to it. That distinction is what these two guard.
check("reach: the pin covers exactly the agents with NO model field",
      cc.SUBAGENT_PIN_REACHES == ("general-purpose", "claude", "workflow-subagent"),
      cc.SUBAGENT_PIN_REACHES)
check("reach: Plan is OUTSIDE the pin — model:\"inherit\" outranks env",
      "Plan" in cc.SUBAGENT_PIN_IGNORED)
check("reach: Explore is outside the pin — its field is never read",
      "Explore" in cc.SUBAGENT_PIN_IGNORED)
check("reach: the two hard-pinned built-ins are recorded as outside it",
      {"statusline-setup", "claude-code-guide"} <= set(cc.SUBAGENT_PIN_IGNORED))
check("reach: no agent is claimed both reached and ignored",
      not (set(cc.SUBAGENT_PIN_REACHES) & set(cc.SUBAGENT_PIN_IGNORED)))

print()
print(f"{'FAILED: ' + ', '.join(fails) if fails else 'all subagent-model checks pass'}")
sys.exit(1 if fails else 0)
