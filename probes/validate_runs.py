#!/usr/bin/env python3
"""Classify probe runs: clean, limit-tainted, or otherwise broken.

A limit-tainted or errored run must never be scored as baseline data; this
prints a re-queue list for them. Clean = session exited 0, produced a final
message, and no usage-limit marker anywhere in its outputs.
"""

import re
import sys
from pathlib import Path

RUNS = Path.home() / "probe-runs"
LIMIT = re.compile(r"usage limit|limit reset|resets \d|hit your (5-hour|five-hour|weekly|usage)", re.I)


def classify(run):
    meta = run / ".runmeta"
    if not (meta / "finished.txt").exists():
        return "in-progress"
    verdicts = []
    for i in (1, 2):
        msg = meta / f"final-message-{i}.txt"
        err = meta / f"final-message-{i}.txt.err"
        if not msg.exists():
            continue
        text = msg.read_text(errors="replace")
        errtext = err.read_text(errors="replace") if err.exists() else ""
        if LIMIT.search(text) or LIMIT.search(errtext):
            return "limit-tainted"
        m = re.search(r"exit=(\d+)", errtext)
        code = int(m.group(1)) if m else -1
        if code != 0 or not text.strip():
            verdicts.append(f"session{i}-exit{code}-{'empty' if not text.strip() else 'err'}")
    return "; ".join(verdicts) if verdicts else "clean"


def main():
    rows = sorted(RUNS.glob("p*-r[0-9]"))
    requeue = []
    for run in rows:
        v = classify(run)
        print(f"{run.name:22s} {v}")
        if v not in ("clean", "in-progress"):
            rig, k = run.name.rsplit("-r", 1)
            requeue.append(f"{rig} {k}")
    if requeue:
        Path("/tmp/probe-requeue.txt").write_text("\n".join(requeue) + "\n")
        print(f"\n{len(requeue)} to re-run -> /tmp/probe-requeue.txt")
    else:
        print("\nall completed runs clean")


if __name__ == "__main__":
    main()
