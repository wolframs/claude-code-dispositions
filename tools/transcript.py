"""Shared parsing for Claude Code .jsonl session transcripts.

The one thing worth getting right here is *who wrote a turn*. Claude Code files
several kinds of record under the `user` role that [HUMAN] never typed: tool
results, compaction summaries, task notifications, slash-command echoes,
interrupt markers. In a subagent transcript the `user` role is the parent
agent's prompt, so nothing in one is human at all. Mining complaints from a
corpus that conflates these produces a taxonomy of the harness, not of Claude.
"""

import json
import re
from pathlib import Path

# Author classes for a record filed under the `user` role.
HUMAN = "human"          # [HUMAN] typed it
HARNESS = "harness"      # Claude Code injected it
AGENT = "agent-prompt"   # a parent agent wrote it, in a subagent transcript
TOOL = "tool-result"     # a tool returned it

IMAGE_REF = re.compile(r"\[Image:\s*source:[^\]]*\]")
SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)

HARNESS_OPENERS = (
    "<local-command",
    "<command-name>",
    "<user-prompt-submit-hook>",
    "[Request interrupted",
    "This session is being continued",
)


def is_subagent(path):
    return "subagents" in Path(path).parts


def text_of(parts):
    return "".join(b.get("text", "") for b in parts if b.get("type") == "text").strip()


def classify_user(record, parts, subagent):
    """Who actually authored this `user` record."""
    if not any(b.get("type") != "tool_result" for b in parts):
        return TOOL
    if subagent:
        return AGENT
    if record.get("isCompactSummary"):
        return HARNESS

    body = text_of(parts)
    if "<task-notification>" in body:
        return HARNESS
    if body.startswith(HARNESS_OPENERS):
        return HARNESS

    # An attachment-only turn carries no complaint, whoever pasted it.
    residue = SYSTEM_REMINDER.sub("", IMAGE_REF.sub("", body)).strip()
    return HUMAN if residue else HARNESS


def blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content or []


def load(path):
    """Yield turn dicts in file order. Non-conversational records are dropped."""
    path = Path(path)
    subagent = is_subagent(path)
    n = 0
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        kind = record.get("type")
        if kind == "system" and record.get("subtype") in ("away_summary", "stop_hook_summary"):
            yield {"role": "system", "author": record["subtype"], "n": None,
                   "ts": record.get("timestamp", ""), "parts": [],
                   "text": record.get("content") or ""}
            continue
        if kind not in ("user", "assistant"):
            continue

        parts = blocks(record.get("message", {}).get("content"))
        n += 1
        author = ("assistant" if kind == "assistant"
                  else classify_user(record, parts, subagent))
        yield {
            "role": kind,
            "author": author,
            "n": n,
            "ts": record.get("timestamp", ""),
            "parts": parts,
            "text": text_of(parts),
            "sidechain": bool(record.get("isSidechain")),
        }


LABELS = {
    "assistant": "ASSISTANT",
    HUMAN: "USER (human)",
    HARNESS: "USER (harness)",
    AGENT: "PROMPT (parent agent)",
    TOOL: "TOOL RESULT",
}
