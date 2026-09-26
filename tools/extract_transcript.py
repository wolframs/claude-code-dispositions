#!/usr/bin/env python3
"""Render a Claude Code .jsonl session transcript as readable markdown.

Everything a human or the model actually wrote is reproduced verbatim: user
prompts, assistant text, thinking, and tool_use inputs. Only tool_result bodies
are truncated, and every truncation states the original length, so a shortened
result is never mistaken for a short one.

Turn headers name the real author — see tools/transcript.py. `USER (human)` is
[HUMAN]; `USER (harness)` is Claude Code injecting a notification or summary
under the same role; `PROMPT (parent agent)` is what a subagent was told to do.

  extract_transcript.py SESSION.jsonl [-o OUT.md] [--tool-result-chars N]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcript as T  # noqa: E402


def fence(text, lang=""):
    ticks = "```"
    while ticks in text:
        ticks += "`"
    return f"{ticks}{lang}\n{text}\n{ticks}"


def render_result(block, limit):
    body = block.get("content")
    if not isinstance(body, str):
        body = json.dumps(body, indent=2, ensure_ascii=False)
    total = len(body)
    note = " (error)" if block.get("is_error") else ""
    if limit and total > limit:
        body = body[:limit]
        note += f" [truncated: showing {limit} of {total} chars]"
    return note, body


def header(path, turns):
    meta = {}
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("type") in ("ai-title", "permission-mode"):
            meta[r["type"]] = r

    stamps = [t["ts"] for t in turns if t["ts"]]
    out = [f"# Session {path.stem}"]
    if meta.get("ai-title", {}).get("aiTitle"):
        out.append(f"**Title:** {meta['ai-title']['aiTitle']}")
    out.append(f"**Project dir:** `{path.parent.name}`")
    if T.is_subagent(path):
        out.append("**Subagent transcript** — nothing here was typed by a human.")
    if stamps:
        out.append(f"**Range:** {min(stamps)} → {max(stamps)}")
    counts = {}
    for t in turns:
        counts[t["author"]] = counts.get(t["author"], 0) + 1
    out.append("**Turns:** " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    if meta.get("permission-mode", {}).get("permissionMode"):
        out.append(f"**Permission mode:** {meta['permission-mode']['permissionMode']}")
    return out + ["\n---\n"]


def render(path, limit):
    path = Path(path)
    turns = list(T.load(path))
    out = header(path, [t for t in turns if t["role"] != "system"])

    for t in turns:
        if t["role"] == "system":
            if t["text"]:
                out.append(f"> **[system: {t['author']}]** {t['text']}\n")
            continue

        side = " · sidechain" if t["sidechain"] else ""
        out.append(f"## [{t['n']}] {T.LABELS[t['author']]} — {t['ts']}{side}\n")

        for b in t["parts"]:
            kind = b.get("type")
            if kind == "text":
                out.append(b.get("text", "") + "\n")
            elif kind == "thinking":
                out.append("**thinking:**\n")
                out.append(fence(b.get("thinking", "")) + "\n")
            elif kind == "tool_use":
                out.append(f"**tool_use `{b.get('name')}`**\n")
                out.append(fence(json.dumps(b.get("input", {}), indent=2, ensure_ascii=False), "json") + "\n")
            elif kind == "tool_result":
                note, body = render_result(b, limit)
                out.append(f"**tool_result**{note}\n")
                out.append(fence(body) + "\n")
            elif kind == "image":
                out.append("**[image omitted]**\n")
            else:
                out.append(f"**[{kind} block]**\n")

    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--tool-result-chars", type=int, default=800,
                    help="max chars per tool_result body; 0 keeps them whole")
    a = ap.parse_args()

    text = render(a.session, a.tool_result_chars)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
