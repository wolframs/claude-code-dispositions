#!/usr/bin/env python3
"""How long closing replies are, and how much of them is bullet structure.

A closing reply is a main-thread API response that carries text and no tool
call — the message a session ends a turn on, which is the one the operator
reads (spec/15 §2). For each, count words, bullets with a bolded label
(`- **Label:** ...`, the shape TODO 30 names) and bullets of any kind, then
group by model x entrypoint x CC version, or by session.

    closing_lengths.py [SINCE]                 # default 2026-09-01, all projects
    closing_lengths.py SINCE --by session
    closing_lengths.py SINCE --by prompt       # split by the prompt each session recorded
    closing_lengths.py SINCE --session f8f264a1 --show 3

This is a count, not a verdict: which lengths are right is the operator's
call, made by living with the patch (spec/00 §10.1). It exists so that "the
closers got longer" is a number with a date instead of an impression, and so
the TODO 30 re-measurement is one command. The regime split in
notes/2026-09-21-length-regime.md is this output, cut at the time the
settings change landed.

`--by prompt` cuts by what the session was actually handed instead of by a
timestamp. Since CC 2.1.268 every process start writes a `prompt_snapshot`
attachment into the transcript carrying the full system prompt it sent, so a
closer can be tied to the snapshot before it. A timestamp cut is wrong for
any session that outlives an apply: a running CC keeps the prompt it started
with until it restarts or compacts, which for a T3 Code session was an hour
and a half after the 2026-09-21 floor landed. A group is named by which
`edits/MARKERS.txt` lines its prompt carried; the legend under the table
lists only the markers that differ between groups.
"""
import argparse
import collections
import glob
import json
import os
import re
import statistics

BOLD_BULLET = re.compile(r"(?m)^\s*(?:[-*]|\d+\.) \*\*")
ANY_BULLET = re.compile(r"(?m)^\s*(?:[-*]|\d+\.) ")
MARKERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "edits", "MARKERS.txt")


def prose_markers(path=MARKERS):
    """The markers that are prompt prose; code markers can never be in a prompt."""
    with open(path, encoding="utf-8") as fh:
        return [l.strip() for l in fh if l.strip() and not l.startswith(")")]


def snapshot_text(record):
    """The system prompt a `prompt_snapshot` attachment carries, or None."""
    a = record.get("attachment") or {}
    if record.get("type") != "attachment" or a.get("type") != "prompt_snapshot":
        return None
    sp = a.get("systemPrompt")
    parts = sp if isinstance(sp, list) else [sp]
    return "\n".join(p if isinstance(p, str) else json.dumps(p) for p in parts)


def closers(since, root=None, markers=None):
    """Every closing reply since the date, one dict each. `prompt` is the
    frozenset of markers in the last prompt snapshot before it, or None when
    the session recorded none (CC before 2.1.268)."""
    root = root or os.path.expanduser("~/.claude/projects")
    markers = prose_markers() if markers is None else markers
    out = []
    for path in glob.glob(os.path.join(root, "*", "*.jsonl")):
        groups = collections.OrderedDict()
        prompt = None
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("isSidechain"):
                    continue
                snap = snapshot_text(r)
                if snap is not None:
                    prompt = frozenset(mk for mk in markers if mk in snap)
                    continue
                if r.get("type") != "assistant":
                    continue
                if (r.get("timestamp") or "") < since:
                    continue
                m = r.get("message") or {}
                # one API response is filed as one record per content block
                g = groups.setdefault(r.get("requestId") or r.get("uuid"), {
                    "model": m.get("model"), "entrypoint": r.get("entrypoint"),
                    "version": r.get("version"), "ts": r.get("timestamp"),
                    "session": r.get("sessionId"), "prompt": prompt, "text": [], "tool": False})
                for c in m.get("content") or []:
                    if c.get("type") == "text":
                        g["text"].append(c.get("text") or "")
                    elif c.get("type") == "tool_use":
                        g["tool"] = True
        for g in groups.values():
            if g["tool"] or not g["text"] or not g["model"] or g["model"].startswith("<"):
                continue
            text = "\n".join(g["text"])
            out.append(dict(g, text=text, words=len(text.split()),
                            bold=len(BOLD_BULLET.findall(text)), bullets=len(ANY_BULLET.findall(text)),
                            project=os.path.basename(os.path.dirname(path))))
    return out


def table(rows, key):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    print(f"{'group':62} {'n':>4} {'p25':>5} {'median':>6} {'p75':>5} {'bold%':>6} {'list%':>6}")
    for k in sorted(groups, key=lambda k: tuple(map(str, k))):
        v = groups[k]
        w = sorted(x["words"] for x in v)
        print(f"{' '.join(map(str, k)):62} {len(v):4} {w[len(w) // 4]:5} {statistics.median(w):6.0f} "
              f"{w[3 * len(w) // 4]:5} {100 * sum(1 for x in v if x['bold']) // len(v):6} "
              f"{100 * sum(1 for x in v if x['bullets']) // len(v):6}")


def prompt_names(rows):
    """{frozenset of markers: short name}, and the legend lines that say what
    tells the named prompts apart. Names follow first appearance in time."""
    seen = []
    for r in sorted(rows, key=lambda r: r["ts"] or ""):
        if r["prompt"] is not None and r["prompt"] not in seen:
            seen.append(r["prompt"])
    names = {p: f"P{i + 1}" for i, p in enumerate(seen)}
    common = frozenset.intersection(*seen) if seen else frozenset()
    legend = [f"{names[p]}: {len(p)} markers" + (
        "; beyond the common set: " + ", ".join(f"'{m}'" for m in sorted(p - common))
        if p - common else "; the common set only") for p in seen]
    if seen:
        legend.insert(0, f"common to every recorded prompt: {len(common)} markers")
    return names, legend


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("since", nargs="?", default="2026-09-01", help="ISO date or timestamp (UTC)")
    ap.add_argument("--by", choices=["regime", "session", "prompt"], default="regime",
                    help="regime = model x entrypoint x CC version (default); session = one row each; "
                         "prompt = model x the markers the session's recorded system prompt carried")
    ap.add_argument("--session", help="only sessions whose id starts with this")
    ap.add_argument("--show", type=int, default=0, help="print the text of the N longest closers")
    args = ap.parse_args()
    rows = [r for r in closers(args.since)
            if not args.session or (r["session"] or "").startswith(args.session)]
    if not rows:
        print("no closing replies in range")
        return
    if args.by == "session":
        table(rows, lambda r: (r["model"], r["entrypoint"], r["session"][:8], r["project"][-24:]))
    elif args.by == "prompt":
        names, legend = prompt_names(rows)
        table(rows, lambda r: (r["model"], r["entrypoint"],
                               names.get(r["prompt"], "no snapshot")))
        print()
        for line in legend:
            print(line)
    else:
        table(rows, lambda r: (r["model"], r["entrypoint"], r["version"]))
    for r in sorted(rows, key=lambda r: -r["words"])[:args.show]:
        print(f"\n=== {r['session'][:8]} {r['ts']} {r['words']} words")
        print(r["text"])


if __name__ == "__main__":
    main()
