"""Tests for the `global CLAUDE` line of `ccctl.py status`.

Run from anywhere:  python tools/tests/test_ccctl_global_claude_md.py
Temp directories only.

A machine's ~/.claude/CLAUDE.md is a link into private/global-claude-md/
(AGENTS.md, "The global CLAUDE.md"). The line has to tell the states apart
that look alike from a session: a local file is outside the sync, and a link
with a missing import still loads and silently drops the shared rules.
"""
import atexit
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "ccctl", str(Path(__file__).resolve().parent.parent / "ccctl.py"))
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


tmp = Path(tempfile.mkdtemp()); atexit.register(shutil.rmtree, tmp, ignore_errors=True)
repo = tmp / "repo" / "private" / "global-claude-md"; repo.mkdir(parents=True)
home = tmp / "home"; home.mkdir()
link = home / "CLAUDE.md"

check("absent file is named absent", "absent" in cc.global_claude_md_line(link))

link.write_text("# local\n")
line = cc.global_claude_md_line(link)
check("local file is outside the sync", "local file" in line and "outside" in line, line)
link.unlink()

(repo / "shared.md").write_text("# shared\n")
# One import per line, joined: in a single literal the escape's `n` and the
# next import read as an e-mail address to the public mirror's redaction, which
# rewrote the fixture.
(repo / "macos.md").write_text("\n".join(["@shared.md", "@home.md", "", "# This machine", ""]))
os.symlink(repo / "macos.md", link)
line = cc.global_claude_md_line(link)
check("a missing import is reported, not passed over", "MISSING import(s): home.md" in line, line)

(repo / "home.md").write_text("# home\n")
line = cc.global_claude_md_line(link)
check("linked file lists its imports", line.endswith("imports shared.md, home.md"), line)

link.unlink(); os.symlink(repo / "gone.md", link)
check("broken link is loud", "BROKEN LINK" in cc.global_claude_md_line(link))

link.unlink(); other = tmp / "elsewhere.md"; other.write_text("x"); os.symlink(other, link)
check("a link into any folder is shown with its target (the layout is not tied to this repo)",
      str(other.resolve()) in cc.global_claude_md_line(link))

# --- the read record: stale when never read and when the delivered prose or
# the CC version moved; quiet otherwise, whatever else in the repo changed.
class _R:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err

stale, line = cc.global_claude_md_review({}, state={})
check("never read: due, and says how to record it", stale and "global-md --read" in line, line)
seen = {cc.GLOBAL_MD_STATE_KEY: {"commit": "abcdef1234567890", "at": "2026-11-10", "fingerprint": "f1"}}
stale, line = cc.global_claude_md_review({}, state=seen, fingerprint="f1")
check("same delivered prose: not due", not stale and "abcdef12" in line, line)
stale, line = cc.global_claude_md_review({}, state=seen, fingerprint="f2")
check("delivered prose or version changed: due", stale and "has changed since" in line, line)
old = {cc.GLOBAL_MD_STATE_KEY: {"commit": "abcdef1234567890", "at": "2026-11-10"}}
real_git = cc.git
try:
    calls = []
    cc.git = lambda cfg, *a: (calls.append(a), _R(0, ""))[1]
    stale, line = cc.global_claude_md_review({}, state=old)
    check("a read recorded before fingerprints falls back to the fragments, and only them",
          not stale and calls and calls[0][-1] == "edits", (line, calls))
    cc.git = lambda cfg, *a: _R(0, "edits/x.md\n")
    stale, line = cc.global_claude_md_review({}, state=old)
    check("…and is due when a fragment changed", stale and "edits/x.md" in line, line)
    cc.git = lambda cfg, *a: _R(128, "", "fatal: bad object abcdef12")
    stale, line = cc.global_claude_md_review({}, state=old)
    check("a recorded commit this checkout lacks is due, not trusted", stale and "does not have" in line, line)
finally:
    cc.git = real_git

# --- the Explore link line
exp = home / "explore.md"
check("explore: absent names the built-in fallback", "built-in Explore" in cc.explore_agent_line(exp))
exp.write_text("x")
check("explore: a local file is outside the sync", "local file" in cc.explore_agent_line(exp))
exp.unlink(); os.symlink(tmp / "nowhere.md", exp)
check("explore: a broken link is loud and names the fallback",
      "BROKEN LINK" in cc.explore_agent_line(exp) and "built-in" in cc.explore_agent_line(exp))
exp.unlink(); os.symlink(repo / "shared.md", exp)
check("explore: a good link shows its target", str(repo / "shared.md") in cc.explore_agent_line(exp))

# --- a later machine takes the checkpoint's reviewed map when upstream has none
import hashlib, json
wrepo = tmp / "wsrepo"; (wrepo / "baseline" / "9.9.9").mkdir(parents=True)
body = b'{"prompts": []}'
(wrepo / "baseline" / "9.9.9" / "prompts.json").write_bytes(body)
rel = {"ccVersion": "9.9.9", "promptData": {"path": "baseline/9.9.9/prompts.json",
                                             "sha256": hashlib.sha256(body).hexdigest(), "pullRequest": 1}}
(wrepo / "release.json").write_text(json.dumps(rel))
cfgfile = tmp / "ccctl.json"; cfgfile.write_text(json.dumps({"repoPath": str(wrepo)}))
real_config = cc.CONFIG
try:
    cc.CONFIG = cfgfile
    cache = tmp / "cache" / "prompts-9.9.9.json"
    check("seed: the checkpoint version with a matching hash is taken",
          cc.seed_from_checkpoint("9.9.9", cache) and cache.read_bytes() == body)
    other = tmp / "cache" / "prompts-9.9.8.json"
    check("seed: another version is not", not cc.seed_from_checkpoint("9.9.8", other) and not other.exists())
    rel["promptData"]["sha256"] = "0" * 64; (wrepo / "release.json").write_text(json.dumps(rel))
    cache.unlink()
    check("seed: a file that does not match release.json is refused",
          not cc.seed_from_checkpoint("9.9.9", cache) and not cache.exists())
finally:
    cc.CONFIG = real_config

# --- pull follows the links to the checkout they live in, and only there
work = tmp / "work"; (work / ".git").mkdir(parents=True); (work / "private").mkdir()
(work / "private" / "x.md").write_text("x")
lf = home / "linked.md"; os.symlink(work / "private" / "x.md", lf)
real_linked = cc.linked_user_files
try:
    cc.linked_user_files = lambda: [lf, home / "not-there.md", link]
    found = cc.linked_checkouts({"repoPath": str(wrepo)})
    check("linked checkouts: the working checkout a link points into is found once",
          found == [work.resolve()], found)
    check("linked checkouts: the workspace clone itself is not pulled twice",
          cc.linked_checkouts({"repoPath": str(work)}) == [])
finally:
    cc.linked_user_files = real_linked

# The real files, where this checkout has them (the public mirror does not).
root = Path(__file__).resolve().parent.parent.parent
for real in (root / "private" / "global-claude-md", root / "global-claude-md.example"):
    if not real.is_dir():
        continue
    for machine in sorted(real.glob("*.md")):
        for ln in machine.read_text(encoding="utf-8").splitlines():
            if ln.startswith("@"):
                check(f"{real.name}/{machine.name}: import {ln} exists", (real / ln[1:].strip()).is_file())

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
