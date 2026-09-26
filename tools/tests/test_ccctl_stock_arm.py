"""Tests for the stock arm — ccctl's B arm (`apply --stock` / `update --stock`).

Run from anywhere:  python tools/tests/test_ccctl_stock_arm.py
No real binaries are touched; a temp state dir and stubbed lookups only.

What is worth pinning here, and why:

  * Zero markers by intent and zero by accident are the SAME BYTES. Only the
    declaration in ccctl-state.json separates them, so every check that reads
    "is this binary correct" has to consult the arm first.
  * `status --check` must exit 3 in the arm — not 0 (the tranche is not live)
    and not 2 (an agent reading FAIL will re-apply and end the operator's
    experiment). The distinct code is a contract, not a detail.
  * tools/cc-doctor.sh is bash and parses the sentinel with a bare `read -r`,
    so the sentinel's shape is a contract too: one line, version first,
    whitespace-free fields up to the note.
"""
import atexit
import shutil
import argparse
import importlib.util
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


sandbox = Path(tempfile.mkdtemp(prefix="ccctl-stockarm-"))
atexit.register(shutil.rmtree, sandbox, True)
cc.STATE = sandbox / "ccctl-state.json"
cc.STOCK_SENTINEL = sandbox / "stock-arm"
cc.CHANGELOG = sandbox / "changelog.md"

# --- state round-trip -------------------------------------------------------
check("no arm declared on a fresh state dir", cc.stock_arm() is None)

arm = cc.set_stock_arm("2.1.259", "feeling out Anthropic's defaults")
check("set_stock_arm records version + note", cc.stock_arm()["ccVersion"] == "2.1.259"
      and cc.stock_arm()["note"] == "feeling out Anthropic's defaults", str(cc.stock_arm()))

# The one field `applied` must NOT absorb: the arm is a separate fact from the
# last tranche apply, and merging them makes `status` misreport history.
cc.save_json(cc.STATE, {**cc.load_json(cc.STATE, {}),
                        "applied": {"ccVersion": "2.1.257", "repoCommit": "abc1234"}})
check("arm leaves `applied` history alone",
      cc.load_json(cc.STATE, {})["applied"]["ccVersion"] == "2.1.257" and
      cc.stock_arm()["ccVersion"] == "2.1.259")

# --- the sentinel is derived, and its shape is cc-doctor.sh's contract ------
line = cc.STOCK_SENTINEL.read_text(encoding="utf-8")
check("sentinel is exactly one line", line.count("\n") == 1, repr(line))
fields = line.split()
check("sentinel field 1 is the version", fields[0] == "2.1.259", line)
check("sentinel field 2 is a whitespace-free timestamp",
      fields[1] == arm["when"] and " " not in arm["when"], line)
check("sentinel keeps the note after the two fixed fields",
      line.split(None, 2)[2].strip() == "feeling out Anthropic's defaults", line)

cc.STOCK_SENTINEL.unlink()
cc.sync_stock_sentinel(cc.stock_arm())
check("a hand-deleted sentinel heals from state", cc.STOCK_SENTINEL.exists())

ended = cc.clear_stock_arm()
check("clear_stock_arm returns the record it ended", ended and ended["ccVersion"] == "2.1.259")
check("clear_stock_arm drops the state key", cc.stock_arm() is None)
check("clear_stock_arm removes the sentinel", not cc.STOCK_SENTINEL.exists())
check("clearing an arm that is not set is a no-op", cc.clear_stock_arm() is None)

# --- assemble refuses to half-build an arm ---------------------------------
try:
    cc.assemble({}, "2.1.259", sandbox / "nope", [{"name": "x", "status": "AUTO"}], stock=True)
    check("assemble(stock=True) refuses a non-empty plan", False, "no SystemExit")
except SystemExit:
    check("assemble(stock=True) refuses a non-empty plan", True)

# --- status: the arm decides what "correct" means --------------------------
CFG = {"repoPath": ".", "tweakccPatches": True, "applyEngine": "span"}
MARKERS = ["marker-one", "marker-two", "marker-three"]
fake_binary = sandbox / "claude.exe"
fake_binary.write_bytes(b"not a real binary")

cc.require_cfg = lambda: CFG
cc.claude_binary = lambda: fake_binary
cc.cc_version = lambda: "2.1.259"
cc.repo_markers = lambda cfg: list(MARKERS)
cc.repo_antimarkers = lambda cfg: []
cc.repo_commit = lambda cfg: "deadbee"
cc.backup_is_patched = lambda markers: False
cc.adhoc_landed = lambda b, v, m: ([], "")

live_markers = set()
cc.binary_contains = lambda b, needles, live=True: {n for n in needles if n in live_markers}


def status_exit(**kw):
    """cmd_status's exit code, or 0 when it returns normally."""
    args = argparse.Namespace(check=kw.get("check", False), quiet=kw.get("quiet", False))
    try:
        cc.cmd_status(args)
        return 0
    except SystemExit as e:
        return e.code


cc.set_stock_arm("2.1.259", "test arm")
rc = status_exit(check=True)
check("status --check exits 3 in a verified stock arm (not 0, not 2)", rc == 3, str(rc))

live_markers = {"marker-one"}
rc = status_exit(check=True)
check("status --check exits 2 when state says stock but our text is live", rc == 2, str(rc))

live_markers = set()
cc.clear_stock_arm()
rc = status_exit(check=True)
check("status --check still exits 2 for an UNdeclared stock binary", rc == 2, str(rc))

live_markers = set(MARKERS)
rc = status_exit(check=True)
check("status --check exits 0 for a fully patched binary with no arm", rc == 0, str(rc))

# --- the tripwire speaks in the arm, and says which arm --------------------
import contextlib, io


def quiet_output():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        status_exit(quiet=True)
    return buf.getvalue()


live_markers = set()
cc.set_stock_arm("2.1.259", "test arm")
out = quiet_output()
check("tripwire announces the arm rather than falling silent",
      "STOCK ARM" in out and "deliberate" in out, repr(out))
check("tripwire does not tell the operator to re-apply while in the arm",
      "run ccctl.py apply" not in out, repr(out))

live_markers = set(MARKERS)
out = quiet_output()
check("tripwire flags state/binary disagreement inside the arm",
      "ARM DISAGREEMENT" in out, repr(out))

# A record for a version that is no longer installed describes a binary that
# is gone. It must not excuse an unpatched one nobody chose.
live_markers = set()
cc.set_stock_arm("2.1.257", "arm from an older version")
out = quiet_output()
check("a stale arm record does not suppress the STOCK PROMPTS warning",
      "STALE stock-arm record" in out and "STOCK PROMPTS" in out, repr(out))
rc = status_exit(check=True)
check("status --check ignores a stale arm and fails normally", rc == 2, str(rc))

cc.clear_stock_arm()

# --- dead anti-markers ------------------------------------------------------
# Found by the arm on the day it shipped: an anti-marker upstream has deleted
# itself is absent from a correctly patched binary for a reason that has
# nothing to do with us, and scores as a passing deletion forever. The check
# has to ask the STOCK binary whether the sentence was ever there.
stock_bin = sandbox / "2.1.259.stock"
stock_bin.write_bytes(b"stand-in for a pristine binary")
ANTI = ["deleted-one", "deleted-two", "never-was-stock"]
in_stock, in_live = {"deleted-one", "deleted-two"}, set()

cc.pristine_source = lambda ver, markers: str(stock_bin)
cc.binary_contains = lambda b, needles, live=True: {
    n for n in needles if n in (in_stock if str(b) == str(stock_bin) else in_live)}

revived, dead, note = cc.antimarkers_live(fake_binary, "2.1.259", ANTI, MARKERS)
check("an anti-marker absent from stock too reads DEAD, not as a deletion that holds",
      dead == ["never-was-stock"] and revived == [] and note == "", f"{revived} {dead} {note!r}")

in_live = {"deleted-one"}
revived, dead, note = cc.antimarkers_live(fake_binary, "2.1.259", ANTI, MARKERS)
check("a revived anti-marker is still reported alongside a dead one",
      revived == ["deleted-one"] and dead == ["never-was-stock"], f"{revived} {dead}")

# An unverifiable check must not read as a passing one: with no pristine
# parked, nothing may be claimed about which entries still prove something.
cc.pristine_source = lambda ver, markers: None
revived, dead, note = cc.antimarkers_live(fake_binary, "2.1.259", ANTI, MARKERS)
check("no parked pristine yields a note, and claims no dead entries",
      dead == [] and "no parked pristine" in note, f"{dead} {note!r}")

check("no anti-markers configured is not an error", cc.antimarkers_live(
    fake_binary, "2.1.259", [], MARKERS) == ([], [], ""))

# --- drift_probe. Same "absent is absent" trap as the dead anti-marker above,
# one command over: a whole-span replacement removes the stock text on purpose,
# so probing the LIVE patched binary for stock windows scores 0 and reports
# `DRIFTED — upstream rewrote this fragment` about text we removed ourselves.
# Measured on win32 right after a clean apply of CC 2.1.268: three fragments
# DRIFTED and check-update exited 2; the parked pristine read 6/7, 5/6, 7/10.
_live = Path(tempfile.mkdtemp(dir=sandbox)) / "claude"
_live.write_bytes(b"x")
_stock = Path(tempfile.mkdtemp(dir=sandbox)) / "claude-stock"
_stock.write_bytes(b"x")
_saved_pristine, _saved_contains = cc.pristine_source, cc.binary_contains

cc.pristine_source = lambda ver, markers: _stock
probe, caveat = cc.drift_probe("2.1.268", MARKERS, _live, "2.1.261")
check("drift_probe: prefers the parked pristine over the live binary",
      probe == _stock and caveat is None, f"{probe} {caveat!r}")

cc.pristine_source = lambda ver, markers: None
cc.binary_contains = lambda b, needles, live=True: set(needles)
probe, caveat = cc.drift_probe("2.1.268", MARKERS, _live, "2.1.261")
check("drift_probe: no pristine + a patched binary says the answer is unproven",
      probe == _live and caveat and "unproven" in caveat and "NO PRISTINE" in caveat,
      f"{probe} {caveat!r}")

cc.binary_contains = lambda b, needles, live=True: set()
probe, caveat = cc.drift_probe("2.1.268", MARKERS, _live, "2.1.261")
check("drift_probe: no pristine + a stock binary answers plainly, no caveat",
      probe == _live and caveat is None, f"{probe} {caveat!r}")

cc.pristine_source, cc.binary_contains = _saved_pristine, _saved_contains

# --- server experiment arms. Sections this tranche depends on are switched by a
# GrowthBook flag the server assigns, so the same binary can carry a fragment
# one week and not the next. Operator's ruling (2026-09-11): accept the arm,
# never fight it -- but know about it. Two things have to be exactly right or
# the instrument is worse than none: what counts as ARMED, and what counts as a
# CHANGE, because a first run on a fresh machine must not cry wolf twelve times.
check("arms_armed: false, null, empty and the strings 'default' and 'off' are all OFF",
      not any(cc.arms_armed(v) for v in (False, None, "", "default", "off")),
      str([cc.arms_armed(v) for v in (False, None, "", "default", "off")]))
check("arms_armed: true and any other non-empty value are ARMED",
      all(cc.arms_armed(v) for v in (True, "counter_steer", 1, "on")),
      str([cc.arms_armed(v) for v in (True, "counter_steer", 1, "on")]))

_first = {"tengu_bison_cairn": False, "tengu_cedar_lantern": True, "tengu_willow_tern": None}
check("arms_drift: a first run reports only the arms that are ON, not all of them",
      cc.arms_drift(_first, {}) == [("tengu_cedar_lantern", "(unrecorded)", True)],
      str(cc.arms_drift(_first, {})))

_state = {"serverArms": dict(_first)}
check("arms_drift: no change is reported as no change",
      cc.arms_drift(dict(_first), _state) == [])
check("arms_drift: an arm switching ON is reported",
      cc.arms_drift({**_first, "tengu_bison_cairn": True}, _state)
      == [("tengu_bison_cairn", False, True)],
      str(cc.arms_drift({**_first, "tengu_bison_cairn": True}, _state)))
# switching OFF matters as much as switching on: it is how a fragment silently
# stops reaching the model while every marker still verifies.
check("arms_drift: an arm switching OFF is reported too",
      cc.arms_drift({**_first, "tengu_cedar_lantern": False}, _state)
      == [("tengu_cedar_lantern", True, False)],
      str(cc.arms_drift({**_first, "tengu_cedar_lantern": False}, _state)))
check("arms_drift: a newly appearing but OFF arm is not news",
      cc.arms_drift({**_first, "tengu_new_thing": False}, _state) == [],
      str(cc.arms_drift({**_first, "tengu_new_thing": False}, _state)))

check("SERVER_ARMS: every env-forced arm names a real CLAUDE_CODE_ var",
      all(v[0] is None or v[0].startswith("CLAUDE_CODE_")
          for v in cc.SERVER_ARMS.values()),
      str([v[0] for v in cc.SERVER_ARMS.values()]))
check("SERVER_ARMS: the two the operator forces by env are tracked and marked OURS",
      cc.SERVER_ARMS["tengu_bison_cairn"][0] == "CLAUDE_CODE_BISON_CAIRN"
      and cc.SERVER_ARMS["tengu_larch_cistern"][0] == "CLAUDE_CODE_LARCH_CISTERN"
      and cc.SERVER_ARMS["tengu_bison_cairn"][2] == "OURS"
      and cc.SERVER_ARMS["tengu_larch_cistern"][2] == "OURS")

print()
print(f"{'FAILED: ' + ', '.join(fails) if fails else 'all stock-arm checks pass'}")
sys.exit(1 if fails else 0)
