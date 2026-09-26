# edits/ — the applied delta

These files are the source of truth for the prompt changes. Apply them through
`ccctl.py apply` from the machine's ccctl state directory. `tools/patch.sh` and
`tools/apply-win32.py` delegate to that same staged, verified engine.

## Current targets

`targets.json` is the list: every current target, fragment or adhoc, with its
intent, implementation and spec. `release.json` names the CC version they are
reviewed against, and `ccctl.py status --check` is the verdict on whether a
machine carries them. Do not restate counts here; they go stale within a round.

The fragments are full replacement bodies in this directory:

| Fragment | Purpose |
| --- | --- |
| `delivering-work-at-full-scope` | Operator frame, scope and autonomy |
| `outcome-first-communication-style` | Output visibility and final-message delivery |
| `communication-style` | Text-channel mechanics, including narration-loss repair |
| `correction-restraint` | Evidence-led correction without repeated self-auditing |
| `tool-description-todowrite` | Task tracking without the stock worked-example mass |
| `subagent-delegation-examples` | Delegation examples with honest asynchronous reporting |

The adhocs are derived by `plan_adhocs()` in `tools/ccctl.py` against each
build's own minified identifiers, with the reasoning for each in the comment
above its plan item. They cover text tweakcc never extracts (the reporting
floor, the Bash tool's compact git section) and gates rather than text (the
silent-turn reminder's model clauses). Historical `adhoc-<version>.json` files
are provenance, not current executable locators. `tools/build_ab.py` generates
`ab/data.json` from `targets.json`, the release's prompt map and the pristine
binary, so the review view shows the exact bytes each locator replaces.
`tools/check_repo.py` fails when the map, the reviews and the authored files
disagree.

Only changed fragments live here; everything else stays stock. Each `.md` is
a full replacement body with HTML-comment metadata. Its `ccVersion` describes
the upstream fragment revision, not the latest binary tested. Do not bump it
merely because a Claude Code release shipped. Keep declared interpolations in
the same order, or deliberately replace a whole span with static text.

`MARKERS.txt` lists substrings that must be present and `ANTIMARKERS.txt`
stock sentences that must be absent; the latter's header says how to measure a
candidate before adding it. Verification uses the active Bun sources and also
checks exact adhoc replacements; whole-file grep can find abandoned bundles and
give a false positive.

## Review acknowledgements

`reviews.json` records the exact stock/replacement pairs reviewed by an agent
or operator. Each fragment entry contains `stockSha256`, `editSha256`, and a
nonempty `reason`. Hash UTF-8 of `ccctl.stock_body(entry)` and
`ccctl.edit_text(edit_path)` respectively. These functions normalize checkout
line endings and exclude edit frontmatter. The stock hash includes named
interpolations, not just static pieces.

When analysis detects upstream drift, read the stock diff, adapt the authored
replacement if needed, then record those two hashes and the review reasoning.
A matching pair turns REVIEW into AUTO; changing either text invalidates the
acknowledgement. Re-saving a file alone does not acknowledge a review. MANUAL,
removed fragments, and missing prompt data remain blocked regardless of hashes.
`update --policy force` remains an explicit bypass for REVIEW only.

For example, after reviewing one fragment, calculate its record from the repo
root (replace `name` and the reason with the fragment and decision reviewed):

```python
import hashlib
import json
from pathlib import Path
from tools import ccctl

name = "system-prompt-communication-style"
stock = ccctl.load_snapshot("2.1.261")[name]
digest = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()
print(json.dumps({name: {
    "stockSha256": digest(ccctl.stock_body(stock)),
    "editSha256": digest(ccctl.edit_text(Path("edits") / (name + ".md"))),
    "reason": "Describe the upstream change and why this replacement preserves intent."
}}, indent=2))
```

The probe battery is retired by operator decision. Mechanical verification
establishes that the edits landed; the operator's real sessions establish
whether the disposition works. Historical A/B artifacts remain provenance.
