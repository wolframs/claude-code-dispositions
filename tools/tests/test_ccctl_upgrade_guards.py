"""Tests for the three guards that make an intermittent CC upgrade cheap.

Run from anywhere:  python tools/tests/test_ccctl_upgrade_guards.py
No real binaries, no network; a temp repo skeleton and stubbed lookups only.

Each guard exists because this round cost an agent real thinking (win32,
2026-09-08), and the point of pinning them is that the NEXT round should not:

  * `tweakcc_build_state` — a CLEAN analysis says nothing about tweakcc's own
    patch set, because `analyze` never runs it. 2.1.261 analysed CLEAN 11/11
    AUTO and still needed a source overlay for Anthropic's rebuilt model menu.
    The ladder below is the whole diagnosis, and it must NEVER raise or gate:
    a rejected patch set costs the in-CC banner, not a correct prompt, so it
    reports. `assemble()` is called with a partial cfg in its own tests, and a
    diagnostic that dies there would turn a survivable fallback into no binary.
  * `divergence_message` — `git pull --ff-only` is how a fleet round arrives,
    and git's own hint stops at "merge or rebase". This repo reconciles machine
    rounds by MERGE and records the resolutions in the merge commit; saying so
    at the refusal is what removes the archaeology.
  * `downgrade_refusal` — a bare `update` follows the configured channel, and
    `stable` served 2.1.236 while this machine ran 2.1.261. Nothing downstream
    objects to that: it is a legitimate staged swap onto a real release.

Added 2026-09-11 (macOS): `tripwire_state`. The SessionStart tripwire cannot
report its own death, because CC drops a failed hook without a word. So `status`
checks from outside that every path the hook names still exists.
"""
import atexit
import shutil
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


sandbox = Path(tempfile.mkdtemp(prefix="ccctl-guards-"))
atexit.register(shutil.rmtree, sandbox, True)
cc.HERE = sandbox

# --- ver_key: releases sort the way humans read them ------------------------
check("ver_key: 2.1.9 sorts below 2.1.10 (not lexically)",
      cc.ver_key("2.1.9") < cc.ver_key("2.1.10"))
check("ver_key: the case that mattered — stable 2.1.236 < installed 2.1.261",
      cc.ver_key("2.1.236") < cc.ver_key("2.1.261"))
check("ver_key: equal versions compare equal", cc.ver_key("2.1.261") == cc.ver_key("2.1.261"))
check("ver_key: a missing/blank version sorts lowest rather than raising",
      cc.ver_key(None) < cc.ver_key("0.0.1") and cc.ver_key("") == ())

# --- overlay inventory: the filename is the record --------------------------
repo = sandbox / "repo"
(repo / "tools" / "tweakcc-overlays").mkdir(parents=True)
cfg = {"repoPath": "repo", "channel": "stable"}

check("overlay_inventory: a missing overlay dir is empty, not an error",
      cc.overlay_inventory({"repoPath": "nonexistent"}) == [])
check("overlay_inventory: no overlays yet reads as empty", cc.overlay_inventory(cfg) == [])

for name in ("4.3.3-cc-2.1.257.patch", "4.3.3-cc-2.1.261.patch",
             "4.3.3-cc-2.1.99.patch", "README.md", "not-an-overlay.patch"):
    (repo / "tools" / "tweakcc-overlays" / name).write_text("x", encoding="utf-8")

inv = cc.overlay_inventory(cfg)
check("overlay_inventory: parses only <twk>-cc-<cc>.patch names", len(inv) == 3, str(inv))
check("overlay_inventory: newest CC version is last, numerically",
      [i[1] for i in inv] == ["2.1.99", "2.1.257", "2.1.261"], str([i[1] for i in inv]))
check("overlay_inventory: carries the tweakcc version too", inv[-1][2] == "4.3.3")

# --- the pin is read from the builder, which owns it ------------------------
builder = repo / "tools" / "build_local_tweakcc.py"
check("overlay_pin: absent builder yields None", cc.overlay_pin(cfg) is None)
builder.write_text('EXPECTED_SHA = "4d78df30a235017d8b5769c80fcc84170716d77d"\n', encoding="utf-8")
check("overlay_pin: reads EXPECTED_SHA", cc.overlay_pin(cfg).startswith("4d78df3"))

# Since 2026-09-13 release.json owns the pin and the builder reads it at import,
# so the literal is gone from current checkouts; release.json must win, and a
# release.json without a commit must fall through to the literal, not to None.
release_file = repo / "release.json"
release_file.write_text('{"tweakcc": {"commit": "f760b888a442436032c3d0a8b6a5bb3b479bb102"}}',
                        encoding="utf-8")
check("overlay_pin: release.json tweakcc.commit wins over the builder literal",
      cc.overlay_pin(cfg).startswith("f760b88"))
release_file.write_text('{"tweakcc": {"commit": "not a sha"}}', encoding="utf-8")
check("overlay_pin: a malformed release.json commit falls back to the builder",
      cc.overlay_pin(cfg).startswith("4d78df3"))
release_file.write_text('{"tweakcc": {}}', encoding="utf-8")
check("overlay_pin: release.json without a commit falls back to the builder",
      cc.overlay_pin(cfg).startswith("4d78df3"))
release_file.unlink()

# --- the build-state ladder -------------------------------------------------
# Stub the three lookups that touch the machine, so the ladder itself is what
# is under test. `tweakcc` is version-gated and would die() on a fake path.
PIN = "4d78df30a235017d8b5769c80fcc84170716d77d"
clone_path = sandbox / "tweakcc-clone"
clone_path.mkdir()

state = {"twk": "4.3.3", "clone": clone_path, "head": PIN, "dirty": False}
cc.tweakcc = lambda: "tweakcc-stub"
cc.run = lambda argv, **kw: type("R", (), {"stdout": state["twk"], "stderr": "", "returncode": 0})()
cc.tweakcc_clone = lambda: state["clone"]
cc.clone_head = lambda c: (state["head"], state["dirty"])


def verdict(target="2.1.261", **over):
    state.update(over)
    return cc.tweakcc_build_state(cfg, target)


check("build state: clone at the pin and an overlay covering the target reads ok",
      verdict()["verdict"] == "ok", verdict()["line"])
check("build state: a stale clone names the rebuild command, with the pin in it",
      verdict(head="0000000deadbeef0000000deadbeef0000000dea")["verdict"] == "stale-clone"
      and PIN in verdict(head="0000000deadbeef0000000deadbeef0000000dea")["fix"])
check("build state: an abbreviated HEAD matching the pin is not called stale",
      verdict(head=PIN[:10])["verdict"] == "ok")
check("build state: uncommitted tracked changes are their own verdict",
      verdict(head=PIN, dirty=True)["verdict"] == "dirty-clone")
check("build state: a published install says the overlay is not in it",
      verdict(dirty=False, clone=None)["verdict"] == "published")
check("build state: a target newer than every overlay is UNPROVEN, not ok",
      verdict(target="2.1.270", clone=clone_path)["verdict"] == "uncovered")
check("build state: the uncovered fix predicts the fallback rather than promising failure",
      "tweakcc-fallback" in verdict(target="2.1.270")["fix"])
check("build state: a tweakcc newer than the overlay flags a possibly-upstream fix",
      verdict(target="2.1.261", twk="4.4.0")["verdict"] == "overlay-behind-tweakcc")
check("build state: no overlays in the repo means nothing to check",
      cc.tweakcc_build_state({"repoPath": "nonexistent"}, "2.1.261")["verdict"] == "no-overlays")

# The invariant that keeps this a diagnostic. assemble() passes a partial cfg.
partial = cc.tweakcc_build_state({"tweakccPatches": True}, "9.9.9")
check("build state: a cfg with no repoPath reads unknown instead of raising",
      partial["verdict"] == "unknown", str(partial))
check("build state: every quiet verdict is quiet, and no actionable one is",
      set(cc.QUIET_BUILD) == {"ok", "no-overlays", "unknown"}
      and all(cc.tweakcc_build_state({"repoPath": "nonexistent"}, v)["fix"] is None
              for v in ("2.1.261",)))
check("build state: actionable verdicts always carry a fix an agent can run",
      all(verdict(**case)["fix"] for case in (
          {"head": "0" * 40, "dirty": False, "clone": clone_path},
          {"head": PIN, "dirty": True},
          {"dirty": False, "clone": None})))

# A guard that cannot find the pin must say so, not read ok. This is exactly
# what happened on 2026-09-13 when the pin moved into release.json and the
# regex over the builder came back empty: a stale clone read "== overlay pin".
builder.write_text("# pin lives in release.json now\n", encoding="utf-8")
nopin = verdict(twk="4.3.3", head="0000000deadbeef0000000deadbeef0000000dea", dirty=False,
                clone=clone_path)
check("build state: a missing pin is its own loud verdict, never ok",
      nopin["verdict"] == "no-pin" and nopin["fix"] and "no-pin" not in cc.QUIET_BUILD, str(nopin))
builder.write_text('EXPECTED_SHA = "%s"\n' % PIN, encoding="utf-8")
check("build state: the pin restored, the same stale clone is stale again",
      verdict(head="0000000deadbeef0000000deadbeef0000000dea")["verdict"] == "stale-clone")

# A pin taken from a PR head is not on main after a squash merge, so the fix
# line must fetch the ref release.json names (Linux 2026-09-13: `fetch origin
# main` left the pin unknown and the printed command failed).
check("build state: without a fetchRef the stale fix fetches main",
      "fetch origin main &&" in verdict(head="0" * 40)["fix"])
release_file.write_text('{"tweakcc": {"commit": "%s", "fetchRef": "pull/993/head"}}' % PIN,
                        encoding="utf-8")
check("build state: with a fetchRef the stale fix fetches that ref",
      "fetch origin pull/993/head &&" in verdict(head="0" * 40)["fix"])
release_file.unlink()
state.update(head=PIN)

# --- divergence: only a genuine two-sided divergence gets the long message --
def diverged(ahead=2, behind=2, local="aaa1111 local round", remote="bbb2222 fleet round"):
    return {"upstream": "origin/master", "ahead": ahead, "behind": behind,
            "local": local, "remote": remote}


check("divergence: no upstream to compare with yields None (git's words stand)",
      cc.divergence_message(repo, None) is None)
check("divergence: ahead only is not divergence — --ff-only would have worked",
      cc.divergence_message(repo, diverged(behind=0)) is None)
check("divergence: behind only is not divergence either",
      cc.divergence_message(repo, diverged(ahead=0)) is None)

msg = cc.divergence_message(repo, diverged(), stderr="fatal: Not possible to fast-forward")
check("divergence: names both sides with counts", "2 local, 2 remote" in msg, msg)
check("divergence: shows the actual commits on each side",
      "aaa1111 local round" in msg and "bbb2222 fleet round" in msg)
check("divergence: states the repo's convention, and which one it is NOT",
      "MERGE, not rebase" in msg)
check("divergence: hands over a runnable merge command", "merge --no-ff origin/master" in msg)
check("divergence: cites the precedent commit so the resolution style is findable",
      "bad7880" in msg)
check("divergence: tells the reader to read the incoming commits first",
      "pipeline repairs" in msg)
check("divergence: keeps git's own words at the end", "fast-forward" in msg.split("git said:")[1])
check("divergence: an empty side prints (none) rather than a blank",
      "(none)" in cc.divergence_message(repo, diverged(local=""), stderr="x"))

# --- downgrade: the channel can point backwards ----------------------------
check("downgrade: a newer target proceeds",
      cc.downgrade_refusal("stable", "2.1.263", "2.1.261") is None)
check("downgrade: the same version proceeds (a re-apply at this version)",
      cc.downgrade_refusal("stable", "2.1.261", "2.1.261") is None)
check("downgrade: --allow-downgrade proceeds deliberately",
      cc.downgrade_refusal("stable", "2.1.236", "2.1.261", allow=True) is None)

refusal = cc.downgrade_refusal("stable", "2.1.236", "2.1.261")
check("downgrade: the measured case is refused", refusal is not None)
check("downgrade: says which channel served it, so the cause is not a mystery",
      "channel `stable`" in refusal, str(refusal))
check("downgrade: offers both ways forward — name the version, or the flag",
      "update <VER>" in refusal and "--allow-downgrade" in refusal)

# --- the flag is actually wired to the parser ------------------------------
args = cc.build_parser().parse_args(["update", "2.1.236", "--allow-downgrade"])
check("parser: `update --allow-downgrade` parses and sets the flag",
      args.allow_downgrade is True and args.version == "2.1.236")
check("parser: `update` without the flag defaults to refusing",
      cc.build_parser().parse_args(["update"]).allow_downgrade is False)

# --- the tripwire cannot report its own death -------------------------------
# macOS 2026-09-11: the hook named ~/cc-dispositions/tools/ccctl.py, long gone;
# CC swallowed the exit-2 and every session started as silent as a patched one.
import json  # noqa: E402

tw = sandbox / "tripwire"
(tw / "ccctl" / "repo" / "tools").mkdir(parents=True)
(tw / "ccctl" / "repo" / "tools" / "ccctl.py").write_text("", encoding="utf-8")


def hook_settings(cmd):
    p = tw / "settings.json"
    hooks = {"SessionStart": [{"hooks": [{"type": "command", "command": cmd}]}]} if cmd else {}
    p.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    return p


live = f"cd {(tw / 'ccctl').as_posix()} && python3 {(tw / 'ccctl' / 'repo' / 'tools' / 'ccctl.py').as_posix()} status --quiet"
check("tripwire: hook naming an existing script and cd target is ok",
      cc.tripwire_state(hook_settings(live))[0] == "ok", str(cc.tripwire_state(hook_settings(live))))
dead = live.replace("/repo/tools/", "/gone/tools/")
v, d = cc.tripwire_state(hook_settings(dead))
check("tripwire: the measured case — a script path that no longer exists is BROKEN",
      v == "broken" and "gone" in d, f"{v}: {d}")
v, d = cc.tripwire_state(hook_settings(live.replace(f"cd {(tw / 'ccctl').as_posix()}",
                                                    f"cd {(tw / 'nohome').as_posix()}")))
check("tripwire: a missing cd target is BROKEN too (it is ccctl's HERE)",
      v == "broken" and "nohome" in d, f"{v}: {d}")
check("tripwire: no ccctl hook at all reads none",
      cc.tripwire_state(hook_settings(None))[0] == "none")
check("tripwire: an unrelated SessionStart hook does not count as the tripwire",
      cc.tripwire_state(hook_settings("~/.masko-desktop/hooks/hook-sender.sh"))[0] == "none")
check("tripwire: absent settings file reads none, never raises",
      cc.tripwire_state(tw / "nope.json")[0] == "none")
(tw / "bad.json").write_text("{not json", encoding="utf-8")
check("tripwire: unreadable settings is unknown, never raises",
      cc.tripwire_state(tw / "bad.json")[0] == "unknown")

# --- the tripwire cannot report its own TIMEOUT either -----------------------
# Linux 2026-08-26 to 2026-09-21: the check took 14 s against the hook's 10 s,
# CC killed it at ~10,035 ms and recorded `hook_cancelled`, and every session
# started as silent as a patched one. Nothing looked, because nothing ran long
# enough to print. These are the three views that now do.

def timed_settings(timeout):
    p = tw / "timed.json"
    hook = {"type": "command", "command": live, "statusMessage": "Checking prompt-patch state..."}
    if timeout is not None:
        hook["timeout"] = timeout
    p.write_text(json.dumps({"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": "node vercel-thing.mjs"}]},
        {"hooks": [hook]}]}}), encoding="utf-8")
    return p


hook = cc.tripwire_hook(timed_settings(10))
check("tripwire_hook: finds the ccctl hook among other SessionStart hooks",
      hook and hook["timeout"] == 10 and "ccctl.py" in hook["command"], str(hook))
check("tripwire_hook: none when no hook runs ccctl",
      cc.tripwire_hook(hook_settings("~/.masko-desktop/hooks/hook-sender.sh")) is None)
check("tripwire_slow: the measured failure reads KILLED",
      cc.tripwire_slow(14.2, 10)[0] == "KILLED")
check("tripwire_slow: past half the timeout reads SLOW, before a busy start kills it",
      cc.tripwire_slow(6.0, 10)[0] == "SLOW" and cc.tripwire_slow(2.9, 10)[0] == "ok")
check("tripwire_slow: no explicit timeout is CC's default of minutes, not a finding",
      cc.tripwire_slow(14.2, None)[0] == "ok")


_real_monotonic = cc.time.monotonic


class _Clock:
    """A runner that takes as long as it is told, without sleeping."""
    def __init__(self, secs):
        self.secs = secs

    def __call__(self, argv, **kw):
        self.argv, self.kw = argv, kw
        cc.time.monotonic = lambda: _real_monotonic() + self.secs


clock = _Clock(12.5)
elapsed, err = cc.tripwire_timed(hook, runner=clock)
cc.time.monotonic = _real_monotonic
check("tripwire_timed: runs this file's own `status --quiet`, portable to win32",
      clock.argv[-2:] == ["status", "--quiet"] and clock.argv[0] == sys.executable, str(clock.argv))
check("tripwire_timed: measures the run and gives it room past the timeout to be caught",
      err is None and 12 < elapsed < 13 and clock.kw["timeout"] == 20, f"{elapsed} {err} {clock.kw}")

# The quiet run's own clock, wired in main(): a run past half its timeout
# says so in the session, on every exit path of `status --quiet`.
import contextlib  # noqa: E402
import io  # noqa: E402

_saved = (cc.cmd_status, cc.tripwire_hook, cc.STARTED, sys.argv)
cc.cmd_status = lambda args: sys.exit(0)
cc.tripwire_hook = lambda settings=None: {"timeout": 10}
cc.STARTED = _real_monotonic() - 7.0
sys.argv = ["ccctl.py", "status", "--quiet"]
out = io.StringIO()
with contextlib.redirect_stdout(out):
    try:
        cc.main()
    except SystemExit:
        pass
cc.STARTED = _real_monotonic()
quiet_out = io.StringIO()
with contextlib.redirect_stdout(quiet_out):
    try:
        cc.main()
    except SystemExit:
        pass
cc.cmd_status, cc.tripwire_hook, cc.STARTED, sys.argv = _saved
check("quiet: a slow run warns in the session, even when the check exits early",
      out.getvalue().startswith("[ccctl] TRIPWIRE SLOW: this check takes 7.0 s"), out.getvalue())
check("quiet: a fast run stays silent", quiet_out.getvalue() == "", quiet_out.getvalue())

proj = tw / "projects" / "-home-x"
proj.mkdir(parents=True)


def record(ts, kind, cmd=None, ms=None, event="SessionStart:startup"):
    a = {"type": kind, "hookName": event, "hookEvent": "SessionStart"}
    if cmd:
        a["command"] = cmd
    if ms:
        a["durationMs"] = ms
    return json.dumps({"type": "attachment", "attachment": a, "timestamp": ts})


(proj / "old.jsonl").write_text("\n".join([
    record("2026-09-20T13:37:16.284Z", "hook_success", "node vercel-thing.mjs"),
    record("2026-09-20T13:37:26.206Z", "hook_cancelled", "Checking prompt-patch state...", 10036),
]) + "\n", encoding="utf-8")
last, since = cc.tripwire_kills(hook, root=tw / "projects")
check("tripwire_kills: finds CC's record of the kill by the hook's status message",
      last == ("2026-09-20T13:37:26.206Z", 10036) and since == 0, f"{last} {since}")
(proj / "new.jsonl").write_text("\n".join([
    record("2026-09-21T18:00:01.000Z", "hook_success", "node vercel-thing.mjs"),
    record("2026-09-21T18:00:01.500Z", "hook_success", "node vercel-thing.mjs"),
    record("2026-09-21T19:00:00.000Z", "hook_success", "node vercel-thing.mjs",
           event="SessionStart:compact"),
]) + "\n", encoding="utf-8")
last, since = cc.tripwire_kills(hook, root=tw / "projects")
check("tripwire_kills: counts the starts after the kill, records of one start as one",
      last[0].startswith("2026-09-20") and since == 2, f"{last} {since}")
check("tripwire_kills: an unrelated hook's cancellation is not ours",
      cc.tripwire_kills({"statusMessage": "something else"}, root=tw / "projects") == (None, 0))


# --- the checkpoint line reports and never gates (2026-09-21)
#
# `release.json`'s ccVersion went five releases and five days stale without
# anything on screen saying so, because nothing in the apply path reads it.
# The line makes the divergence visible; these checks pin that it stays a
# report — it names the routine step, it never returns a refusal, and it is
# not consulted by any verdict.
import tempfile as _tf, json as _json, shutil as _sh
from pathlib import Path as _P
_tmp = _P(_tf.mkdtemp())
(_tmp / "repo").mkdir()
def _cp(version, observed="2026-09-21"):
    (_tmp / "repo" / "release.json").write_text(
        _json.dumps({"ccVersion": version, "observedAt": observed}), encoding="utf-8")
    return cc.checkpoint_line({"repoPath": "repo"}, INSTALLED)
_saved_here = cc.HERE
cc.HERE = _tmp
INSTALLED = "2.1.278"
try:
    line = _cp("2.1.278")
    check("checkpoint: same version reads as the installed one",
          "installed here" in line and "2.1.278" in line, line)
    line = _cp("2.1.273")
    check("checkpoint: a stale checkpoint names the machine's version and the routine step",
          "2.1.273" in line and "2.1.278" in line and "routine" in line, line)
    check("checkpoint: a stale checkpoint is not phrased as a refusal",
          not any(w in line.upper() for w in ("FAIL", "REFUS", "BLOCK")), line)
    line = _cp("2.1.299")
    check("checkpoint: a machine behind the checkpoint is told so, not told to advance it",
          "BEHIND" in line and "routine" not in line, line)
    (_tmp / "repo" / "release.json").write_text("{}", encoding="utf-8")
    line = cc.checkpoint_line({"repoPath": "repo"}, INSTALLED)
    check("checkpoint: no version recorded points at the release step",
          "spec/40" in line, line)
finally:
    cc.HERE = _saved_here
    _sh.rmtree(_tmp, ignore_errors=True)
print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all upgrade-guard checks pass'}")
sys.exit(1 if fails else 0)
