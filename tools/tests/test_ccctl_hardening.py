"""Regression tests for the 2026-08-21 adversarial-review fixes on ccctl.py.

Run from anywhere:  python tools/tests/test_ccctl_hardening.py
No real binaries are touched; fake blobs and a temp sandbox only.
Each check names the review finding it guards.
"""
import hashlib, importlib.util, os, sys, tempfile, shutil
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

# --- finding 4: version-name filter (the sort/filter logic in claude_binary)
import re
names = ["2.1.9", "2.1.233", "2.1.238", "2.1.238.stock", "2.1.238.pre-swap", "2.1.238.new"]
kept = [n for n in names if re.fullmatch(r"\d+(\.\d+)+", n)]
check("finding4: .stock/.pre-swap/.new filtered from version candidates",
      kept == ["2.1.9", "2.1.233", "2.1.238"], str(kept))
ordered = sorted(kept, key=lambda p: [int(x) for x in p.split(".")])
check("finding4: numeric version ordering (2.1.9 < 2.1.233)",
      ordered == ["2.1.9", "2.1.233", "2.1.238"], str(ordered))

# --- finding 8: empty markers
check("finding8: binary_contains([]) returns empty set, no O(n^2)",
      cc.binary_contains(__file__, []) == set())
check("finding8: is_stock with no markers is False (never trust as pristine)",
      cc.is_stock(Path(__file__), []) is False)

# --- finding 18: platform detection
check("finding18: cc_platform returns a known key",
      cc.cc_platform() in ("win32-x64", "win32-arm64", "linux-x64", "linux-arm64",
                           "linux-x64-musl", "linux-arm64-musl", "darwin-x64", "darwin-arm64"),
      cc.cc_platform())

# --- finding 15: run() tolerates timeout/OSError instead of raising
r = cc.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
check("finding15: run() converts TimeoutExpired to rc=124", r.returncode == 124, str(r))

# --- finding 12: gap bound + overlap detection in plan_fragments
blob = (b"AAA" + b"HEAD-UNIQUE-ANCHOR-ONE" + b"x" * 500 + b"TAIL-UNIQUE-ONE" + b"BBB")
class FakeEdit:
    def __init__(self, stem, body): self.stem, self._b = stem, body
    @property
    def name(self): return self.stem + ".md"
    def read_bytes(self): return self._b.encode()
data = {"frag": {"pieces": ["HEAD-UNIQUE-ANCHOR-ONE", "TAIL-UNIQUE-ONE"]}}
plan = cc.plan_fragments(blob, data, [FakeEdit("frag", "replacement text")])
check("finding12: 500-byte gap rejected as mis-anchored",
      plan[0]["status"] == "MANUAL" and "mis-anchored" in plan[0]["reason"], str(plan[0]))

# small gap accepted
blob2 = b"AAA" + b"HEAD-UNIQUE-ANCHOR-TWO" + b"r" + b"TAIL-UNIQUE-TWO" + b"BBB"
data2 = {"frag": {"pieces": ["HEAD-UNIQUE-ANCHOR-TWO", "TAIL-UNIQUE-TWO"]}}
plan2 = cc.plan_fragments(blob2, data2, [FakeEdit("frag", "new body")])
check("finding12: 1-byte gap accepted as AUTO", plan2[0]["status"] == "AUTO", str(plan2[0]))

# overlap detection
blob3 = b"P1-ANCHOR" + b"m" + b"P2-END" + b"m" + b"P3-TAIL"
data3 = {"a": {"pieces": ["P1-ANCHOR", "P2-END"]}, "b": {"pieces": ["P2-END", "P3-TAIL"]}}
# spans: a = [0, len(P1)+1+len(P2)), b starts inside a's end piece -> disjoint actually;
# construct a true overlap: b's span starts before a's end
data4 = {"a": {"pieces": ["P1-ANCHOR", "P3-TAIL"]}, "b": {"pieces": ["P2-END"]}}
plan4 = cc.plan_fragments(blob3, data4, [FakeEdit("a", "x"), FakeEdit("b", "y")])
overlapped = [p for p in plan4 if p["status"] == "MANUAL" and "overlap" in p.get("reason", "")]
check("finding12: overlapping spans both flagged MANUAL", len(overlapped) == 2,
      str([(p["name"], p["status"], p.get("reason", "")[:40]) for p in plan4]))

# --- C-05: build_replacement must re-emit the braces around a spliced gap.
# tweakcc splits at identifier boundaries, so `${` and `}` live in the stock
# pieces; an edit's ${VAR} token eats both. Splicing the bare gap shipped
# "one call to the Il tool" and "Ri({" into the live prompt for months.
check("C-05: ${VAR} splice re-emits ${gap}",
      cc.build_replacement("call the ${EDIT_TOOL_NAME} tool", [b"Il"])
      == b"call the ${Il} tool",
      repr(cc.build_replacement("call the ${EDIT_TOOL_NAME} tool", [b"Il"])))
check("C-05: two gaps, both braced",
      cc.build_replacement("a ${X} b ${Y} c", [b"Ri", b"Ri"]) == b"a ${Ri} b ${Ri} c",
      repr(cc.build_replacement("a ${X} b ${Y} c", [b"Ri", b"Ri"])))
check("C-05: no-${VAR} body is whole-span static, gaps discarded",
      cc.build_replacement("plain prose", [b"r", b"r"]) == b"plain prose")
check("C-05: literal ${ in a static body is escaped, never emitted raw",
      cc.build_replacement("cost is ${5}", []) == b"cost is \\${5}",
      repr(cc.build_replacement("cost is ${5}", [])))
check("C-05: ${VAR} count mismatch is MANUAL, not a silent splice",
      cc.build_replacement("one ${A} here", [b"r", b"r"]) is None)
# A ternary edit falls to the whole-span path: `${V?"a":"b"}` does not match the
# token regex, so the body is emitted as static text with `${` escaped — which
# ships the scaffolding to the reader. This is the defect, pinned as behaviour
# so nobody re-authors an interpolated body and assumes it works: the fix is to
# flatten such a fragment to unconditional prose, not to write ${...} in it.
check("C-05: ternary body silently falls to escaped whole-span static",
      cc.build_replacement('x ${V?"a":"b"} y', [b"r"]) == b'x \\${V?"a":"b"} y',
      repr(cc.build_replacement('x ${V?"a":"b"} y', [b"r"])))

# --- C-05: an edit body that keeps a ternary must be REFUSED, not silently
# shipped as escaped scaffolding. plan_fragments is the gate.
blobT = b"ZZZ" + b"HEAD-ANCHOR-TERNARY-Q" + b"r" + b"TAIL-ANCHOR-TERNARY-Q" + b"ZZZ"
dataT = {"frag": {"pieces": ["HEAD-ANCHOR-TERNARY-Q", "TAIL-ANCHOR-TERNARY-Q"]}}
planT = cc.plan_fragments(blobT, dataT, [FakeEdit("frag", 'text ${V?"a":"b"} more')])
check("C-05: ternary edit body -> MANUAL, never a silent escaped splice",
      planT[0]["status"] == "MANUAL" and "scaffolding" in planT[0].get("reason", ""),
      str(planT[0]))
planOK = cc.plan_fragments(blobT, dataT, [FakeEdit("frag", "flat ${VAR} prose")])
check("C-05: a bare ${VAR} body is still AUTO and braces the gap",
      planOK[0]["status"] == "AUTO" and planOK[0]["new"] == b"flat ${r} prose",
      str(planOK[0].get("new")))

# --- anti-markers: MARKERS.txt proves our text went in; ANTIMARKERS.txt proves
# stock text came out. Nothing checked the second half before 2026-08-25.
tmpA = Path(tempfile.mkdtemp())
try:
    (tmpA / "edits").mkdir()
    cfgA = {"repoPath": str(tmpA)}
    check("antimarkers: absent file is an empty list, never an error",
          cc.repo_antimarkers(cfgA) == [])
    (tmpA / "edits" / "ANTIMARKERS.txt").write_text(
        "# a comment line\n\nAssume users can't see\n   To leave something undone   \n",
        encoding="utf-8")
    got = cc.repo_antimarkers(cfgA)
    check("antimarkers: comments and blanks dropped, entries stripped",
          got == ["Assume users can't see", "To leave something undone"], str(got))
    (tmpA / "edits" / "MARKERS.txt").write_text("kept marker\n", encoding="utf-8")
    check("antimarkers: markers and anti-markers are separate lists",
          cc.repo_markers(cfgA) == ["kept marker"], str(cc.repo_markers(cfgA)))
finally:
    shutil.rmtree(tmpA, ignore_errors=True)

# the shipped file must itself obey its own rule: every entry non-empty prose
_repo = Path(__file__).resolve().parent.parent.parent
_anti = cc.repo_antimarkers({"repoPath": str(_repo)})
check("antimarkers: the shipped ANTIMARKERS.txt parses and is non-empty",
      len(_anti) >= 1 and all(len(a) > 10 for a in _anti), str(_anti))

# --- finding 7: adhoc anchor ambiguity
dup = (b"function aa(e){let t=bb()?.tengu_heron_brook;"
       b"function cc(e){let t=dd()?.tengu_heron_brook;")
p = cc.plan_adhocs(dup)
check("finding7: duplicate adhoc anchor -> MANUAL",
      p[0]["status"] == "MANUAL" and "2x" in p[0]["reason"], str(p[0]))

# --- safe_put atomicity + finding 1/3: os.replace over existing
tmp = Path(tempfile.mkdtemp())
try:
    src, dst = tmp / "src", tmp / "dst"
    src.write_bytes(b"NEW"); dst.write_bytes(b"OLD")
    cc.safe_put(src, dst)
    check("safe_put: replaces existing file", dst.read_bytes() == b"NEW")
    check("safe_put: leaves no .part", not (tmp / "dst.part").exists())
    check("safe_put: copy keeps source", src.exists())
    src2 = tmp / "src2"; src2.write_bytes(b"MOVED")
    cc.safe_put(src2, dst, may_move=True)
    check("safe_put(may_move): consumes source", dst.read_bytes() == b"MOVED" and not src2.exists())

    # os.replace over an existing file (the finding-3 fix) works on this OS
    a, b = tmp / "a", tmp / "b"
    a.write_bytes(b"A"); b.write_bytes(b"B")
    os.replace(a, b)
    check("finding3: os.replace over existing file succeeds", b.read_bytes() == b"A")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# --- finding 5: base_missing surfaces (structural check on the report writer)
import inspect
srcs = inspect.getsource(cc.analyze_core)
check("finding5: base_missing forces a REVIEW verdict entry",
      "base_missing" in srcs and '"(base-snapshot)"' in srcs)
check("finding5: report carries baseSnapshot flag", '"baseSnapshot"' in srcs)

# --- finding 6: cc_version uses claude_binary
check("finding6: cc_version resolves via claude_binary",
      "claude_binary()" in inspect.getsource(cc.cc_version)
      and 'shutil.which("claude")' not in inspect.getsource(cc.cc_version))

# --- finding 17: planTotal from the report, not hardcoded
check("finding17: cmd_update uses report planTotal",
      'report["planTotal"]' in inspect.getsource(cc.cmd_update))

# --- finding 11: state written before post_verify
u = inspect.getsource(cc.cmd_update)
check("finding11: state saved before post_verify",
      u.index("save_json(STATE, state)") < u.index("post_verify(cfg, target)"))

# --- finding 14: rmtree instead of unlink loop
check("finding14: staging cleanup uses rmtree", "shutil.rmtree(STAGING" in u)

# --- finding 9: pristine_source knows .stock
check("finding9: pristine_source checks versions/<ver>.stock",
      '".stock"' in inspect.getsource(cc.pristine_source))

# --- finding 2: macOS re-signing carries identity + entitlements forward.
# NB: --preserve-metadata=entitlements was the ORIGINAL fix and is WRONG —
# measured on real arm64 hardware 2026-08-21 it yielded 0 of 5 entitlements,
# because tweakcc strips and ad-hoc re-signs during repack, leaving nothing
# to preserve. Both must be read off the pristine binary instead.
m = inspect.getsource(cc.macos_resign)
check("finding2: entitlements read from the PRISTINE binary",
      '"-d", "--entitlements"' in m and "pristine" in m)
check("finding2: entitlements passed explicitly", '"--entitlements", str(entfile)' in m)
check("finding2: stock identifier carried forward", '"--identifier", ident' in m)
body = m.split('"""')[2] if m.count('"""') >= 2 else m   # skip the docstring, which
check("finding2: does NOT rely on --preserve-metadata",   # explains why not to use it
      "--preserve-metadata" not in body)
check("finding2: codesign --verify --deep --strict", '"--verify", "--deep", "--strict"' in m)
check("finding2: post-sign check dies on dropped entitlements",
      "lost" in m and "nothing was swapped" in m)
check("finding2: assemble routes macOS signing through macos_resign",
      "macos_resign(" in inspect.getsource(cc.assemble))

# --- finding 13: uniqueness re-asserted between writes
check("finding13: span_apply re-counts before each write",
      "live_blob(target).count(item" in inspect.getsource(cc.span_apply))

# --- linux orphaned bundle copies (2026-08-24)
# tweakcc's repackELFSection appends a fresh .bun and abandons the old one, so
# the file accumulates stale copies of every string we search for. Synthesize
# that shape rather than trusting a 600 MB binary to still be around.
import struct

def fake_elf(path, payload, orphans=0, trailer=cc.BUN_TRAILER, elf=True):
    """ELF64 whose .bun holds [u64 len][payload][trailer], preceded by
    `orphans` abandoned copies of the same bytes."""
    shstr = b"\0.bun\0.shstrtab\0"
    section = struct.pack("<Q", len(payload) + len(trailer)) + payload + trailer
    head = (b"\x7fELF\x02\x01\x01" + bytes(9) if elf else b"MZ" + bytes(14))
    head += struct.pack("<HHIQ", 2, 0x3e, 1, 0)              # type, machine, version, entry
    dead = section * orphans
    bunoff = 64 + len(dead)
    stroff = bunoff + len(section)
    shoff = stroff + len(shstr)
    head += struct.pack("<QQ", 0, shoff)                      # e_phoff, e_shoff
    head += struct.pack("<IHHHHHH", 0, 64, 0, 0, 64, 3, 2)    # flags, ehsize, ph*, sh*, shnum, shstrndx
    def shdr(name, off, size):
        return struct.pack("<IIQQQQIIQQ", name, 1, 0, 0, off, size, 0, 0, 1, 0)
    Path(path).write_bytes(head + dead + section + shstr
                           + shdr(0, 0, 0) + shdr(1, bunoff, len(section)) + shdr(6, stroff, len(shstr)))
    return bunoff, bunoff + len(section)

tmp = Path(tempfile.mkdtemp())
body = b"x" * 4096 + b"UNIQUE-MARKER-TEXT" + b"y" * 4096
want = fake_elf(tmp / "clean", body)
check("bun_window: locates .bun in a well-formed ELF",
      cc.bun_window(tmp / "clean") == want, str(cc.bun_window(tmp / "clean")))
check("live_blob: returns exactly the bundle, not the file",
      cc.live_blob(tmp / "clean") == open(tmp / "clean", "rb").read()[want[0]:want[1]])

fake_elf(tmp / "orphaned", body, orphans=3)
raw = open(tmp / "orphaned", "rb").read()
check("orphans: the stale copies really are in the file",
      raw.count(b"UNIQUE-MARKER-TEXT") == 4, str(raw.count(b"UNIQUE-MARKER-TEXT")))
check("live_blob: sees one copy despite 3 orphans",
      cc.live_blob(tmp / "orphaned").count(b"UNIQUE-MARKER-TEXT") == 1)
check("binary_contains(live=True): confined to the live bundle",
      cc.binary_contains(tmp / "orphaned", ["UNIQUE-MARKER-TEXT"]) == {"UNIQUE-MARKER-TEXT"})
check("binary_contains(live=False): still whole-file",
      cc.binary_contains(tmp / "orphaned", ["UNIQUE-MARKER-TEXT"], live=False) == {"UNIQUE-MARKER-TEXT"})
# the case that actually bit us: text present ONLY in an abandoned copy must
# not count as patched. Plant a token inside the first orphan, nowhere else.
win = cc.bun_window(tmp / "orphaned")
raw = bytearray(open(tmp / "orphaned", "rb").read())
raw[200:200 + 14] = b"ONLY-IN-ORPHAN"
assert 200 + 14 < win[0], "token must land outside the live window"
(tmp / "orphaned").write_bytes(raw)
check("binary_contains: text only in an orphan does NOT read as live",
      cc.binary_contains(tmp / "orphaned", ["ONLY-IN-ORPHAN"]) == set())
check("binary_contains: that same text IS visible whole-file",
      cc.binary_contains(tmp / "orphaned", ["ONLY-IN-ORPHAN"], live=False) == {"ONLY-IN-ORPHAN"})

# refuse to guess rather than half-parse: no trailer, and not an ELF at all
fake_elf(tmp / "notrailer", body, trailer=b"\0" * 16)
check("bun_window: None when the Bun trailer is absent",
      cc.bun_window(tmp / "notrailer") is None)
check("live_blob: falls back to the whole file when the window is unknown",
      cc.live_blob(tmp / "notrailer") == open(tmp / "notrailer", "rb").read())
fake_elf(tmp / "notelf", body, elf=False)
check("bun_window: None for a non-ELF (win32/macOS keep whole-file behaviour)",
      cc.bun_window(tmp / "notelf") is None)
(tmp / "tiny").write_bytes(b"not a binary")
check("bun_window: None for junk, no exception",
      cc.bun_window(tmp / "tiny") is None)
shutil.rmtree(tmp, ignore_errors=True)

# --- 2.1.245: `$` is legal in a JS identifier and the minifier emits it.
# The anchors used \w+, which cannot match `$r`, so a byte-identical block read
# as "upstream rewrote this" (a false MANUAL that only a hand-grep could clear).
#
# The fixture is synthetic, and deliberately so. ccctl locates this block by
# digest rather than by quoting it, so the test does not need Anthropic's fifty
# words either — any body of the same shape serves, once the expected digest is
# swapped for this one's. Everything under test here is shape: the `$r`
# identifier, the order the interpolations come back in, and the byte run the
# patch replaces.
shell_body = (b"A stand-in for the stock bypass-mode shell block, written here rather than "
              b"transcribed. Same shape: one template literal, five interpolations, the shell "
              b"tool ${qn} first, the dedicated ${Ln}, ${$r} and ${io} after it, and ${qn} once "
              b"more at the end.")
shell_block = b"x,s=`" + shell_body + b"`"
# The real digest must NOT match this text. Failing closed on rewritten prose is
# the whole reason the locator is a digest.
_untouched = {i["name"]: i for i in cc.plan_adhocs(shell_block)}["bypass-auto-shell-block-invert"]
check("shell block: prose that is not the stock block fails closed",
      _untouched["status"] == "MANUAL", _untouched.get("status", ""))
_saved = (cc.SHELL_BLOCK_SHA256, cc.SHELL_BLOCK_NORM_LEN)
_norm = re.sub(rb"\$\{[^{}]*\}", b"${}", shell_body)
cc.SHELL_BLOCK_SHA256 = hashlib.sha256(_norm).hexdigest()
cc.SHELL_BLOCK_NORM_LEN = len(_norm)
try:
    shell = {i["name"]: i for i in cc.plan_adhocs(shell_block)}["bypass-auto-shell-block-invert"]
finally:
    cc.SHELL_BLOCK_SHA256, cc.SHELL_BLOCK_NORM_LEN = _saved
check("2.1.245: $-prefixed identifier ($r) still anchors the shell block",
      shell["status"] == "AUTO", shell.get("reason", ""))
check("2.1.245: $-identifiers carried through into the replacement",
      shell.get("new", b"").count(b"${$r}") == 1 and b"${qn}" in shell.get("new", b""),
      str(shell.get("new", b""))[:120])
# 2.1.280: Opus 5.5's model default selects a second, relaxed shell block.
# The old h= patch stays installed but the delivered SDK prompt reads y=.
# Leave explicit client-data and GrowthBook choices alone while cutting the
# model fallback that selects y= without either arm.
model_shell = (b'function $g(e){return cap(e,"opus_5_5_prompt_bundle")===!0}'
               b'function $sel(){return V(T()?.[D])??($g(A(B(C())))?"relaxed":void 0)'
               b'??V(flag(D,null))??"strict"}'
               b'class X{bashFirstSteerVariant=Yo($sel)}')
model_cut = {i["name"]: i for i in cc.plan_adhocs(model_shell)}["shell-steer-model-default-cut"]
check("2.1.280: model-default shell fallback is AUTO with its predicate and owner",
      model_cut["status"] == "AUTO", model_cut.get("reason", ""))
check("2.1.280: model cut retains both explicit arms and strict fallback",
      b'V(T()?.[D])??V(flag(D,null))??"strict"' in model_cut.get("new", b"")
      and b'?"relaxed":void 0' not in model_cut.get("new", b""))
unowned_shell = model_shell.replace(b'bashFirstSteerVariant=Yo($sel)', b'other=Yo($sel)')
unowned_cut = {i["name"]: i for i in cc.plan_adhocs(unowned_shell)}["shell-steer-model-default-cut"]
check("2.1.280: an unowned shell selector is MANUAL, not a quiet skip",
      unowned_cut["status"] == "MANUAL", unowned_cut.get("reason", ""))
check("shell block: the replaced run starts at the assignment target, not the backtick",
      shell.get("old", b"") == b"s=`" + shell_body + b"`", str(shell.get("old", b""))[:80])
gate = cc.plan_adhocs(b"function $NIs(e){let t=$Uh()?.tengu_heron_brook;")[0]
check("2.1.245: $-prefixed identifiers still anchor the delegation cut",
      gate["status"] == "AUTO" and b"function $NIs(e){return null;" in gate.get("new", b""),
      gate.get("reason", ""))

# --- 2.1.257 split the delegation prohibition into a default section plus two
# server-driven override sections, and restructured the communication selector.
split_delegation = (
    b'Wg("opus5_reduced_delegation",()=>{if(!dKt(d))return null;'
    b'if(!P("tengu_slate_bittern",!0))return null;let ye=vzn()?.value;'
    b'if(ye?.includes(Ezn)||ye?.includes(h3o))return null;return Ezn}),'
    b'Wg("heron_brook",()=>p3o()),Wg("brook_heron",()=>y3o(d))')
gate257 = {i["name"]: i for i in cc.plan_adhocs(split_delegation)}["delegation-override-cut"]
check("2.1.257: all three delegation-prohibition sections plan as one AUTO cut",
      gate257["status"] == "AUTO"
      and gate257.get("new", b"").count(b"()=>null") == 2,
      gate257.get("reason", ""))
check("2.1.257: split cut preserves the fleet-wide early-return marker",
      b"){return null;let t=" in gate257.get("new", b""),
      str(gate257.get("new", b"")))

communication257 = (
    b'function r3o(e){let n=Ve(e);if(a.TURN_UPDATES)return n3o;'
    b'if(e3o(n,e)||ktr(n)){let r=t3o(n,e);return`# Communicating with the user\n'
    b'full communication body`}if(LN(e))return"Write code that reads like the surrounding'
    b' ... lean code-comment branch body";return`fallback`}')
lean257 = {i["name"]: i for i in cc.plan_adhocs(communication257)}[
    "lean-models-get-the-communication-fragment"]
check("2.1.257: restructured communication selector plans AUTO",
      lean257["status"] == "AUTO", lean257.get("reason", ""))
check("2.1.257: selector uses the lean branch's own predicate and session argument",
      b"if(e3o(n,e)||ktr(n)||LN(e)){let r=t3o(n,e);return`" == lean257.get("new", b""),
      str(lean257.get("new", b"")))

# --- 2.1.251 split the output-style renderer: the template literal moved into a
# two-argument helper and the renderer became a bare null-guard. Both CC
# generations are live on the fleet, so both shapes must plan AUTO from one
# ccctl. The delegating anchor needs its `var <v>="output_style";` prefix — the
# minified names in that span also name unrelated functions in the bundle.
FLOOR_KEY = b"# How to report back"


def floor_of(blob):
    return {i["name"]: i for i in cc.plan_adhocs(blob)}["disposition-floor-under-every-output-style"]


inline = floor_of(b"function rHE(e){if(e===null)return null;"
                  b"return`# Output Style: ${e.name}\n${e.prompt}`}")
check("2.1.241: inline output-style renderer still plans AUTO",
      inline["status"] == "AUTO" and FLOOR_KEY in inline.get("new", b""),
      inline.get("reason", ""))

deleg = floor_of(b'var vce="output_style";function T8t(e){if(e===null)return null;'
                 b"return _ue(e.name,e.prompt)}")
check("2.1.251: delegating output-style renderer plans AUTO",
      deleg["status"] == "AUTO" and FLOOR_KEY in deleg.get("new", b""),
      deleg.get("reason", ""))
check("2.1.251: the helper call is preserved, not re-inlined",
      b"_ue(e.name,e.prompt)" in deleg.get("new", b"")
      and b"# Output Style: " not in deleg.get("new", b""),
      str(deleg.get("new", b""))[:160])
check("2.1.251: the floor survives an unselected style (no early null return)",
      b"return null" not in deleg.get("new", b"") and b'e===null?""' in deleg.get("new", b""),
      str(deleg.get("new", b""))[:160])
check("2.1.251: replacement stays ASCII, so latin-1 encoding cannot raise",
      deleg.get("new", b"").decode("latin-1").isascii())

# a body-shaped match without the `var <v>="output_style";` prefix is NOT the
# renderer — refuse it rather than patching the wrong function
decoy = floor_of(b"function T8t(e){if(e===null)return null;return _ue(e.name,e.prompt)}")
check("2.1.251: prefix-less lookalike is MANUAL, not a silent mispatch",
      decoy["status"] == "MANUAL", decoy.get("reason", ""))

# --- 2.1.268 deleted the output_style and language SECTIONS. Both now ship only
# as attachments (the behaviour `staticSystemPromptEnabled` used to gate), so the
# style body is a meta message after the system prompt and there is no renderer
# left to append to. The floor moves onto `context_management`, an always-present
# constant section of the same shared list. Three things have to hold at once:
# the new shape plans AUTO, the older shapes still win when they are present, and
# the attachment renderer is never mistaken for the old prompt renderer.
SECTION268 = (b'var E0s=`# Context management\nWhen the conversation grows long`;'
              b'Jb("bg-session",()=>T0s()),Jb("context_management",()=>E0s),'
              b'Jb("brief",()=>C0s()),')

section = floor_of(SECTION268)
check("2.1.268: section-list shape plans AUTO once the renderer is gone",
      section["status"] == "AUTO" and FLOOR_KEY in section.get("new", b""),
      section.get("reason", ""))
check("2.1.268: the floor is appended to the section, not substituted for it",
      section.get("new", b"").startswith(b'Jb("context_management",()=>E0s+"\\n\\n"+"')
      and section.get("new", b"").endswith(b'"),'),
      str(section.get("new", b""))[:120] + " ... " + str(section.get("new", b""))[-40:])
check("2.1.268: only the one list entry is rewritten, siblings untouched",
      section.get("old", b"") == b'Jb("context_management",()=>E0s),',
      str(section.get("old", b"")))
check("2.1.268: replacement stays ASCII, so latin-1 encoding cannot raise",
      section.get("new", b"").decode("latin-1").isascii())

# 2.1.251..2.1.261 carry BOTH shapes. Applying both would install the floor
# twice; the renderer keeps precedence because it still places the floor after
# the style body, which the section shape cannot do.
both = floor_of(b'var vce="output_style";function T8t(e){if(e===null)return null;'
                b"return _ue(e.name,e.prompt)}" + SECTION268)
check("2.1.261: renderer wins over the section shape, so the floor lands once",
      both["status"] == "AUTO" and both.get("old", b"").startswith(b'var vce="output_style";'),
      str(both.get("old", b""))[:120])
check("2.1.261: the section entry is left alone when the renderer matched",
      b"context_management" not in both.get("new", b""),
      str(both.get("new", b""))[:120])

# the attachment renderer ships the reset sentence on null instead of returning
# null. It is not the prompt renderer and must not be anchored as one; with no
# section list beside it there is nothing to patch, so MANUAL is the honest
# verdict rather than a silent mispatch onto a path that only runs on a change.
ATTACH268 = (b'function A$t(e){if(e===null)return"The output style was reset to the '
             b'default. Respond in your usual style.";return HFr(j_(e.name),e.prompt)}')
attach = floor_of(ATTACH268)
check("2.1.268: the attachment renderer alone is MANUAL, not a silent mispatch",
      attach["status"] == "MANUAL", attach.get("reason", ""))

# the realistic build has BOTH: the attachment renderer and the section list.
# MANUAL on the renderer alone could equally be what an empty blob returns, so
# the shape that actually ships has to be exercised too.
real = floor_of(ATTACH268 + SECTION268)
check("2.1.268: with both present the section wins and the renderer is left alone",
      real["status"] == "AUTO" and real.get("old", b"") == b'Jb("context_management",()=>E0s),'
      and b"A$t" not in real.get("new", b""),
      f"{real.get('status')} {str(real.get('old', b''))}")

# --- willow-tern-writing-style-cut. The only target whose correct state can be
# "not present": the section does not exist before 2.1.251, and two machines are
# still on 2.1.241. Absent must plan NOOP, which every plan consumer treats as a
# pass — anything else would fail every apply on the older binary.
WT = "willow-tern-writing-style-cut"

wt = {i["name"]: i for i in cc.plan_adhocs(
    b'function g8t(e){if(!C_t(e))return null;return s("tengu_willow_tern_applied",'
    b"{fromClientData:K3e()===!0}),p8t}")}[WT]
check("willow_tern: the 2.1.251 renderer plans AUTO",
      wt["status"] == "AUTO", wt.get("reason", ""))
check("willow_tern: the cut is a prepended early return, nothing else rewritten",
      wt.get("new", b"").startswith(b"function g8t(e){return null;if(!C_t(e))")
      and wt.get("new", b"").endswith(b'return s("tengu_willow_tern_applied"'),
      str(wt.get("new", b""))[:140])

absent = {i["name"]: i for i in cc.plan_adhocs(b"nothing resembling the section here")}[WT]
check("willow_tern: absent section plans NOOP, not MANUAL",
      absent["status"] == "NOOP", absent.get("status", "") + " " + absent.get("reason", ""))
check("willow_tern: a NOOP item carries no bytes to apply",
      "old" not in absent and "new" not in absent)

# --- git-commit-authority (TODO 38). The stock sentence exists twice in a
# real bundle: in the live template, followed by the `${` of the next
# interpolation, and in the string table of template pieces, which has no
# `${`. Only the first is the one that renders; the second must neither make
# the plan ambiguous nor be required.
#
# The fixture does not carry Anthropic's sentence: the locator is a digest
# (cc.GIT_COMPACT), so the test swaps in a synthetic sentence of the same shape
# and that sentence's own digest, and checks separately that the real digest
# rejects it - the fail-closed property, stated as a test.
GC = "git-commit-authority"
GC_SYN = b"- Commit or push only in this synthetic fixture; branch first."
_real_git_compact = cc.GIT_COMPACT
_real_gc_plan = {i["name"]: i for i in cc.plan_adhocs(
    b'return`# Git\n' + GC_SYN + b'${r?`\n${r}`:""}`')}[GC]
check("git-commit-authority: the real digest does not accept a sentence it was not taken from",
      _real_gc_plan["status"] == "MANUAL", _real_gc_plan.get("status", ""))
cc.GIT_COMPACT = cc.StockSentence(b"- Commit or push only", len(GC_SYN),
                                  hashlib.sha256(GC_SYN).hexdigest())
TEMPLATE = (b'return`# Git\n- Interactive flags (\\`-i\\`) are not supported.\n'
            b'- A synthetic line stands in for the stock one here.\n'
            + GC_SYN + b'${r?`\n${r}`:""}`')
TABLE = b"\x00# Git\n- A synthetic line.\n" + GC_SYN + b"\x00\x10other"
gc = {i["name"]: i for i in cc.plan_adhocs(TEMPLATE + b";" + TABLE)}[GC]
check("git-commit-authority: template copy plans AUTO beside the string-table copy",
      gc["status"] == "AUTO", gc.get("reason", ""))
check("git-commit-authority: the replacement keeps the next interpolation's opener",
      gc.get("old", b"").endswith(b"branch first.${") and gc.get("new", b"").endswith(b"go-ahead.${")
      and b"Committing finished work is part of the task" in gc.get("new", b""))
check("git-commit-authority: the payload is template-safe ASCII",
      all(c < 128 for c in gc.get("new", b"")) and b"`" not in gc.get("new", b"")
      and gc.get("new", b"").count(b"${") == 1)
gone = {i["name"]: i for i in cc.plan_adhocs(TABLE)}[GC]
check("git-commit-authority: string-table copy alone is MANUAL, not a quiet skip",
      gone["status"] == "MANUAL", gone.get("status", ""))
dup = {i["name"]: i for i in cc.plan_adhocs(TEMPLATE + b";" + TEMPLATE)}[GC]
check("git-commit-authority: two live templates are MANUAL (ambiguous)",
      dup["status"] == "MANUAL" and "ambiguous" in dup.get("reason", ""), dup.get("reason", ""))
cc.GIT_COMPACT = _real_git_compact

# --- action-caution-outcome-line (item 30 audit). The sentence sits in the
# section builder and again in a string table; only the builder, found by its
# own `function <f>(e){if(!<lean>(e))return null;return"` prologue, is rewritten.
AC = "action-caution-outcome-line"
AC_TAIL = (b"a synthetic clause stands in for the stock middle here. On a failed check, "
           b"say so with the output; a synthetic tail closes the sentence.")
AC_BUILDER = (b'function Q2n(e){if(!x2(e))return null;return"' + cc.ACTION_CAUTION_HEAD
              + b' unless durably authorized; ' + AC_TAIL + b'"}')
AC_TABLE = b"\x00\x08\x02\x00\x80" + cc.ACTION_CAUTION_HEAD + b" unless durably authorized; " + AC_TAIL + b"\x00"
ac = {i["name"]: i for i in cc.plan_adhocs(AC_TABLE + b";" + AC_BUILDER)}[AC]
check("action-caution: the builder copy plans AUTO beside the string-table copy",
      ac["status"] == "AUTO", ac.get("reason", ""))
check("action-caution: only the outcome clause changes",
      ac.get("old", b"").startswith(b"function Q2n(e){if(!x2(e))return null;return\"")
      and ac.get("new", b"") == ac.get("old", b"").replace(b"say so with the output;",
                                                          b"say so and where the output is;"),
      str(ac.get("new", b""))[-80:])
check("action-caution: the payload is string-safe ASCII",
      all(c < 128 for c in ac.get("new", b"")) and ac.get("new", b"").count(b'"') == 1)
ac_gone = {i["name"]: i for i in cc.plan_adhocs(AC_TABLE)}[AC]
check("action-caution: string-table copy alone is MANUAL, not a quiet skip",
      ac_gone["status"] == "MANUAL", ac_gone.get("status", ""))
ac_dup = {i["name"]: i for i in cc.plan_adhocs(AC_BUILDER + b";" + AC_BUILDER)}[AC]
check("action-caution: two builders are MANUAL (ambiguous)",
      ac_dup["status"] == "MANUAL" and "ambiguous" in ac_dup.get("reason", ""), ac_dup.get("reason", ""))
ac_reworded = {i["name"]: i for i in cc.plan_adhocs(
    AC_BUILDER.replace(b"say so with the output", b"say so, quoting the output"))}[AC]
check("action-caution: a reworded clause is MANUAL",
      ac_reworded["status"] == "MANUAL", ac_reworded.get("status", ""))

# --- bash-audience-note-result-not-output (item 30 audit). One string
# literal; only its last sentence changes, and nothing outside the quotes.
BN = "bash-audience-note-result-not-output"
BN_SYN = b"If the user needs this synthetic tail, it stands in here."
_real_bn_tail = cc.BASH_NOTE_TAIL
cc.BASH_NOTE_TAIL = cc.StockSentence(b"If the user needs", len(BN_SYN), hashlib.sha256(BN_SYN).hexdigest())
BN_LIT = (b'"' + cc.BASH_NOTE_HEAD + b" \\u2014 a synthetic middle sentence. "
          + BN_SYN + b'"')
BN_SRC = b"bash_output_audience_note:()=>ui([Te({content:" + BN_LIT + b",isMeta:!0})]),"
bn = {i["name"]: i for i in cc.plan_adhocs(BN_SRC)}[BN]
check("bash-audience-note: the literal plans AUTO", bn["status"] == "AUTO", bn.get("reason", ""))
check("bash-audience-note: only the last sentence changes, the gate is not in the span",
      bn.get("old", b"") == BN_LIT and bn.get("new", b"") == BN_LIT.replace(
          BN_SYN, b"Say what it means for them, not what it printed.")
      and b"bash_output_audience_note" not in bn.get("old", b""),
      str(bn.get("new", b"")))
check("bash-audience-note: the payload is string-safe ASCII",
      all(c < 128 for c in bn.get("new", b"")) and bn.get("new", b"").count(b'"') == 2)
bn_gone = {i["name"]: i for i in cc.plan_adhocs(b"no note here")}[BN]
check("bash-audience-note: absent is MANUAL, not a quiet skip",
      bn_gone["status"] == "MANUAL", bn_gone.get("status", ""))
bn_dup = {i["name"]: i for i in cc.plan_adhocs(BN_SRC + BN_SRC)}[BN]
check("bash-audience-note: two copies are MANUAL (ambiguous)",
      bn_dup["status"] == "MANUAL" and "ambiguous" in bn_dup.get("reason", ""), bn_dup.get("reason", ""))

# --- span_apply asserts the exact replacement bytes, not just tweakcc's
# "Replaced 1" — JS replace() honours $&/$1/$<name> in the replacement, and our
# replacements now carry `$` sequences.
tmp2 = Path(tempfile.mkdtemp())
real_adhoc = cc.adhoc_patch
try:
    tgt = tmp2 / "staged"
    item = {"name": "t", "status": "AUTO", "old": b"OLD-PAYLOAD-${$r}", "new": b"NEW-PAYLOAD-${$r}"}

    def honest(target, old, new):
        p = Path(target); p.write_bytes(p.read_bytes().replace(old, new)); return True, "Replaced 1"
    cc.adhoc_patch = honest
    tgt.write_bytes(b"HEAD" + item["old"] + b"TAIL")
    cc.span_apply(tgt, [dict(item)])
    check("span_apply: honest write satisfies the exact-bytes assertion",
          item["new"] in tgt.read_bytes())

    def mangling(target, old, new):        # reports success, eats the $ sequence
        p = Path(target)
        p.write_bytes(p.read_bytes().replace(old, new.replace(b"${$r}", b"${}")))
        return True, "Replaced 1"
    cc.adhoc_patch = mangling
    tgt.write_bytes(b"HEAD" + item["old"] + b"TAIL")
    try:
        cc.span_apply(tgt, [dict(item)])
        caught = False
    except SystemExit:
        caught = True
    check("span_apply: mangled $ replacement is caught despite 'Replaced 1'", caught)
finally:
    cc.adhoc_patch = real_adhoc
    shutil.rmtree(tmp2, ignore_errors=True)

# --- the seam between the two fixes above (merge, 2026-08-25). The $-assertion
# was written on win32, where the file holds exactly one bundle; on linux the
# write it checks has just appended a copy and abandoned the old one. Scoped
# whole-file, an earlier item's identical replacement sitting in an abandoned
# copy answers for the current item and a mangled live write reads as success.
tmp3 = Path(tempfile.mkdtemp())
real_adhoc = cc.adhoc_patch
try:
    item = {"name": "t", "status": "AUTO", "old": b"OLD-PAYLOAD-${$r}", "new": b"NEW-PAYLOAD-${$r}"}
    tgt3 = tmp3 / "elf-staged"
    win3 = fake_elf(tgt3, b"a" * 512 + item["old"] + b"b" * 512, orphans=1)
    raw = bytearray(tgt3.read_bytes())
    off = raw.index(item["old"])                  # first hit is the abandoned copy
    assert off < win3[0], "the poke must land outside the live window"
    raw[off:off + len(item["new"])] = item["new"]  # as an earlier item's write left it
    tgt3.write_bytes(raw)
    check("seam: the abandoned copy holds the exact replacement bytes, the live one does not",
          bytes(raw).count(item["new"]) == 1 and cc.live_blob(tgt3).count(item["new"]) == 0)

    def mangling_elf(target, old, new):           # writes the live window, eats the $
        p, w = Path(target), cc.bun_window(target)
        r = bytearray(p.read_bytes())
        i = r.index(old, w[0], w[1])
        bad = new.replace(b"${$r}", b"${}")
        r[i:i + len(old)] = bad + b"\0" * (len(old) - len(bad))   # keep offsets valid
        p.write_bytes(r)
        return True, "Replaced 1"
    cc.adhoc_patch = mangling_elf
    try:
        cc.span_apply(tgt3, [dict(item)])
        caught3 = False
    except SystemExit:
        caught3 = True
    check("span_apply: mangled live write is caught even though an abandoned copy "
          "holds the right bytes", caught3)
finally:
    cc.adhoc_patch = real_adhoc
    shutil.rmtree(tmp3, ignore_errors=True)

# --- 2026-08-26: tweakccPatches is on everywhere; the darwin carve-out is gone
# It was never a platform property. The Mac's PATH node could not parse the
# `using` declarations in CC's Bun-built bundle, so tweakcc's own `node --check`
# rejected a valid bundle. node_exe() picks a capable node instead, and the
# platform no longer decides anything here.
_defsrc = (Path(__file__).resolve().parent.parent / "ccctl.py").read_text(encoding="utf-8")
check("tweakccPatches: default is True on every platform",
      cc.TWEAKCC_PATCHES_DEFAULT is True,
      f"{sys.platform} -> {cc.TWEAKCC_PATCHES_DEFAULT}")
check("tweakccPatches: no platform test survives in the default",
      "sys.platform" not in _defsrc.split("TWEAKCC_PATCHES_DEFAULT =")[1].split("\n")[0])

# --- 2026-08-26: tweakcc must run under a node that parses `using`
# CC's bundle is Bun-built and carries `using` declarations; tweakcc verifies
# its patched bundle with `node --check`, so an old node rejects a VALID bundle
# and tweakcc reverts. That, not macOS, is what "tweakcc cannot patch on darwin"
# was. The probe is the fix, so the probe is what gets guarded.
check("node probe: a `using` declaration is what it tests for",
      "using r" in cc._USING_PROBE and "Symbol.dispose" in cc._USING_PROBE)
check("node probe: rejects an exe that cannot parse it",
      cc.node_parses_using(sys.executable) is False,   # python is not a JS parser
      "python accepted a using-declaration probe")
_chosen = cc.node_exe()
check("node_exe: returns something runnable", isinstance(_chosen, str) and _chosen)
# On a machine that HAS a capable node, the chosen one must actually be capable.
# Where none exists the fallback is deliberate, so only assert the positive case.
_any_capable = any(cc.node_parses_using(c) for c in
                   [x for x in (shutil.which("node"), "/opt/homebrew/bin/node") if x])
if _any_capable:
    check("node_exe: picks a node that parses `using` when one exists",
          cc.node_parses_using(_chosen), f"chose {_chosen}")
    check("tweakcc_node: runs tweakcc under that node, not bare 'node'",
          cc.tweakcc_node()[0] == _chosen, str(cc.tweakcc_node()[:1]))
check("tweakccPatches: CONFIG_DEFAULTS carries the derived value, not a literal",
      cc.CONFIG_DEFAULTS["tweakccPatches"] is cc.TWEAKCC_PATCHES_DEFAULT)
# The knob has to stay overridable per machine: a Mac retesting a fixed tweakcc,
# or a linux box that a future build breaks, edits its own ccctl.json and wins.
_merged = {**cc.CONFIG_DEFAULTS, **{"tweakccPatches": not cc.TWEAKCC_PATCHES_DEFAULT}}
check("tweakccPatches: an explicit ccctl.json value still overrides the default",
      _merged["tweakccPatches"] == (not cc.TWEAKCC_PATCHES_DEFAULT))
# init must NOT freeze it into config — that is what makes a later fix a pull
# instead of three hand edits.
_init_src = (Path(__file__).resolve().parent.parent / "ccctl.py").read_text(encoding="utf-8")
_init_body = _init_src.split("def cmd_init(")[1].split("\ndef ")[0]
check("tweakccPatches: init does not write the key into ccctl.json",
      '"tweakccPatches"' not in _init_body.split("save_json(CONFIG")[0])

# --- 2026-08-25: assemble falls back when a build rejects tweakcc's patch set
# The knob's platform default handles the ONE build we have met. This is the
# general case: test the build in front of us, once, and carry on without the
# patch set if it says no. What must hold is that the retry starts from a fresh
# copy of the pristine source — the staged file has had a failed tweakcc pass
# over it and is not a trustworthy base.
import types
tmp4 = Path(tempfile.mkdtemp(prefix="ccctl-fallback-"))
_saved = (cc.STAGING, cc.STATE, cc.CHANGELOG, cc.tweakcc_apply_to, cc.span_apply,
          cc.repo_markers, cc.run, cc.tweakcc_pass, cc.repo_antimarkers)
try:
    MARKER = "a marker only the pristine source carries"
    POISON = "TWEAKCC-WROTE-THIS"
    src = tmp4 / "pristine.bin"
    src.write_bytes(b"x" * 100 + MARKER.encode() + b"y" * 100)
    cc.STAGING, cc.STATE, cc.CHANGELOG = tmp4 / "staging", tmp4 / "state.json", tmp4 / "log.md"
    cc.repo_markers = lambda cfg: [MARKER]
    cc.repo_antimarkers = lambda cfg: []          # assemble gates on these too now
    cc.span_apply = lambda target, plan: None                       # our patches: not under test
    cc.run = lambda cmd, **kw: types.SimpleNamespace(stdout="9.9.9 (Claude Code)", stderr="", returncode=0)

    def rejecting(staged, markers):           # what a build that says no looks like
        Path(staged).write_bytes(Path(staged).read_bytes() + POISON.encode())
        return False, "tweakcc --apply exited 1: could not find anchor"
    cc.tweakcc_apply_to = rejecting
    staged = cc.assemble({"tweakccPatches": True}, "9.9.9", src, [])
    body = Path(staged).read_bytes()
    check("fallback: assemble still produces a verified staged binary", staged.exists())
    check("fallback: retry re-staged from pristine, not from the poisoned copy",
          POISON.encode() not in body)
    check("fallback: the staged binary still carries our markers", MARKER.encode() in body)
    st = cc.load_json(cc.STATE, {})
    check("fallback: recorded in state so status can explain the absent banner",
          st.get("tweakccFallback", {}).get("ccVersion") == "9.9.9")
    check("fallback: reason is carried, not just a flag",
          "anchor" in st.get("tweakccFallback", {}).get("reason", ""))
    check("fallback: written to the changelog", cc.CHANGELOG.exists()
          and "tweakcc-fallback" in cc.CHANGELOG.read_text(encoding="utf-8"))

    # ...and a build that accepts the patch set retires the note by itself, so
    # a fixed tweakcc does not leave `status` apologising for a stale failure.
    def accepting(staged, markers):
        return True, "patched 12 files"
    cc.tweakcc_apply_to = accepting
    cc.tweakcc_pass = lambda cfg, version, staged, plan: (plan, None)   # re-plan not under test
    cc.assemble({"tweakccPatches": True}, "9.9.9", src, [])
    check("fallback: a later successful patch set clears the note",
          "tweakccFallback" not in cc.load_json(cc.STATE, {}))

    # The knob off entirely: no tweakcc pass, no fallback bookkeeping.
    cc.tweakcc_apply_to = lambda *a: (_ for _ in ()).throw(AssertionError("must not run"))
    cc.assemble({"tweakccPatches": False}, "9.9.9", src, [])
    check("fallback: tweakccPatches=false skips the patch set entirely",
          "tweakccFallback" not in cc.load_json(cc.STATE, {}))
finally:
    (cc.STAGING, cc.STATE, cc.CHANGELOG, cc.tweakcc_apply_to, cc.span_apply,
     cc.repo_markers, cc.run, cc.tweakcc_pass, cc.repo_antimarkers) = _saved
    shutil.rmtree(tmp4, ignore_errors=True)

# --- 2026-08-25: tweakcc's native-binary.backup can be one of OUR outputs.
# Measured on win32: the backup was byte-identical to the previous round's
# patched binary, and `--apply` restores from it before patching — so the second
# apply's staged "pristine" copy was silently already-patched. Two consumers had
# to be taught: --apply (park it) and --restore (refuse it).
tmp5 = Path(tempfile.mkdtemp(prefix="ccctl-backup-"))
_savedtw = cc.TWEAKCC_DIR
try:
    cc.TWEAKCC_DIR = tmp5
    MK = ["the artifact, not the account of it"]
    bk = tmp5 / "native-binary.backup"

    bk.write_bytes(b"stock bytes, nothing of ours here")
    check("backup: a STOCK backup is left where it is",
          cc.park_patched_backup(MK) is None and bk.exists())

    bk.write_bytes(b"head " + MK[0].encode() + b" tail")
    parked = cc.park_patched_backup(MK)
    check("backup: a PATCHED backup is parked out of --apply's reach",
          parked is not None and not bk.exists() and parked.exists())
    check("backup: parked, not deleted (rollback artifacts are kept here)",
          parked and MK[0].encode() in parked.read_bytes())

    bk.write_bytes(b"head " + MK[0].encode() + b" tail")
    check("backup: no markers means unknowable — leave it alone, do not guess",
          cc.park_patched_backup([]) is None and bk.exists())
finally:
    cc.TWEAKCC_DIR = _savedtw
    shutil.rmtree(tmp5, ignore_errors=True)

# the cached verdict: one scan per (size, mtime, markers), then free
tmp7 = Path(tempfile.mkdtemp(prefix="ccctl-verdict-"))
_savedv = (cc.TWEAKCC_DIR, cc.STATE)
try:
    cc.TWEAKCC_DIR, cc.STATE = tmp7, tmp7 / "state.json"
    MK2 = ["the artifact, not the account of it"]
    bk2 = tmp7 / "native-binary.backup"
    check("verdict: no backup means the question does not apply",
          cc.backup_is_patched(MK2) is None)
    bk2.write_bytes(b"clean bytes")
    check("verdict: a stock backup reads as not patched", cc.backup_is_patched(MK2) is False)
    check("verdict: cached in state after the first scan",
          cc.load_json(cc.STATE, {}).get("backupVerdict", {}).get("patched") is False)
    calls = []
    real_is_stock = cc.is_stock
    cc.is_stock = lambda p, m: (calls.append(1), real_is_stock(p, m))[1]
    try:
        cc.backup_is_patched(MK2)
        check("verdict: an unchanged backup is not re-scanned", not calls)
        bk2.write_bytes(b"head " + MK2[0].encode() + b" tail")
        check("verdict: a changed backup IS re-scanned and reads patched",
              cc.backup_is_patched(MK2) is True and len(calls) == 1)
    finally:
        cc.is_stock = real_is_stock
    check("verdict: no markers means unknowable, not False",
          cc.backup_is_patched([]) is None)
finally:
    (cc.TWEAKCC_DIR, cc.STATE) = _savedv
    shutil.rmtree(tmp7, ignore_errors=True)

check("verdict: the SessionStart hook path warns about a patched backup",
      "backup_is_patched(markers)" in inspect.getsource(cc.cmd_status))

_restore = inspect.getsource(cc.cmd_restore)
check("backup: --restore refuses a patched backup instead of calling it stock",
      "is_stock(backup, markers)" in _restore and "die(" in _restore
      and _restore.index("die(") < _restore.index('run([*tweakcc_node(), "--restore"]'))
check("backup: status labels a patched backup as not a rollback source",
      "not a rollback source" in inspect.getsource(cc.cmd_status))

# --- 2026-08-25: prune keeps the newest N per class. The trap is that
# `.pre-swap.N` is the slot the swap took when `.pre-swap` was LOCKED by a
# running session, so the suffix does not track recency — measured on win32:
# `.pre-swap.1` was 21.08 while the unsuffixed one was 24.08 and `.4` was 25.08.
# Name-order retention deletes the newest rollback and keeps the stalest.
tmp6 = Path(tempfile.mkdtemp(prefix="ccctl-prune-"))
_savedp = (cc.launcher_path, cc.versions_dirs, cc.TWEAKCC_DIR, cc.STAGING,
           cc.CHANGELOG, cc.require_cfg, cc.repo_markers, cc.cc_version)
try:
    live = tmp6 / "claude.exe"; live.write_bytes(b"live")
    vdir = tmp6 / "versions"; vdir.mkdir()
    cc.launcher_path = lambda: live
    cc.versions_dirs = lambda: [vdir]
    cc.TWEAKCC_DIR, cc.STAGING, cc.CHANGELOG = tmp6, tmp6 / "staging", tmp6 / "log.md"
    cc.require_cfg = lambda: {}
    cc.repo_markers = lambda cfg: ["MARKER"]
    cc.cc_version = lambda: "2.1.241"

    # name order and mtime order deliberately disagree, as they did on hardware
    ages = {"claude.exe.pre-swap": 3, "claude.exe.pre-swap.1": 5,
            "claude.exe.pre-swap.2": 4, "claude.exe.pre-swap.3": 2,
            "claude.exe.pre-swap.4": 1}
    for n, days in ages.items():
        f = tmp6 / n; f.write_bytes(b"x" * 10)
        os.utime(f, (0, 1_000_000_000 - days * 86400))
    (vdir / "2.1.241.stock").write_bytes(b"stock")           # installed: protected
    os.utime(vdir / "2.1.241.stock", (0, 1))                 # ...and the OLDEST file here
    (vdir / "2.1.234.stock").write_bytes(b"old stock")
    os.utime(vdir / "2.1.234.stock", (0, 1_000_000_000))
    (vdir / "2.1.241").write_bytes(b"CC's own install, not ours to delete")
    os.utime(vdir / "2.1.241", (0, 1))                       # oldest thing on disk

    groups = dict(cc.parked_groups(["MARKER"]))
    order = [p.name for p in groups["previous live binaries"]]
    check("prune: groups are ordered by mtime, not by name",
          order == ["claude.exe.pre-swap.4", "claude.exe.pre-swap.3",
                    "claude.exe.pre-swap", "claude.exe.pre-swap.2", "claude.exe.pre-swap.1"],
          str(order))
    check("prune: the installed version's .stock is protected",
          (vdir / "2.1.241.stock").resolve() in cc.prune_protected(["MARKER"]))

    class A: keep, dry_run = 2, False        # explicit --keep overrides every class
    cc.cmd_prune(A())
    left = sorted(p.name for p in tmp6.glob("claude.exe.pre-swap*"))
    check("prune: --keep N forces the same count for every class",
          left == ["claude.exe.pre-swap.3", "claude.exe.pre-swap.4"], str(left))

    class B: keep, dry_run = None, False      # per-class defaults: 1 previous build
    cc.cmd_prune(B())
    left = sorted(p.name for p in tmp6.glob("claude.exe.pre-swap*"))
    check("prune: default keeps ONE previous build, the newest",
          left == ["claude.exe.pre-swap.4"], str(left))
    (tmp7b := tmp6 / "native-binary.backup.patched-x").write_bytes(b"our own output")
    cc.cmd_prune(B())
    check("prune: parked tweakcc backups default to keep 0 (useless by construction)",
          not tmp7b.exists())
    check("prune: the oldest .stock survives when it is the installed version's",
          (vdir / "2.1.241.stock").exists())
    check("prune: never touches the live binary", live.exists())
    check("prune: never touches a bare versions/<ver> install (CC's own, not ours)",
          (vdir / "2.1.241").exists())

    # a file a session is executing: OSError on unlink must be survivable
    doomed = tmp6 / "claude.exe.pre-swap.9"; doomed.write_bytes(b"running")
    os.utime(doomed, (0, 1))
    real_unlink = Path.unlink
    Path.unlink = lambda self, **kw: (_ for _ in ()).throw(OSError(13, "in use")) \
        if self.name.endswith(".9") else real_unlink(self, **kw)
    try:
        cc.cmd_prune(A())
        survived = doomed.exists()
    finally:
        Path.unlink = real_unlink
    check("prune: a binary still executing is skipped, not fatal", survived)
finally:
    (cc.launcher_path, cc.versions_dirs, cc.TWEAKCC_DIR, cc.STAGING,
     cc.CHANGELOG, cc.require_cfg, cc.repo_markers, cc.cc_version) = _savedp
    shutil.rmtree(tmp6, ignore_errors=True)


# --- bun_source_patch: the in-place writer for code-split builds (CC >= 2.1.243,
# tweakcc #969). Size-preserving splice; the pad before the offsets header
# absorbs the delta. Synthetic 25-module blob with per-module bytecode caches
# whose word at offset 28 records the compiled-from source length, as measured
# on 2.1.241 and 2.1.251.
import struct as _st


def _bun_blob(nmods=25, pad=64, optional=False):
    data = bytearray()
    for i in range(nmods):
        name = f"/$bunfs/root/chunk-{i:04d}.js".encode()
        src = f"var m{i}=`MODULE{i}-PAYLOAD-STOCKTEXT`;".encode()
        runs = []
        for run in (name, src):
            runs.append((len(data), len(run))); data += run + b"\x00"
        runs.append((0, 0))
        bc = bytearray(40); _st.pack_into("<I", bc, 28, len(src))
        runs.append((len(data), 40)); data += bc + b"\x00"
        runs += [(0, 0), (0, 0)]
        data_runs = runs
        if i == 0:
            structs = bytearray()
        structs += b"".join(_st.pack("<II", o, l) for o, l in data_runs) + bytes([1, 1, 1, 0])
    # These shared regions model the tables introduced in newer Bun bundles.
    # They deliberately precede the module table, so an early source edit has
    # to relocate both their records and their pointed-at data.
    builtin = b"BUILTIN-BYTECODE" if optional else b""
    bytecode_strings = b"bytecode-strings" if optional else b""
    module_info_strings = b"module-info-strings" if optional else b""
    if optional:
        bo = len(data); data += builtin + b"\x00"
        bso = len(data); data += bytecode_strings + b"\x00"
        miso = len(data); data += module_info_strings + b"\x00"
    mo = len(data); data += structs; ml = len(structs)
    if optional:
        data += b"".join(_st.pack("<I", i + 1) for i in range(nmods))
        data += _st.pack("<I", 1) + _st.pack("<III", 17, bo, len(builtin))
        data += _st.pack("<II", bso, len(bytecode_strings))
        data += _st.pack("<I", 1)
        data += _st.pack("<II", miso, len(module_info_strings))
    ao = len(data); data += b"\x00" + b"\x00" * pad
    blob_len = 8 + len(data) + 32 + 16
    flags = 7 | (32 | 64 | 128 | 256 | 512 if optional else 0)
    hdr = _st.pack("<QIIIIII", blob_len - 56, mo, ml, 0, ao, 0, flags)
    return _st.pack("<Q", blob_len - 8) + bytes(data) + hdr + cc.BUN_TRAILER


_bb = _bun_blob()
check("bun: synthetic blob parses", cc.bun_table(_bb) is not None)

# --- changed_module_parse_errors: `--version` never loads a chunk, so a patched
# module that no longer parses passed assemble's checks on macOS 2.1.270 and
# crashed every real session (tweakcc's AGENTS.md patch, `$$t` -> `$t`).
_node = cc.node_exe()
_pd = Path(tempfile.mkdtemp())
(_pd / "src").write_bytes(_bb)
_srcmods = cc.bundle_sources(_pd / "src")
check("parse-check: bundle_sources reads every module of a synthetic blob",
      _srcmods is not None and len(_srcmods) == 25
      and _srcmods["/$bunfs/root/chunk-0007.js"] == b"var m7=`MODULE7-PAYLOAD-STOCKTEXT`;")
if shutil.which(_node) or Path(_node).exists():
    (_pd / "good").write_bytes(_bb)
    ok, _ = cc.bun_source_patch(_pd / "good", b"MODULE7-PAYLOAD-STOCKTEXT", b"OURS7")
    check("parse-check: a valid edit reports no broken modules",
          ok and cc.changed_module_parse_errors(_pd / "src", _pd / "good") == [])
    (_pd / "bad").write_bytes(_bb)
    ok, _ = cc.bun_source_patch(_pd / "bad", b"MODULE7-PAYLOAD-STOCKTEXT", b"x`;let m7=1;`")
    _broken = cc.changed_module_parse_errors(_pd / "src", _pd / "bad")
    check("parse-check: a duplicate declaration in the edited module is named",
          ok and _broken is not None and [n for n, _e in _broken] == ["/$bunfs/root/chunk-0007.js"]
          and "SyntaxError" in _broken[0][1], str(_broken))
    check("parse-check: unchanged modules are not re-parsed into the verdict",
          all(n == "/$bunfs/root/chunk-0007.js" for n, _e in (_broken or [])))
else:
    print("SKIP parse-check: no node on this machine")
check("parse-check: no module table means the check cannot run, not that it passed",
      cc.changed_module_parse_errors(Path(__file__), Path(__file__)) is None)
shutil.rmtree(_pd, ignore_errors=True)
tmp7 = Path(tempfile.mkdtemp()); _bf = tmp7 / "blob"

_bf.write_bytes(_bb)
ok, out = cc.bun_source_patch(_bf, b"MODULE7-PAYLOAD-STOCKTEXT", b"OURS7")
nb = _bf.read_bytes(); t = cc.bun_table(nb)
check("bun shrink: ok, size preserved, table re-parses",
      ok and len(nb) == len(_bb) and t is not None, out)
m8 = [m for m in (t["mods"] if t else []) if b"0008" in m["name"]][0]
co, cl = m8["runs"][1]
check("bun shrink: shifted run still points at its own bytes",
      nb[co + 8:co + 8 + cl] == b"var m8=`MODULE8-PAYLOAD-STOCKTEXT`;")

_bf.write_bytes(_bb)
ok, out = cc.bun_source_patch(_bf, b"MODULE3-PAYLOAD-STOCKTEXT",
                              b"MODULE3-PAYLOAD-STOCKTEXT-PLUS-TWENTYTWO")
check("bun grow-within-pad: ok, size preserved",
      ok and len(_bf.read_bytes()) == len(_bb), out)

_bf.write_bytes(_bun_blob(pad=2))
ok, out = cc.bun_source_patch(_bf, b"MODULE3-PAYLOAD-STOCKTEXT", b"X" * 400)
check("bun grow-beyond-pad: refused, file untouched",
      not ok and "pad" in out and _bf.read_bytes() == _bun_blob(pad=2), out)

_bf.write_bytes(_bb)
ok, out = cc.bun_source_patch(_bf, b"MODULE5-PAYLOAD-STOCKTEXT", b"MODULE5-PAYLOAD-stocktext")
_same = cc.bun_table(_bf.read_bytes())
_same_owner = [m for m in _same["mods"] if b"0005" in m["name"]][0] if _same else None
check("bun same-length edit: succeeds and drops the stale bytecode cache",
      ok and _same_owner and _same_owner["runs"][3] == (0, 0), out)

_optional = _bun_blob(optional=True)
_bf.write_bytes(_optional)
ok, out = cc.bun_source_patch(_bf, b"MODULE7-PAYLOAD-STOCKTEXT", b"OURS7-PLUS")
_optional_out = _bf.read_bytes(); _optional_table = cc.bun_table(_optional_out)
_optional_owner = ([m for m in _optional_table["mods"] if b"0007" in m["name"]][0]
                   if _optional_table else None)
_builtin_ok = (_optional_table and len(_optional_table["builtins"]) == 1 and
               (_b := _optional_table["builtins"][0]) and
               _optional_out[_b["off"] + 8:_b["off"] + 8 + _b["len"]] == b"BUILTIN-BYTECODE")
_bcs = _optional_table["bytecode_strings"] if _optional_table else None
_mis = _optional_table["module_info_strings"] if _optional_table else None
_shared_ok = (_bcs and _mis and
              _optional_out[_bcs["off"] + 8:_bcs["off"] + 8 + _bcs["len"]] == b"bytecode-strings" and
              _optional_out[_mis["off"] + 8:_mis["off"] + 8 + _mis["len"]] == b"module-info-strings")
_hash_ok = (_optional_table and _optional_table["source_hash_at"] is not None and
            _st.unpack_from("<I", _optional_out, _optional_table["source_hash_at"] + 8 + 7 * 4)[0] == 0)
check("bun optional tables: builtins/string pointers relocate and edited caches invalidate",
      ok and _optional_table and _builtin_ok and _shared_ok and _hash_ok and _optional_owner and
      _optional_owner["runs"][3:] == [(0, 0), (0, 0), (0, 0)], out)

_bf.write_bytes(_bun_blob(nmods=5))
ok, out = cc.bun_source_patch(_bf, b"MODULE3-PAYLOAD-STOCKTEXT", b"Z")
check("bun pre-split build: defers to the tweakcc path",
      not ok and out.startswith("not code-split"), out)

# --- 4-BYTE ALIGNMENT. Bytecode and builtin regions are mapped in place and
# read as u32s; a region that moves by a non-multiple of 4 makes CC fast-fail at
# startup (0xC0000409) with no output at all, while every patch still reports
# OK and the module table still re-parses. Measured 2026-09-11 on a tweakcc-
# rebuilt 2.1.261: deltas 0/4/8/12/16/20 launch, 1/2/3/5/6/7/13 do not. The
# writer rounds each splice up to a multiple of 4 with filler spaces at the end
# of the owner module's own source. Assert the invariant, not the symptom: every
# region beyond the owner moves by a multiple of 4, whatever the edit's length.
for _d, _new in ((-1, b"MODULE7-PAYLOAD-STOCKTEX"),
                 (+1, b"MODULE7-PAYLOAD-STOCKTEXT2"),
                 (+2, b"MODULE7-PAYLOAD-STOCKTEXT23"),
                 (+3, b"MODULE7-PAYLOAD-STOCKTEXT234"),
                 (0, b"MODULE7-PAYLOAD-OURSTEXT!")):
    _before_blob = _bun_blob(optional=True)
    _bf.write_bytes(_before_blob)
    _before = cc.bun_table(_before_blob)
    ok, out = cc.bun_source_patch(_bf, b"MODULE7-PAYLOAD-STOCKTEXT", _new)
    _after_blob = _bf.read_bytes()
    _after = cc.bun_table(_after_blob)
    _owner_i = next(i for i, m in enumerate(_before["mods"]) if b"0007" in m["name"])
    _moves, _grew, _intact = [], None, True
    if _after:
        for i, (mb, ma) in enumerate(zip(_before["mods"], _after["mods"])):
            for j, ((ob, lb), (oa, la)) in enumerate(zip(mb["runs"], ma["runs"])):
                if lb and i != _owner_i:
                    _moves.append(oa - ob)
                    # A multiple of 4 is necessary, not sufficient: a relocated
                    # run also has to still address ITS OWN bytes. Without this
                    # the loop would pass on a writer that shifted everything by
                    # a tidy but wrong amount. (The older delta -20 case at the
                    # top of this block never tested it either, because -20 was
                    # already aligned — the exact case this code was written for
                    # was the one with no content assertion.)
                    if j == 1:
                        want = f"var m{i}=`MODULE{i}-PAYLOAD-STOCKTEXT`;".encode()
                        if _after_blob[oa + 8:oa + 8 + la] != want:
                            _intact = False
        for key in ("bytecode_strings", "module_info_strings"):
            _moves.append(_after[key]["off"] - _before[key]["off"])
        _moves += [a["off"] - b["off"]
                   for a, b in zip(_after["builtins"], _before["builtins"])]
        _oc = _after["mods"][_owner_i]["runs"][1]
        _grew = _oc[1] - _before["mods"][_owner_i]["runs"][1][1]
    _fill = (-_d) % 4
    check(f"bun align: delta {_d:+d} moves every later region by a multiple of 4",
          ok and _after and all(m % 4 == 0 for m in _moves),
          f"{out} moves={sorted(set(_moves))[:6] if _moves else None}")
    check(f"bun align: delta {_d:+d} widens the owner's source by {_d}+{_fill} filler",
          ok and _grew == _d + _fill, f"grew={_grew}")
    check(f"bun align: delta {_d:+d} keeps the edit's exact bytes and pads with spaces",
          ok and _after is not None
          and _after_blob[_oc[0] + 8:_oc[0] + 8 + _oc[1]]
          == b"var m7=`" + _new + b"`;" + b" " * _fill,
          repr(_after_blob[_oc[0] + 8:_oc[0] + 8 + _oc[1]]) if _after else "")
    check(f"bun align: delta {_d:+d} preserves blob size",
          len(_after_blob) == len(_before_blob))
    check(f"bun align: delta {_d:+d} leaves every relocated run addressing its own bytes",
          ok and _intact)

# the two-tier shift is only valid because nothing starts inside the owner's
# source between the cut and its end. Refuse rather than guess if that is ever
# false — a bundle whose layout this writer does not model must not be written.
_bf.write_bytes(_bb)
_saved_bun_table = cc.bun_table


def _table_with_run_inside(blob):
    t = _saved_bun_table(blob)
    # only tamper with the read that plans the write; bun_source_patch calls
    # bun_table again for its self-check, on a blob that no longer holds `old`
    p = blob.find(b"MODULE7-PAYLOAD-STOCKTEXT")
    if t is None or p < 0:
        return t
    owner = next(i for i, m in enumerate(t["mods"])
                 if m["runs"][1][0] + 8 <= p < m["runs"][1][0] + 8 + m["runs"][1][1])
    # plant a bogus run start exactly at the cut, which is inside module 7's
    # source and before its end — the interval the two-tier shift cannot serve
    cut = p + len(b"MODULE7-PAYLOAD-STOCKTEXT")
    t["mods"][(owner + 1) % len(t["mods"])]["runs"][0] = (cut - 8, 4)
    return t


cc.bun_table = _table_with_run_inside
ok, out = cc.bun_source_patch(_bf, b"MODULE7-PAYLOAD-STOCKTEXT", b"OURS7")
cc.bun_table = _saved_bun_table
check("bun align: a run starting inside the owner's source is refused, not guessed at",
      not ok and "not what this writer models" in out and _bf.read_bytes() == _bb, out)

# growth is refused against the pad the ALIGNED splice actually needs, not the
# raw edit length — a +1 edit consumes 4 bytes of slack, and a pad of 3 is not
# enough however the arithmetic looks.
_bf.write_bytes(_bun_blob(pad=3))
ok, out = cc.bun_source_patch(_bf, b"MODULE3-PAYLOAD-STOCKTEXT",
                              b"MODULE3-PAYLOAD-STOCKTEXT5")
check("bun align: growth is gated on edit+filler, not the edit alone",
      not ok and "alignment filler" in out and _bf.read_bytes() == _bun_blob(pad=3), out)

_bf.write_bytes(_bb)
ok, out = cc.bun_source_patch(_bf, b"PAYLOAD-STOCKTEXT", b"Z")
check("bun ambiguous old: refused", not ok and "exactly 1" in out, out)

_bf.write_bytes(b"tiny")
ok, out = cc.bun_source_patch(_bf, b"a", b"b")
check("bun tiny non-blob file: survivable, defers",
      not ok and out.startswith("not code-split"), out)
shutil.rmtree(tmp7, ignore_errors=True)


# --- macho_bun_window: darwin blob locator (2026-08-29). The Mac stores the
# bundle as a `__BUN,__bun` segment mid-file — __LINKEDIT follows it, so the
# trailing-blob fallback never fires, and the runtime area carries its own
# copy of the trailer string, so a whole-file trailer search finds 2. Both
# facts measured on the real darwin-arm64 2.1.251 pristine.
def fake_macho(path, blob, magic=0xfeedfacf, mismatch=0):
    """Minimal Mach-O 64: one LC_SEGMENT_64 `__BUN` with one `__bun` section
    holding `blob`, a decoy trailer string before it, junk after it."""
    sizeofcmds = 72 + 80
    off = 32 + sizeofcmds + 256                       # runtime area before the segment
    hdr = _st.pack("<IIIIIIII", magic, 0x0100000C, 0, 2, 1, sizeofcmds, 0, 0)
    seg = _st.pack("<II", 0x19, sizeofcmds) + b"__BUN".ljust(16, b"\0")
    seg += _st.pack("<QQQQiiII", 0, len(blob), off, len(blob), 3, 3, 1, 0)
    sect = b"__bun".ljust(16, b"\0") + b"__BUN".ljust(16, b"\0")
    sect += _st.pack("<QQIIIIIIII", 0, len(blob) + mismatch, off, 14, 0, 0, 0x10000000, 0, 0, 0)
    runtime = (b"\x90" * 100 + cc.BUN_TRAILER + b"\x90" * 100).ljust(off - 32 - sizeofcmds, b"\0")
    Path(path).write_bytes(hdr + seg + sect + runtime + blob + b"\0" * 64)  # linkedit-ish tail
    return off, off + len(blob)


tmp8 = Path(tempfile.mkdtemp()); _mf = tmp8 / "macho"
want = fake_macho(_mf, _bb)
raw = _mf.read_bytes()
check("macho: synthetic file reproduces the ambiguity (2 trailers, none at EOF)",
      raw.count(cc.BUN_TRAILER) == 2 and not raw.endswith(cc.BUN_TRAILER))
check("macho_bun_window: locates __BUN,__bun despite the decoy trailer",
      cc.macho_bun_window(_mf) == want, str(cc.macho_bun_window(_mf)))
ok, out = cc.bun_source_patch(_mf, b"MODULE7-PAYLOAD-STOCKTEXT", b"OURS7")
nb = _mf.read_bytes()
check("macho: bun_source_patch writes through the segment window, size preserved",
      ok and len(nb) == len(raw) and nb.count(b"OURS7") == 1, out)
check("macho: bytes outside the section untouched",
      nb[:want[0]] == raw[:want[0]] and nb[want[1]:] == raw[want[1]:])

fake_macho(tmp8 / "framing", _bb, mismatch=4)         # section size != 8 + u64 prefix
check("macho_bun_window: None when the section's framing disagrees with the prefix",
      cc.macho_bun_window(tmp8 / "framing") is None)
fake_macho(tmp8 / "notmacho", _bb, magic=0xfeedfacd)
check("macho_bun_window: None for a non-Mach-O-64 magic",
      cc.macho_bun_window(tmp8 / "notmacho") is None)
check("macho_bun_window: None for junk, no exception",
      ((tmp8 / "tiny").write_bytes(b"?"), cc.macho_bun_window(tmp8 / "tiny"))[1] is None)
check("macho_bun_window: None for an ELF (bun_window's territory)",
      (fake_elf(tmp8 / "elf", b"x" * 64), cc.macho_bun_window(tmp8 / "elf"))[1] is None)
shutil.rmtree(tmp8, ignore_errors=True)
# --- 2026-08-26: the banner guard in tweakcc_apply_to was unreachable
# Every path through its try-block returns, so a check written AFTER the
# try/finally never ran once. Found while turning `tweakccPatches` on for
# darwin — the knob it guards had never been on wherever anyone was looking.
# Guarded statically, because the failure is one of reachability: a call-level
# test would have passed against the broken version too.
import ast
_src = (Path(__file__).resolve().parent.parent / "ccctl.py").read_text(encoding="utf-8")
_fn = next(n for n in ast.walk(ast.parse(_src))
           if isinstance(n, ast.FunctionDef) and n.name == "tweakcc_apply_to")
_try = next(s for s in _fn.body if isinstance(s, ast.Try))
_after = _fn.body[_fn.body.index(_try) + 1:]
check("tweakcc_apply_to: nothing sits after the try/finally (it would be dead code)",
      _after == [], str([(s.lineno, type(s).__name__) for s in _after]))
_guard = [n for n in ast.walk(_try) if isinstance(n, ast.Call)
          and getattr(n.func, "id", "") == "binary_contains"]
check("tweakcc_apply_to: the banner guard runs INSIDE the try", len(_guard) == 1,
      f"{len(_guard)} binary_contains calls in try")
# It must hand the caller a fallback, not abort: exiting 0 with no banner is a
# silently-rejected patch set, which is exactly what assemble()'s fallback is for.
_banner_if = next(n for n in ast.walk(_try) if isinstance(n, ast.If)
                  and any(call is _guard[0] for call in ast.walk(n.test)))
_dies = [n for n in ast.walk(_banner_if) if isinstance(n, ast.Call)
         and getattr(n.func, "id", "") == "die"]
check("tweakcc_apply_to: the guard returns a fallback reason rather than die()ing",
      _dies == [], f"{len(_dies)} die() calls in try")


# --- pe_bun_window: win32 blob locator (2026-08-31). win32 stores the bundle
# as a trailing overlay, but a signed build appends an Authenticode certificate
# table after it — 10,400 bytes on both 2.1.241 and 2.1.251, with alignment
# padding in front, so 10,690 bytes sit past the trailer and the file does NOT
# end with it. bun_source_patch's ends-with-trailer fallback therefore never
# fired here: every write fell through to tweakcc, which cannot write a
# code-split bundle at all (#969). That, and nothing about the prompts, is why
# win32 could not carry the 2.1.251 round. Same decoy-trailer ambiguity as
# darwin (2 hits in every win32 build measured), so the last hit is accepted
# only when the u64 framing agrees.
def fake_pe(path, blob, magic=b"MZ", sig=10400, pad=290, mismatch=0):
    """Minimal PE-shaped file: MZ magic, a runtime area carrying a decoy
    trailer, the blob as trailing overlay, then padding + a fake signature so
    the file does not end with the trailer."""
    body = bytearray(blob)
    if mismatch:
        _st.pack_into("<Q", body, 0, len(blob) - 8 + mismatch)   # break the length prefix
    runtime = magic + b"\x90" * 100 + cc.BUN_TRAILER + b"\x90" * 100
    off = len(runtime)
    Path(path).write_bytes(bytes(runtime) + bytes(body) + b"\x00" * pad + b"\xa5" * sig)
    return off, off + len(blob)


tmp9 = Path(tempfile.mkdtemp()); _pf = tmp9 / "claude.exe"
want_pe = fake_pe(_pf, _bb)
raw_pe = _pf.read_bytes()
check("pe: synthetic file reproduces the shape (2 trailers, none at EOF)",
      raw_pe.count(cc.BUN_TRAILER) == 2 and not raw_pe.endswith(cc.BUN_TRAILER))
check("pe_bun_window: locates the overlay behind the appended signature",
      cc.pe_bun_window(_pf) == want_pe, str(cc.pe_bun_window(_pf)))

ok, out = cc.bun_source_patch(_pf, b"MODULE7-PAYLOAD-STOCKTEXT", b"OURS7")
nb_pe = _pf.read_bytes()
check("pe: bun_source_patch writes through the overlay window, size preserved",
      ok and len(nb_pe) == len(raw_pe), out)
check("pe: the appended signature is untouched",
      nb_pe[want_pe[1]:] == raw_pe[want_pe[1]:])
check("pe: the module table still parses after the write",
      cc.bun_table(nb_pe[want_pe[0]:want_pe[1]]) is not None)

fake_pe(tmp9 / "framing", _bb, mismatch=4)
check("pe_bun_window: None when the length prefix disagrees with the trailer",
      cc.pe_bun_window(tmp9 / "framing") is None)
fake_pe(tmp9 / "notpe", _bb, magic=b"ZM")
check("pe_bun_window: None for a non-MZ magic",
      cc.pe_bun_window(tmp9 / "notpe") is None)
check("pe_bun_window: None for junk, no exception",
      ((tmp9 / "tiny").write_bytes(b"MZ"), cc.pe_bun_window(tmp9 / "tiny"))[1] is None)
check("pe_bun_window: None for an ELF (bun_window's territory)",
      (fake_elf(tmp9 / "elf", b"x" * 64), cc.pe_bun_window(tmp9 / "elf"))[1] is None)
# A bare blob that ends exactly at EOF is NOT this locator's job — it has no MZ
# magic, and bun_source_patch's original trailing-blob fallback still owns it.
# Asserting that keeps the two paths from quietly overlapping.
(tmp9 / "bare").write_bytes(_bb)
check("pe_bun_window: declines a bare blob (the trailing-blob fallback owns it)",
      cc.pe_bun_window(tmp9 / "bare") is None)


# The PRIMARY path is the section table, not the trailer search above: real
# win32 builds carry the blob in a `.bun` section exactly as linux does, and
# SizeOfRawData is rounded up to file alignment while VirtualSize frames the
# blob exactly. A locator that trusted the raw size would overshoot by the
# padding and fail the trailer check.
def fake_pe_sections(path, blob, secname=b".bun", pad=290, sig=10400, use_rawsize_as_vsize=False):
    e_lfanew, nsec, optsz = 0x80, 2, 0
    table_at = e_lfanew + 24 + optsz
    rawptr = table_at + 40 * nsec + 64
    rawsz = len(blob) + pad                        # file-alignment padding, as real builds have
    vsize = rawsz if use_rawsize_as_vsize else len(blob)
    head = bytearray(rawptr)
    head[0:2] = b"MZ"
    _st.pack_into("<I", head, 0x3C, e_lfanew)
    head[e_lfanew:e_lfanew + 4] = b"PE\0\0"
    _st.pack_into("<HH", head, e_lfanew + 4 + 2, nsec, 0)      # NumberOfSections
    _st.pack_into("<H", head, e_lfanew + 4 + 16, optsz)        # SizeOfOptionalHeader
    decoy = b".text".ljust(8, b"\0") + _st.pack("<IIII", 16, 0x1000, 16, table_at + 40 * nsec)
    sec = secname.ljust(8, b"\0") + _st.pack("<IIII", vsize, 0x2000, rawsz, rawptr)
    head[table_at:table_at + 40] = decoy.ljust(40, b"\0")
    head[table_at + 40:table_at + 80] = sec.ljust(40, b"\0")
    Path(path).write_bytes(bytes(head) + blob + b"\x00" * pad + b"\xa5" * sig)
    return rawptr, rawptr + len(blob)


tmp10 = Path(tempfile.mkdtemp())
want_sec = fake_pe_sections(tmp10 / "sec.exe", _bb)
check("pe_bun_window: reads the .bun section table (primary path)",
      cc.pe_bun_window(tmp10 / "sec.exe") == want_sec, str(cc.pe_bun_window(tmp10 / "sec.exe")))
ok, out = cc.bun_source_patch(tmp10 / "sec.exe", b"MODULE7-PAYLOAD-STOCKTEXT", b"OURS7")
check("pe section: bun_source_patch writes through it, size preserved",
      ok and len((tmp10 / "sec.exe").read_bytes()) == len(bytes(_bb)) + want_sec[0] + 290 + 10400, out)
fake_pe_sections(tmp10 / "rawsz.exe", _bb, use_rawsize_as_vsize=True)
check("pe_bun_window: None when the section size includes the alignment padding",
      cc.pe_bun_window(tmp10 / "rawsz.exe") is None)
fake_pe_sections(tmp10 / "nosec.exe", _bb, secname=b".data")
check("pe_bun_window: falls back to the trailer when there is no .bun section",
      cc.pe_bun_window(tmp10 / "nosec.exe") is not None)
shutil.rmtree(tmp9, ignore_errors=True)
shutil.rmtree(tmp10, ignore_errors=True)

# --- 2026-09-03: `diff custom` never honoured its own documented usage.
# Four positionals are shared with `diff stock A B [frag]`, so the fragment id
# landed in a version slot: one form died with "no stock snapshot for
# system-prompt-...", the other silently diffed all six fragments.
cases = [
    (("system-prompt-communication-style", None, None), (None, "system-prompt-communication-style")),
    (("2.1.259", "tool-description-todowrite", None), ("2.1.259", "tool-description-todowrite")),
    (("2.1.259", None, "tool-description-todowrite"), ("2.1.259", "tool-description-todowrite")),
    ((None, None, None), (None, None)),
    (("2.1.259", None, None), ("2.1.259", None)),
]
for args, want in cases:
    got = cc.diff_custom_args(*args)
    check(f"diff_custom_args{args} -> {want}", got == want, str(got))

# --- 2026-09-25: a shadowing old tweakcc must not stop a non-interactive apply
# Linux has a root-installed tweakcc 4.0.13 in /usr/bin and the operator's
# 4.3.3 under the ~/.npmrc prefix, which only ~/.zshrc puts on PATH. In a
# non-interactive shell the first on PATH was the old one and ccctl refused to run.
if sys.platform != "win32":
    import contextlib, io
    from unittest import mock
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        def fake(path, out):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"#!/bin/sh\necho {out}\n")
            path.chmod(0o755)
            return path
        old = fake(tmp / "usr" / "bin" / "tweakcc", "4.0.13")
        new = fake(tmp / "npm" / "bin" / "tweakcc", "4.3.3")
        (tmp / "home").mkdir()
        (tmp / "home" / ".npmrc").write_text(f"fund=false\nprefix = {tmp / 'npm'}\n")
        env = {"PATH": f"{old.parent}{os.pathsep}{new.parent}", "HOME": str(tmp / "home")}
        with mock.patch.dict(os.environ, env):
            check("tweakcc_candidates: PATH order, then the npm prefix, each file once",
                  cc.tweakcc_candidates() == [str(old), str(new)], str(cc.tweakcc_candidates()))
            os.environ["PATH"] = str(old.parent)
            check("tweakcc_candidates: the npm prefix is found with PATH missing it",
                  cc.tweakcc_candidates() == [str(old), str(new)], str(cc.tweakcc_candidates()))
            cc.tweakcc.cache_clear()
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                chosen = cc.tweakcc()
            check("tweakcc: skips a shadowing 4.0.13 and takes the prefix's 4.3.3",
                  chosen == str(new), chosen)
            check("tweakcc: the skip note goes to stderr, never into a hook's stdout",
                  "skipping" in err.getvalue() and not out.getvalue(), repr(out.getvalue()))
            (tmp / "home" / ".npmrc").write_text("fund=false\n")
            cc.tweakcc.cache_clear()
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    cc.tweakcc()
                refused = False
            except SystemExit:
                refused = True
            check("tweakcc: refuses when only a too-old one exists", refused)
    cc.tweakcc.cache_clear()
    cc.tweakcc_node.cache_clear()

print()
print(("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
