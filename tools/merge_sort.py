#!/usr/bin/env python3
"""Merge the three independent sorting passes into one coded set.

Three agents coded the same 177 complaints against the same codebook without
seeing each other. Where they agree the assignment is worth something; where
they split, the codebook is the thing at fault, not the entry, so disagreement
is reported per code rather than averaged away.

Consensus primary = the primary code at least two of three chose. No majority
means CONTESTED, which is a finding about the code boundary.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SORT = ROOT / "notes" / "taxonomy" / "sort"
TURNS = ROOT / "corpus" / "pushbacks" / "human-turns.jsonl"
OUT = ROOT / "notes" / "taxonomy" / "10-sorted.md"


def read_pass(path):
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        codes = [c.strip() for c in cols[1].split(",") if c.strip()]
        if not codes:
            continue
        conf = ""
        reason = ""
        if len(cols) > 2:
            c = cols[2].strip()
            conf = c.split(":", 1)[1] if c.lower().startswith("confidence") else c
        if len(cols) > 3:
            reason = cols[3].strip()
        rows[cols[0].strip()] = {"codes": codes, "conf": conf, "reason": reason}
    return rows


def main():
    passes = {p.stem: read_pass(p) for p in sorted(SORT.glob("pass-*.tsv"))}
    if not passes:
        print("no passes found")
        return

    ids = sorted(set().union(*(set(p) for p in passes.values())))
    turns = {json.loads(l)["id"]: json.loads(l) for l in TURNS.open(encoding="utf-8")}

    consensus = {}
    unanimous = split = contested = 0
    per_code = Counter()
    any_mention = Counter()
    disagree_partners = defaultdict(Counter)

    for i in ids:
        primaries = [p[i]["codes"][0] for p in passes.values() if i in p]
        votes = Counter(primaries)
        top, n = votes.most_common(1)[0]
        if n == len(passes):
            unanimous += 1
            verdict = top
        elif n >= 2:
            split += 1
            verdict = top
        else:
            contested += 1
            verdict = "CONTESTED"

        if verdict != "CONTESTED":
            per_code[verdict] += 1
        if len(votes) > 1:
            for a in votes:
                for b in votes:
                    if a < b:
                        disagree_partners[a][b] += 1
                        disagree_partners[b][a] += 1

        for p in passes.values():
            if i in p:
                for c in p[i]["codes"]:
                    any_mention[c] += 1

        consensus[i] = {
            "primary": verdict,
            "votes": dict(votes),
            "agreement": f"{n}/{len(passes)}",
            "reasons": [p[i]["reason"] for p in passes.values() if i in p and p[i]["reason"]],
        }

    total = len(ids)
    md = [
        "# Sorted complaints",
        "",
        "Three independent passes over the same 177 complaints and the same codebook,",
        "merged by `tools/merge_sort.py`. Consensus primary = the code at least two of",
        "three chose; no majority is `CONTESTED`, which is a fact about the code",
        "boundary rather than about the entry.",
        "",
        f"**{total} entries.** {unanimous} unanimous ({unanimous * 100 // total}%), "
        f"{split} two-of-three, {contested} contested.",
        "",
        "## Per code, by consensus primary",
        "",
        "| Code | Primary | Mentioned (any pass, any position) |",
        "| --- | --- | --- |",
    ]
    for code, n in per_code.most_common():
        md.append(f"| `{code}` | {n} | {any_mention[code]} |")
    for code, n in sorted(any_mention.items()):
        if code not in per_code:
            md.append(f"| `{code}` | 0 | {n} |")

    md += ["", "## Where the coders split", "",
           "Code pairs the passes confused for each other. A high number means the two",
           "definitions do not separate cleanly in practice.", "",
           "| Pair | Times confused |", "| --- | --- |"]
    pairs = Counter()
    for a, others in disagree_partners.items():
        for b, n in others.items():
            if a < b:
                pairs[(a, b)] = n
    for (a, b), n in pairs.most_common(12):
        md.append(f"| `{a}` ↔ `{b}` | {n} |")

    contested_ids = [i for i in ids if consensus[i]["primary"] == "CONTESTED"]
    md += ["", "## Contested entries", "",
           "Three coders, three different primaries. Read these before trusting the codebook.", ""]
    for i in contested_ids:
        v = ", ".join(f"`{c}`" for c in consensus[i]["votes"])
        md.append(f"- **{i}** — {v}")
    if not contested_ids:
        md.append("- none")

    md += ["", "## Full assignment", "", "| ID | Consensus primary | Agreement | Session |",
           "| --- | --- | --- | --- |"]
    for i in ids:
        c = consensus[i]
        sess = turns.get(i, {}).get("session", "")
        md.append(f"| {i} | `{c['primary']}` | {c['agreement']} | `{sess}` |")

    OUT.write_text("\n".join(md), encoding="utf-8")
    (SORT / "consensus.json").write_text(json.dumps(consensus, indent=2), encoding="utf-8")

    print(f"{total} entries | {unanimous} unanimous, {split} 2-of-3, {contested} contested")
    for code, n in per_code.most_common():
        print(f"  {n:4d}  {code}")


if __name__ == "__main__":
    main()
