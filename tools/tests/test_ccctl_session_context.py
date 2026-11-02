"""Tests for the `[session]` line the session-start tripwire prints.

Run from anywhere:  python tools/tests/test_ccctl_session_context.py
Temp directories only; no git process, no real hostname dependence.

What is worth pinning: every way the derivation can fail says so in words the
floor names (UNRECOGNIZED, MIXED, FAILED, UNKNOWN), with the evidence it saw,
because the model is told to react to exactly those, and a silent guess is
the failure the operator asked to be told about (2026-10-29).
"""
import atexit
import contextlib
import importlib.util
import io
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
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        fails.append(name)


sandbox = Path(tempfile.mkdtemp(prefix="ccctl-session-"))
atexit.register(shutil.rmtree, sandbox, True)


def repo(name, *urls, worktree=False):
    root = sandbox / name
    git = root / ".git"
    git.mkdir(parents=True)
    body = "[core]\n\tbare = false\n"
    for i, u in enumerate(urls):
        body += f'[remote "r{i}"]\n\turl = {u}\n\tfetch = +refs/heads/*:refs/remotes/r{i}/*\n'
    (git / "config").write_text(body, encoding="utf-8")
    if not worktree:
        return root
    wt = sandbox / (name + "-wt")
    wt.mkdir()
    meta = git / "worktrees" / "wt"
    meta.mkdir(parents=True)
    (meta / "commondir").write_text("../..\n", encoding="utf-8")
    (wt / ".git").write_text(f"gitdir: {meta}\n", encoding="utf-8")
    return wt


# Fixtures are built from the pattern tables rather than spelled out, so the
# test names no host, organisation or account of its own: the public mirror
# redacts those in ccctl.py, and the test still exercises whatever they become.
REMOTES = dict(cc.SESSION_REMOTES)
ADO, FORGEJO, GH_SCP = REMOTES["day-job"][0], REMOTES["private"][0], REMOTES["private"][2]


def host_for(word):
    return next(h for h, d in cc.SESSION_MACHINES.items() if word in d)


WORK_HOST, LINUX_HOST, MAC_HOST = host_for("work laptop"), host_for("Kubuntu"), host_for("MacBook")

work = repo("workrepo", f"https://user@{ADO}/proj/_git/workrepo")
private = repo("dispositions", f"http://{FORGEJO}/owner/claude-code-dispositions.git")
gh = repo("musializer", f"git@{GH_SCP}rusty-musializer.git", worktree=True)
third = repo("tweakcc", "https://github.com/Piebald-AI/tweakcc.git")
mixed = repo("both", f"git@{GH_SCP}x.git", f"https://{ADO}/p/_git/x")
bare = repo("noremote")
nogit = sandbox / "plain"
nogit.mkdir()
(work / "src" / "deep").mkdir(parents=True)

line = cc.session_context(WORK_HOST.upper(), str(work / "src" / "deep"))
check("work laptop + ADO repo (from a subdirectory): day-job",
      line.startswith(f"[session] machine {WORK_HOST.upper()}: the work laptop")
      and "workrepo" not in line.split(";")[0]
      and "project deep: day-job work" in line, line)
line = cc.session_context(LINUX_HOST + ".lan", str(private))
check("domain suffix stripped; Forgejo remote: private",
      f"machine {LINUX_HOST}: the private Kubuntu" in line and "private work" in line, line)
line = cc.session_context(MAC_HOST, str(gh))
check("worktree .git file followed to the common config", "private work" in line, line)
line = cc.session_context(WORK_HOST, str(third))
check("someone else's GitHub repo: a third-party clone, with the remote shown",
      "third-party clone" in line and "Piebald-AI/tweakcc" in line, line)
line = cc.session_context(WORK_HOST, str(mixed))
check("work and private remotes together: MIXED with both URLs", "MIXED" in line
      and "dev.azure.com" in line and GH_SCP + "x" in line, line)
check("no remote: UNRECOGNIZED", "UNRECOGNIZED (no git remote)" in cc.session_context(WORK_HOST, str(bare)))
check("not a repository: UNRECOGNIZED", "UNRECOGNIZED (no git remote)" in cc.session_context(WORK_HOST, str(nogit)))
line = cc.session_context("some-vm", str(private))
check("unknown host: UNRECOGNIZED with the hostname", "machine some-vm: UNRECOGNIZED hostname" in line, line)
check("no project dir: UNKNOWN, not a guess",
      "UNKNOWN (CLAUDE_PROJECT_DIR not set)" in cc.session_context(WORK_HOST, ""), "")

_saved = cc.session_context
cc.session_context = lambda *a, **k: 1 / 0
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    cc.session_context_quiet()
cc.session_context = _saved
check("a crashing derivation prints FAILED instead of nothing",
      buf.getvalue().startswith("[session] derivation FAILED"), buf.getvalue())

floor = cc.plan_adhocs.__code__.co_consts
text = " ".join(c for c in floor if isinstance(c, str))
check("the floor names every failure word this code can print",
      all(w in text for w in ("UNRECOGNIZED", "UNKNOWN", "MIXED", "FAILED", "`[session]`")), "")

sys.exit(1 if fails else 0)
