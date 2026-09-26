"""Tests for the delivered-prompt capture (ccctl `status --delivered`) and the
client-data channel it surfaced.

Run from anywhere:  python tools/tests/test_ccctl_delivered.py
No real CC binary runs: the capture plumbing is driven by a stand-in script
that POSTs to ANTHROPIC_BASE_URL the way CC does, and the scoring by request
bodies built here.

Why this file exists (TODO 30, notes/2026-09-21-length-regime.md):

  * Every earlier check read a FILE — the binary, settings.json — and the
    paid `--live` read-back asked one sentence through `claude -p`. None could
    say what a model is handed under a given harness. The capture reads the
    request itself, for free.
  * Its first run found a displacement no other check could see: on Opus 5
    under an SDK host the bypass-mode shell block carried upstream's "relaxed"
    text, chosen by per-(model, entrypoint) client data, while the GrowthBook
    cache said "strict". The verdicts below pin the classes that let a reader
    tell that apart from a stale binary and from a slot the session never has.
"""
import atexit
import shutil
import importlib.util
import json
import os
import sys
import tempfile
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


def request(system="", messages=(), tools=None):
    return {"system": [{"type": "text", "text": system}],
            "messages": [{"role": r, "content": [{"type": "text", "text": t}]} for r, t in messages],
            "tools": [{"name": n, "description": d} for n, d in (tools or {}).items()]}


OWNERS = {
    "The longer you work": "disposition-floor-under-every-output-style",
    "Work that went right is not news": "disposition-floor-under-every-output-style",
    "only this text lands": "system-prompt-outcome-first-communication-style",
    "Thinking is a workspace": "system-prompt-communication-style",
    "the artifact, not the account": "system-prompt-delivering-work-at-full-scope",
    "fix it silently and continue": "system-prompt-correction-restraint",
    "a list of one item": "tool-description-todowrite",
    "batch work when one invocation": "bypass-auto-shell-block-invert",
}
FULL_SYSTEM = ("# How to report back\nThe longer you work ... Work that went right is not news.\n"
               "only this text lands\nthe artifact, not the account\nfix it silently and continue")


def verdicts(req, snapshot=None):
    return {t: v for t, v, _d in cc.delivered_verdicts(req, OWNERS, snapshot)}


# --- the scoring -----------------------------------------------------------

v = verdicts(request(FULL_SYSTEM))
check("delivered: every marker present -> DELIVERED",
      v["disposition-floor-under-every-output-style"] == "DELIVERED"
      and v["system-prompt-delivering-work-at-full-scope"] == "DELIVERED", str(v))
check("delivered: the sibling fragment of the same section is ALTERNATIVE, not missing",
      v["system-prompt-communication-style"] == "ALTERNATIVE", str(v))
check("delivered: no TodoWrite tool -> NOT IN SESSION (a fact about the session)",
      v["tool-description-todowrite"] == "NOT IN SESSION", str(v))
check("delivered: no bypass/auto block -> NOT IN SESSION",
      v["bypass-auto-shell-block-invert"] == "NOT IN SESSION", str(v))

# A binary built before the repo last changed the floor carries the old floor:
# some markers, not all. That is the state a machine is in between `pull` and
# `apply`, and it must not read as DELIVERED.
old_floor = FULL_SYSTEM.replace(" Work that went right is not news.", "")
v = verdicts(request(old_floor))
check("delivered: an older version of our text is PARTIAL",
      v["disposition-floor-under-every-output-style"] == "PARTIAL", str(v))

v = verdicts(request(FULL_SYSTEM.replace("fix it silently and continue", "")))
check("delivered: a section that must be everywhere and is not is MISSING",
      v["system-prompt-correction-restraint"] == "MISSING", str(v))

relaxed = ("While bypass permissions mode is active:\n\nA synthetic relaxed variant routes "
           "work through the Bash tool")
v = verdicts(request(FULL_SYSTEM, [("user", relaxed)]))
check("delivered: the shell block present with other text is DISPLACED",
      v["bypass-auto-shell-block-invert"] == "DISPLACED", str(v))
ours = "While bypass permissions mode is active:\n\nUse the Bash tool to batch work when one invocation"
v = verdicts(request(FULL_SYSTEM, [("user", ours)]))
check("delivered: the shell block with our sentence is DELIVERED in a message",
      v["bypass-auto-shell-block-invert"] == "DELIVERED", str(v))

stock_piece = "Synthetic stand-in for the stock tool description, one piece long enough to be found"
snap = {"tool-description-todowrite": {"pieces": [stock_piece]}}
v = verdicts(request(FULL_SYSTEM, tools={"TodoWrite": stock_piece}), snap)
check("delivered: upstream's own text in the slot is DISPLACED",
      v["tool-description-todowrite"] == "DISPLACED", str(v))

# The two sentence rewrites of the 2026-09-25 audit: the stock clause in a
# request means upstream's text holds the slot; neither means the slot is not
# in this session (the audience note only fires after truncated Bash output).
bn_owner = {"not what it printed": "bash-audience-note-result-not-output"}
# A synthetic last sentence and its own digest stand in for Anthropic's.
_bn_syn = "If the user needs this synthetic tail, it stands in here."
_real_bn_tail = cc.BASH_NOTE_TAIL
cc.BASH_NOTE_TAIL = cc.StockSentence(b"If the user needs", len(_bn_syn.encode()),
                                     __import__("hashlib").sha256(_bn_syn.encode()).hexdigest())
cc.ADHOC_STOCK_CLAUSES["bash-audience-note-result-not-output"] = (
    cc.BASH_NOTE_TAIL, cc.ADHOC_STOCK_CLAUSES["bash-audience-note-result-not-output"][1])
bn = {t: v for t, v, _d in cc.delivered_verdicts(
    request(FULL_SYSTEM, [("user", "Only you see that command's output. " + _bn_syn)]),
    bn_owner)}
check("delivered: the stock Bash audience sentence in a message is DISPLACED",
      bn["bash-audience-note-result-not-output"] == "DISPLACED", str(bn))
bn = {t: v for t, v, _d in cc.delivered_verdicts(request(FULL_SYSTEM), bn_owner)}
check("delivered: no audience note at all is NOT IN SESSION",
      bn["bash-audience-note-result-not-output"] == "NOT IN SESSION", str(bn))
ac_owner = {"say so and where the output is": "action-caution-outcome-line"}
ac = {t: v for t, v, _d in cc.delivered_verdicts(
    request(FULL_SYSTEM + "\nReport outcomes faithfully: " + cc.ACTION_CAUTION_STOCK.decode()), ac_owner)}
check("delivered: the stock action_caution clause in the system prompt is DISPLACED",
      ac["action-caution-outcome-line"] == "DISPLACED", str(ac))

system, messages, tools = cc.request_texts(request("S", [("system", "# Output Style: Proactive\nbody")],
                                                   {"Agent": "d"}))
check("request_texts: system, messages and tools come apart",
      system == "S" and "Output Style" in messages and tools == {"Agent": "d"})
check("delivered_style: the style arrives by name in a mid-conversation system message",
      cc.delivered_style(messages) == "Proactive" and cc.delivered_style("nothing") is None)

# --- the client-data channel ------------------------------------------------

config = {"clientDataCacheSlots": {
    "a": {"model": "claude-opus-5", "entrypoint": "sdk-ts", "at": 1,
          "data": {"tengu_cozy_teapot": "strict"}},
    "b": {"model": "claude-opus-5", "entrypoint": "sdk-ts", "at": 2,
          "data": {"experimentKey": "x", "tengu_cozy_teapot": "relaxed", "atis": "v1.token"}},
    "c": {"model": "claude-opus-5", "entrypoint": "cli", "at": 3, "data": {}}}}
slots = cc.client_data_slots(config)
check("client_data_slots: the newest slot per (model, entrypoint) wins",
      slots[("claude-opus-5", "sdk-ts")][0].get("tengu_cozy_teapot") == "relaxed"
      and ("claude-opus-5", "cli") in slots, str(slots))
check("client_data_armed: cozy_teapot is an arm only when 'relaxed'",
      cc.client_data_armed("tengu_cozy_teapot", "relaxed")
      and not cc.client_data_armed("tengu_cozy_teapot", "strict"))
check("client_data_armed: an empty toasty_thimble map entry turns the reminder OFF",
      not cc.client_data_armed("tengu_toasty_thimble", {"claude-fable-5-1": ""}))
rows = {k for k, *_ in cc.client_data_rows({"atis": "t", "experimentKey": "x", "brand_new_key": 1})}
check("client_data_rows: inert keys are dropped, an unknown key is shown",
      rows == {"brand_new_key"}, str(rows))
data = slots[("claude-opus-5", "sdk-ts")][0]
check("attribution: a displaced shell block is explained by a relaxed cozy_teapot",
      cc.delivered_attribution("bypass-auto-shell-block-invert", data, {}) is not None)
check("attribution: an env override means the arm cannot be the explanation",
      cc.delivered_attribution("bypass-auto-shell-block-invert", data,
                               {"CLAUDE_CODE_COZY_TEAPOT": "strict"}) is None)

# --- the capture plumbing ---------------------------------------------------

check("capture_env: the caller's own CC identity does not leak into the capture",
      not any(k.startswith(("CLAUDE", "ANTHROPIC")) for k in cc.capture_env({}))
      and cc.capture_env({"X": "1"})["X"] == "1")
check("cc_project_dir: CC's sanitising rule",
      cc.cc_project_dir("/tmp/ccctl-capture-fx88s_tt").name == "-tmp-ccctl-capture-fx88s-tt")

# A stand-in for CC's main loop: one side call, then the session's own
# requests. Each reply is parsed the way CC reads it; a tool call is answered
# with a tool result and the loop goes on, text ends it. With FAKE_NAG set it
# does what CC does after five silent turns: a mid-conversation system message.
# It is launched as [python, script] rather than by shebang so the same test
# runs on win32, where a script is not an executable.
scratch = Path(tempfile.mkdtemp(prefix="ccctl-delivered-test-"))
atexit.register(shutil.rmtree, scratch, True)
fake_script = scratch / "fake-claude.py"
fake_script.write_text(f"""import json, os, sys, urllib.request
base = os.environ["ANTHROPIC_BASE_URL"]
assert "CLAUDECODE" not in os.environ
nag = os.environ.get("FAKE_NAG")
def post(body):
    req = urllib.request.Request(base + "/v1/messages?beta=true", json.dumps(body).encode(),
                                 {{"content-type": "application/json"}})
    try:
        with urllib.request.urlopen(req) as r:
            return r.read().decode()
    except urllib.error.HTTPError:
        return None
post({{"model": "m", "messages": []}})
messages = [{{"role": "user", "content": "hi"}}]
for turn in range(20):
    reply = post({{"model": "m", "stream": True, "system": [{{"type": "text", "text": "x" * 500}}],
                  "tools": [{{"name": "Read", "description": "reads"}}], "messages": messages,
                  "argv": sys.argv[1:], "entry": os.environ.get("CLAUDE_CODE_ENTRYPOINT"),
                  "stdin": sys.stdin.read() if turn == 0 else ""}})
    if reply is None:
        sys.exit(1)
    events = [json.loads(l[6:]) for l in reply.splitlines() if l.startswith("data: ")]
    start = next(e for e in events if e["type"] == "content_block_start")["content_block"]
    if start["type"] != "tool_use":
        sys.exit(0)
    args = json.loads(next(e for e in events if e["type"] == "content_block_delta")["delta"]["partial_json"])
    messages += [{{"role": "assistant", "content": [dict(start, input=args)]}},
                 {{"role": "user", "content": [{{"type": "tool_result", "tool_use_id": start["id"],
                                                 "content": open(args["file_path"]).read()}}]}}]
    if nag and turn == 4:
        messages.append({{"role": "system", "content": "{cc.SILENT_REMINDER_OPENING}, synthetic tail."}})
""", encoding="utf-8")
fake = [sys.executable, str(fake_script)]
os.environ["CLAUDECODE"] = "1"                  # what an agent's own shell carries
before = set(Path(tempfile.gettempdir()).glob("ccctl-capture-*"))
reqs, err = cc.capture_request("claude-x", "sdk", binary=fake, timeout=30)
req = reqs[0] if reqs else None
check("capture_request: the session's request is returned, the side call is not",
      err is None and len(reqs) == 1 and len(req["system"][0]["text"]) == 500,
      f"{err} {len(reqs)}")
check("capture_request: the sdk shape runs as sdk-ts with the host's flags and a user message",
      req and req["entry"] == "sdk-ts" and "--append-system-prompt" in req["argv"]
      and '"type": "user"' in req["stdin"], str(req and (req["entry"], req["argv"][:4])))
reqs, err = cc.capture_request("claude-x", "print", binary=fake, timeout=30)
req = reqs[0] if reqs else None
check("capture_request: the print shape runs -p without a forced entrypoint",
      req and req["argv"][:2] == ["-p", "hi"] and req["entry"] is None, str(req and req["argv"]))
# The prompt must precede --allowedTools, which takes a LIST and swallowed a
# trailing prompt in a first draft ("Input must be provided ...").
argv = cc.DELIVERED_SHAPES["print"][2]("m", 7)
check("print shape: the prompt is not left where --allowedTools can swallow it",
      argv.index("hi") < argv.index("--allowedTools") and argv[argv.index("--max-turns") + 1] == "8",
      str(argv))

reqs, err = cc.capture_request("claude-x", "print", binary=fake, timeout=30, turns=7)
check("tool loop: 7 silent tool turns and a closing turn are 8 requests",
      err is None and len(reqs) == 8 and len(reqs[-1]["messages"]) == 15, f"{err} {len(reqs)}")
check("tool loop: every scripted call reads the capture's own probe file",
      bool(reqs) and all(Path(m["content"][0]["input"]["file_path"]).name == "probe.txt"
                         for m in reqs[-1]["messages"] if m["role"] == "assistant"))
check("tool loop: no reminder, no per-turn injection", cc.per_turn_injections(reqs) == [],
      str(cc.per_turn_injections(reqs)))
os.environ["FAKE_NAG"] = "1"
reqs, err = cc.capture_request("claude-x", "print", binary=fake, timeout=30, turns=7)
del os.environ["FAKE_NAG"]
inj = cc.per_turn_injections(reqs)
check("tool loop: a mid-conversation system message after five silent turns is seen, once",
      len(inj) == 1 and inj[0][0] == 6 and inj[0][1].startswith(cc.SILENT_REMINDER_OPENING), str(inj))
check("injected_texts: <system-reminder> blocks in a tool result count too",
      cc.injected_texts({"messages": [{"role": "user", "content": [
          {"type": "tool_result", "content": "data<system-reminder>\nbe careful\n</system-reminder>"}]}]})
      == ["be careful"])
left = set(Path(tempfile.gettempdir()).glob("ccctl-capture-*")) - before
check("capture_request: the capture cwd is gone afterwards", not left, str(left))

missing = scratch / "no-request.py"
missing.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
reqs, err = cc.capture_request("claude-x", "print", binary=[sys.executable, str(missing)], timeout=30)
check("capture_request: a run that sends nothing is an error, not an empty pass",
      reqs == [] and "exit 3" in (err or ""), str(err))
failed_record = {"deliveredCapture": {"ccVersion": "2.1.280", "when": "2026-09-23T22:00:00",
                                      "results": [{"shape": "print", "model": "claude-x",
                                                   "error": "OAuth session expired"}]}}
check("delivered summary: auth failure is unverified, not displaced prompt text",
      "capture failed" in cc.delivered_summary("2.1.280", failed_record)
      and "delivered prompt unverified" in cc.delivered_summary("2.1.280", failed_record)
      and "NOT:" not in cc.delivered_summary("2.1.280", failed_record))
failed_record["deliveredCapture"]["results"].append(
    {"shape": "sdk", "model": "claude-y", "verdicts": {"target": "MISSING"}})
check("delivered summary: a real missing fragment survives a separate capture failure",
      "NOT: sdk/claude-y" in cc.delivered_summary("2.1.280", failed_record))

# --- the CLI surface --------------------------------------------------------

args = cc.build_parser().parse_args(["status", "--delivered", "--shape", "sdk"])
check("parser: status --delivered --shape sdk", args.delivered and args.shape == "sdk")
args = cc.build_parser().parse_args(["status"])
check("parser: plain status does not capture", not args.delivered)

print()
print(f"{len(fails)} failure(s)" if fails else "all passed")
sys.exit(1 if fails else 0)
