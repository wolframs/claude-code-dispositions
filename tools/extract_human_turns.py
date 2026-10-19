#!/usr/bin/env python3
"""Extract every turn [HUMAN] actually typed, paired with what Claude just said.

There is no keyword filter here, on purpose. An earlier version scored candidate
complaints with a regex and it cut 755 turns to 38 while dropping the strongest
evidence in the corpus — including "this is a clear feedback-goes-to-anthropic
transcript ... not taking a hint is crazy ridiculous". A keyword net finds
complaints phrased the way the net's author imagined them. Extraction is this
script's job; judging what is a complaint is a reader's.

Only top-level sessions are read, and only turns classified HUMAN: subagent
prompts, task notifications, compaction summaries and slash-command echoes are
all filed under the `user` role and none of them are [HUMAN].

Writes corpus/pushbacks/HUMAN-TURNS.md and human-turns.jsonl, plus per-slice
markdown under corpus/pushbacks/slices/ for parallel review.

The same read over live transcripts, for a period the corpus has not caught up
with, without touching the committed set:

    extract_human_turns.py --src ~/.claude/projects --since 2026-08-19T20:20 \
        --exclude probe-runs --exclude=-tmp --exclude ccctl --prefix PT --out /tmp/post

`--since` is compared with the turn's UTC timestamp. Give such a run its own
`--prefix`, because the HT numbers are cited from spec/ and notes/.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcript as T  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "corpus" / "raw"
OUT = ROOT / "corpus" / "pushbacks"

ASSISTANT_CHARS = 900


def prose(parts):
    return "\n".join(b.get("text", "") for b in parts if b.get("type") == "text").strip()


def clip(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [clipped, {len(text)} chars total]"


def collect(src=RAW, since="", exclude=(), prefix="HT", until=""):
    records = []
    for path in sorted(Path(src).glob("*/*.jsonl")):
        if any(x in path.parent.name for x in exclude):
            continue
        turns = [t for t in T.load(path) if t["role"] != "system"]
        for i, t in enumerate(turns):
            if (t["ts"] or "") < since or (until and (t["ts"] or "") >= until):
                continue
            embedded = T.embedded_human(t["text"]) if t["author"] == T.ORCHESTRATOR else None
            if t["author"] != T.HUMAN and embedded is None:
                continue
            body = embedded[0] if embedded else T.SYSTEM_REMINDER.sub("", t["text"]).strip()
            if not body:
                continue
            prior = ""
            model = ""
            for j in range(i - 1, max(i - 8, -1), -1):
                if turns[j]["author"] == "assistant":
                    model = model or turns[j]["model"]
                    said = prose(turns[j]["parts"])
                    if said:
                        prior = said
                        break
            records.append({
                "session": f"{path.parent.name}/{path.stem}",
                "turn": t["n"],
                "ts": t["ts"],
                # the model that answered the turn he is reacting to; "" at a
                # session opening, "<synthetic>" when the harness spoke
                "model": model,
                "assistant_before": clip(prior, ASSISTANT_CHARS),
                "human": body,
                "human_source": embedded[1] if embedded else "typed",
            })

    records.sort(key=lambda r: (r["ts"], r["session"], r["turn"]))
    unique = {}
    for record in records:
        key = record["ts"], record["human"]
        occurrence = {k: record[k] for k in ("session", "turn", "model", "human_source")}
        if key not in unique:
            record["occurrences"] = [occurrence]
            unique[key] = record
        else:
            kept = unique[key]
            kept["occurrences"].append(occurrence)
            if not kept["assistant_before"] and record["assistant_before"]:
                kept["assistant_before"] = record["assistant_before"]
                kept["model"] = record["model"]
    records = list(unique.values())
    for i, r in enumerate(records, 1):
        r["id"] = f"{prefix}-{i:03d}"
    return records


def entry(r):
    return [
        f"## {r['id']} — `{r['session']}` turn {r['turn']} — {r['ts'][:16]}"
        + (f" — {r['model']}" if r.get("model") else ""),
        "",
        "**Claude, immediately before:**",
        "",
        "> " + (r["assistant_before"] or "*(session opening — no prior turn)*").replace("\n", "\n> "),
        "",
        "**[HUMAN]:**",
        "",
        "> " + r["human"].replace("\n", "\n> "),
        "",
        "---",
        "",
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slices", type=int, default=6, help="how many review slices to write")
    ap.add_argument("--src", default=str(RAW), help="transcript root (default: corpus/raw)")
    ap.add_argument("--since", default="", help="only turns at or after this UTC timestamp")
    ap.add_argument("--until", default="", help="only turns before this UTC timestamp (exclusive)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="skip project directories whose name contains this (repeatable)")
    ap.add_argument("--prefix", default="HT", help="id prefix (default HT, the committed set)")
    ap.add_argument("--out", default=str(OUT), help="output directory (default: corpus/pushbacks)")
    a = ap.parse_args()

    out_dir = Path(a.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    records = collect(Path(a.src).expanduser(), a.since, a.exclude, a.prefix, a.until)

    with (out_dir / "human-turns.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    intro = [
        "# Human turns",
        "",
        "Generated by `tools/extract_human_turns.py`. Entries are typed turns or",
        "owner messages lifted from recognized Telegram envelopes. Machine inputs",
        "are excluded, duplicates retain their source occurrences, and human words",
        "are never clipped. Unfiltered: most of these are ordinary",
        "instructions, not complaints.",
        "",
        f"**{len(records)} turns.**",
        "",
        "---",
        "",
    ]
    body = []
    for r in records:
        body += entry(r)
    (out_dir / "HUMAN-TURNS.md").write_text("\n".join(intro + body), encoding="utf-8")

    slices = out_dir / "slices"
    slices.mkdir(exist_ok=True)
    for old in slices.glob("*.md"):
        old.unlink()
    size = -(-len(records) // a.slices)
    for n in range(a.slices):
        chunk = records[n * size:(n + 1) * size]
        if not chunk:
            continue
        head = [
            f"# Human turns — slice {n + 1} of {a.slices}",
            "",
            f"{chunk[0]['id']} … {chunk[-1]['id']} ({len(chunk)} turns), chronological.",
            "",
            "---",
            "",
        ]
        out = []
        for r in chunk:
            out += entry(r)
        (slices / f"slice-{n + 1}.md").write_text("\n".join(head + out), encoding="utf-8")

    print(f"{len(records)} human turns → {a.slices} slices")


if __name__ == "__main__":
    main()
