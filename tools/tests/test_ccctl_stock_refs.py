"""Tests for fragment bodies that keep stock spans by reference (site-prep).

Run from anywhere:  python tools/tests/test_ccctl_stock_refs.py
Temp directories only; the stock text is synthetic.

A fragment that changes a clause or two of a stock section names the spans it
keeps as `{{stock:N:H}}` instead of writing Anthropic's text out (ccctl.py,
STOCK_REF). The plan must be the one the spelled-out body gives, and an
upstream edit inside a kept span must stop the plan, not shorten it.
"""
import atexit
import hashlib
import importlib.util
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ccctl", str(HERE.parent / "ccctl.py"))
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


tmp = Path(tempfile.mkdtemp()); atexit.register(shutil.rmtree, tmp, ignore_errors=True)
FM = "<!--\nname: 'Synthetic'\n-->\n"
PIECES = ["# Section\nA first kept sentence of the synthetic section. A sentence the edit cuts. "
          "The ${", "} tool reads a file before it writes one, and a last kept clause closes it."]
ENTRY = {"id": "synthetic", "pieces": PIECES, "identifiers": [0], "identifierMap": {"0": "TOOL_NAME"}}
STOCK = cc.stock_body(ENTRY)
KEEP_A = "A first kept sentence of the synthetic section."
KEEP_B = "The ${TOOL_NAME} tool reads a file before it writes one, and a last kept clause closes it."
SPELLED = "# Section\n" + KEEP_A + " An added sentence of ours. " + KEEP_B


def fragment(name, body):
    path = tmp / (name + ".md")
    path.write_text(FM + body + "\n", encoding="utf-8")
    return path


def blob_for(pieces):
    """A bundle holding the stock template literal, with `Ab` as the minified name."""
    return b"var q=1;let s=`" + cc.enc_match(pieces[0]) + b"Ab" + cc.enc_match(pieces[1]) + b"`;q++;"


ref_body = "# Section\n" + cc.stock_ref(KEEP_A) + " An added sentence of ours. " + cc.stock_ref(KEEP_B)
spelled, by_ref = fragment("synthetic", SPELLED), fragment("synthetic-ref", ref_body)

check("stock_ref names a span by length and a 16-digit digest prefix",
      re.fullmatch(r"\{\{stock:\d+:[0-9a-f]{16}\}\}", cc.stock_ref(KEEP_A)) is not None)
check("the reference form carries none of the kept words",
      "kept sentence" not in ref_body and "reads a file" not in ref_body)
check("fragment_body resolves the references to the spelled-out body",
      cc.fragment_body(by_ref, ENTRY) == SPELLED, repr(cc.fragment_body(by_ref, ENTRY)))
check("a body without references is its own text, entry or not",
      cc.fragment_body(spelled, ENTRY) == SPELLED and cc.fragment_body(spelled, None) == SPELLED)
check("a body with references and no stock entry does not resolve",
      cc.fragment_body(by_ref, None) is None)
check("fragment_body_or_text falls back to the file's own text",
      cc.fragment_body_or_text(by_ref, None) == cc.edit_text(by_ref))

blob = blob_for(PIECES)
p_spelled = cc.plan_fragments(blob, {"synthetic": ENTRY}, [spelled])[0]
p_ref = cc.plan_fragments(blob, {"synthetic-ref": dict(ENTRY, id="synthetic-ref")}, [by_ref])[0]
check("the spelled-out body plans AUTO", p_spelled["status"] == "AUTO", p_spelled)
check("the reference body plans the same bytes as the spelled-out one",
      p_ref["status"] == "AUTO" and (p_ref["old"], p_ref["new"]) == (p_spelled["old"], p_spelled["new"]),
      p_ref.get("reason"))
check("the minified name is spliced back around the reference's ${VAR}",
      b"The ${Ab} tool reads" in p_ref.get("new", b""))

# Upstream edits one character inside a kept span: the reference must not
# resolve, and the plan must say so rather than install a shortened body.
EDITED = [PIECES[0].replace("first kept", "first-kept"), PIECES[1]]
entry2 = {"id": "synthetic-ref", "pieces": EDITED, "identifiers": [0], "identifierMap": {"0": "TOOL_NAME"}}
check("an upstream edit inside a kept span leaves the body unresolved",
      cc.fragment_body(by_ref, entry2) is None)
p_edit = cc.plan_fragments(blob_for(EDITED), {"synthetic-ref": entry2}, [by_ref])[0]
check("…and the plan is MANUAL, naming the reference", p_edit["status"] == "MANUAL"
      and "by reference" in p_edit.get("reason", "") and "new" not in p_edit, p_edit)

digest = lambda t: hashlib.sha256(t.encode("utf-8")).hexdigest()
review = {"stockSha256": digest(STOCK), "editSha256": digest(SPELLED), "reason": "synthetic"}
check("a review of the spelled-out pair covers the reference form",
      cc.fragment_review_matches(review, ENTRY, by_ref))
check("…and not once upstream has edited a kept span",
      not cc.fragment_review_matches(dict(review, stockSha256=digest(cc.stock_body(entry2))), entry2, by_ref))

# The repository's own fragments: every `{{stock` is a well-formed reference,
# so a mistyped one cannot reach a prompt as text.
for f in sorted((HERE.parents[1] / "edits").glob("*.md")):
    text = cc.edit_text(f)
    stray = text.count("{{stock") - len(cc.STOCK_REF.findall(text))
    check(f"edits/{f.name}: every stock reference is well-formed", stray == 0, f"{stray} malformed")

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
