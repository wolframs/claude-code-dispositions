"""Tests for tools/closing_lengths.py's `--by prompt` split.

Run from anywhere:  python tools/tests/test_closing_lengths.py

Why this file exists: the first count after the 2026-09-21 floor rewrite was
cut by timestamp, and it was wrong. The T3 Code session it measured kept the
prompt it had started with until a compaction an hour and a half after the
apply, so a timestamp cut filed old-floor replies under the new floor. Since
CC 2.1.268 each process start records a `prompt_snapshot` attachment with the
full system prompt, and the split now reads that. These pin the three things
the split depends on: a closer takes the snapshot before it, a subagent's
records never set the main thread's prompt, and a session with no snapshot
is reported as such rather than guessed.
"""
import atexit
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "closing_lengths", str(Path(__file__).resolve().parent.parent / "closing_lengths.py"))
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        fails.append(name)


MARKERS = ["old floor sentence", "Work that went right is not news", "shared sentence"]


def snapshot(ts, text, sidechain=False):
    return {"type": "attachment", "timestamp": ts, "isSidechain": sidechain,
            "attachment": {"type": "prompt_snapshot", "systemPrompt": ["# Harness\n", text]}}


def closer(ts, rid, words, model="claude-fable-5-1"):
    return {"type": "assistant", "timestamp": ts, "requestId": rid, "sessionId": "s1",
            "entrypoint": "sdk-ts", "version": "2.1.278",
            "message": {"model": model, "content": [{"type": "text", "text": " ".join(["w"] * words)}]}}


def tool_turn(ts, rid):
    return {"type": "assistant", "timestamp": ts, "requestId": rid, "sessionId": "s1",
            "message": {"model": "claude-fable-5-1", "content": [{"type": "tool_use", "name": "Bash"}]}}


root = Path(tempfile.mkdtemp(prefix="closing-lengths-"))
atexit.register(shutil.rmtree, root, True)
proj = root / "-home-x-proj"
proj.mkdir()
records = [
    snapshot("2026-09-21T10:00:00Z", "shared sentence. old floor sentence."),
    closer("2026-09-21T10:05:00Z", "r1", 300),
    tool_turn("2026-09-21T10:06:00Z", "r2"),
    # a subagent's snapshot in the main file must not move the main prompt
    snapshot("2026-09-21T10:07:00Z", "shared sentence. Work that went right is not news.", sidechain=True),
    closer("2026-09-21T10:08:00Z", "r3", 280),
    # the apply landed at 10:10, but this process only picks it up at 11:40
    closer("2026-09-21T11:00:00Z", "r4", 260),
    snapshot("2026-09-21T11:40:00Z", "shared sentence. Work that went right is not news."),
    closer("2026-09-21T11:45:00Z", "r5", 90),
]
(proj / "s1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
(proj / "old.jsonl").write_text(json.dumps(closer("2026-09-21T09:00:00Z", "r0", 50)) + "\n",
                                encoding="utf-8")

rows = cl.closers("2026-09-21", root=str(root), markers=MARKERS)
by_rid = {r["ts"][11:16]: r for r in rows}
old = frozenset({"shared sentence", "old floor sentence"})
new = frozenset({"shared sentence", "Work that went right is not news"})

check("tool turns are not closers", len(rows) == 5, f"{len(rows)} rows")
check("a closer takes the snapshot before it", by_rid["10:05"]["prompt"] == old)
check("a sidechain snapshot does not move the main prompt", by_rid["10:08"]["prompt"] == old)
check("a closer after the apply but before the restart keeps the old prompt",
      by_rid["11:00"]["prompt"] == old)
check("the closer after the new snapshot has the new prompt", by_rid["11:45"]["prompt"] == new)
check("a session without snapshots reports none", by_rid["09:00"]["prompt"] is None)

names, legend = cl.prompt_names(rows)
check("prompts are named in order of first appearance", names == {old: "P1", new: "P2"}, str(names))
check("the legend names the common set", legend[0] == "common to every recorded prompt: 1 markers",
      legend[0] if legend else "no legend")
check("the legend lists only what differs",
      "'Work that went right is not news'" in legend[2] and "shared sentence" not in legend[2],
      legend[2] if len(legend) > 2 else "short legend")

check("the real marker file parses without code markers",
      all(not m.startswith(")") for m in cl.prose_markers()) and len(cl.prose_markers()) > 0)

if fails:
    print(f"\n{len(fails)} FAILED")
    sys.exit(1)
print("\nall passed")
