#!/usr/bin/env python3
"""Teach tweakcc to patch code-split Claude Code bundles (CC >= 2.1.243).

WHY
---
tweakcc picks the module it patches by NAME:

    function l(e){return e.endsWith(`/claude`)||e===`claude`
      ||e.endsWith(`/claude.exe`)||e===`claude.exe`
      ||e.endsWith(`/src/entrypoints/cli.js`)||e===`src/entrypoints/cli.js`
      ||e.endsWith(`/cli`)||e===`cli`}

For years that was right: Bun bundled Claude Code into ONE module called
`cli`, so the entrypoint WAS the app. Code splitting in 2.1.243 broke the
equivalence and left the name alone. The entrypoint is now a ~20 KB loader,
still called `B:/~BUN/root/cli`, and the app lives in ~2000 sibling chunks.
The predicate still matches on the first try, so tweakcc hands its patchers
0.015% of the program and every locator reports "failed to find X component"
— pointing at its patterns rather than at its input. Nothing errors; it is
simply looking at the wrong 20 KB. Upstream #969 files the write half of
this; the read half is the bigger one.

Measured on CC 2.1.251 win32-x64 (2026-08-31):
  entry module  B:/~BUN/root/cli   19,688 bytes, 0 UI markers
  Text component            chunk-dyqmsmtx.js
  Box component             chunk-42zxntnv.js, chunk-mn5egq73.js, chunk-kja40jst.js
  1968 modules, 38,480,414 bytes of source in total

WHAT THIS DOES
--------------
Keeps tweakcc's whole patcher contract — "one string in, one string out" —
and changes only what that string is:

  extract : concatenate EVERY module's source, separated by a marker, instead
            of returning the first name-matched module. Cross-module locators
            then resolve, which matters because a single patcher needs
            identifiers from several chunks (userMessageDisplay wants both
            Text and Box, and they live in different ones).
  repack  : REPLACE tweakcc's serializer `function S` wholesale (see NEW_S). It
            splits the patched string back on the marker, but tweakcc's own `S`
            was written for an older Bun blob and drops the five tail tables +
            two shared regions + bytecode alignment that CC 2.1.251's Bun build
            requires while copying the `flags` word that says they're present —
            which is what made every repacked binary segfault. NEW_S rebuilds
            the blob faithfully. Full write-up: notes/2026-08-31-tweakcc-code-split.md
  verify  : skip the node --check pass when the string is a concatenation. It
            writes `bundle.cjs` and checks it as CommonJS, which fails on any
            ESM chunk regardless (that is the "Cannot use import statement
            outside a module" seen on 2.1.251) — and a concatenation of 2000
            modules is not valid JS under any goal symbol. The `.cjs` name is
            corrected anyway for the single-module path.

The marker is wrapped in newlines on purpose: tweakcc's patterns use bounded
`.{0,N}` runs, and `.` does not cross a newline in JS regex, so no pattern can
match across a module boundary and swallow a separator.

Idempotent: always regenerates from the pristine `.orig` copies, so running it
twice is the same as running it once. Re-run after `npm i -g tweakcc`.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

BS = chr(92)
SEP_JS = '"' + BS + 'n//__TWEAKCC_MODULE_BOUNDARY__' + BS + 'n"'
BIN_JS = '"//__TWEAKCC_BINARY_MODULE__"'
CONST = "const __TWKSEP=" + SEP_JS + ";const __TWKBIN=" + BIN_JS + ";"


def find_dist():
    for c in [Path.home() / "AppData/Roaming/npm/node_modules/tweakcc/dist",
              Path.home() / ".bun/install/global/node_modules/tweakcc/dist",
              Path("/usr/lib/node_modules/tweakcc/dist"),
              Path("/usr/local/lib/node_modules/tweakcc/dist")]:
        if c.is_dir():
            return c
    sys.exit("tweakcc dist not found — pass its path as argv[1]")


def pristine(p):
    """Return the untouched source, creating the .orig snapshot on first run."""
    orig = p.with_suffix(p.suffix + ".orig")
    if not orig.exists():
        shutil.copy2(p, orig)
    return orig.read_text(encoding="utf-8")


def sub_once(src, old, new, label):
    n = src.count(old)
    if n != 1:
        sys.exit(f"FAIL: anchor for {label} matched {n}x, expected 1 — "
                 "tweakcc's minified shape changed; re-derive against dist/")
    return src.replace(old, new)


def replace_S(src, new_body):
    """Replace tweakcc's whole blob serializer `function S(e,t,n,r){...}` with
    `new_body`.

    The serializer's own minified signature has stayed stable, but the next
    helper's signature has not: tweakcc 4.3.3 changed `function C(e,t,r,i=` to
    `function C(e,t,r,i){`.  Its name is minifier-assigned too, so bracket on
    the next top-level minified function declaration rather than either detail.
    The generated serializer contains no nested function declaration; refuse
    ambiguous input instead of consuming an arbitrary later function."""
    head = "function S(e,t,n,r){"
    a = src.find(head)
    next_functions = list(re.finditer(r"}function [$A-Za-z_][\w$]*\(",
                                      src[a + len(head):])) if a >= 0 else []
    b = a + len(head) + next_functions[0].start() + 1 if next_functions else -1
    if a < 0 or b < 0 or src.find(head, a + 1) != -1:
        sys.exit("FAIL: could not bracket `function S` — tweakcc's minified "
                 "shape changed; re-derive against dist/")
    return src[:a] + new_body + src[b:]


# The faithful re-serializer that replaces tweakcc's `function S`.
#
# tweakcc's original S was written for an older Bun blob whose only regions were
# the per-module strings + the module table + compileExecArgv + the 32-byte
# Offsets. The Bun build Claude Code >= 2.1.243 ships adds, after the module
# table, five optional tables (source hashes, builtin bytecode, a bytecode
# string-table pointer, a startup-module count, a module-info string-table
# pointer), keeps two large SHARED regions the string table points at, and
# 128-byte-aligns every module's JSC bytecode (file offset == 120 mod 128, so
# the mmapped section start + 8-byte header lands it 128-aligned). The old S
# reproduced none of that yet copied the `flags` word verbatim — flags that
# tell Bun's loader all those tables are present. The loader then reads them
# from offsets that now hold the Offsets struct, gets a garbage builtin count,
# and segfaults before any user code. Proven by a no-op round trip (extract ->
# repack with zero content change) crashing identically.
#
# This rewrite preserves every region Bun expects. It reads the original tail
# tables out of `e` gated on the original flags (so it degrades cleanly on a
# build that lacks one), relocates the two shared regions and rewrites their
# pointer records, 128-aligns bytecode and the (also-bytecode) shared string
# table, and — for the handful of modules whose source tweakcc actually edited
# — drops that module's now-stale bytecode/moduleInfo/originPath and zeroes its
# source hash so Bun recompiles it from the patched source. It clears only
# SOURCE_TEXT_CONTIGUOUS (bit 4): the interleaved layout is no longer one
# contiguous source-text run, and that bit merely gates a madvise(DONTNEED)
# that is a no-op stub on win32 anyway. Everything is placed by explicit
# offset, so field ORDER does not matter to the loader.
#
# CC 2.1.257 ships a non-empty builtin-bytecode table. Its records and pointed
# bytecode are relocated below with Bun's required 120-mod-128 file alignment.
NEW_S = r'''function S(e,t,n,r){
var TS=__TWKSEP,BIN=__TWKBIN,MAG=o,Z={offset:0,length:0};
var M=[];d(e,t,r,function(m){M.push(m)});var mc=M.length;
var SP=null;if(n){var ns=Buffer.isBuffer(n)?n.toString("utf-8"):String(n);
if(ns.indexOf(TS)!==-1){SP=ns.split(TS);
if(SP.length!==mc)throw Error("tweakcc split mismatch: "+SP.length+" parts vs "+mc+" modules")}}
var fl=t.flags,ro=t.modulesPtr.offset+t.modulesPtr.length;
var oSH=null;if(fl&32){oSH=s(e,{offset:ro,length:mc*4});ro+=mc*4}
var oBI=[];if(fl&64){var bic=e.readUInt32LE(ro);ro+=4;
for(var j=0;j<bic;j++){var bid=e.readUInt32LE(ro),bof=e.readUInt32LE(ro+4),bln=e.readUInt32LE(ro+8);ro+=12;
oBI.push({id:bid,bytes:{offset:bof,length:bln}})}}
var oBST=Z;if(fl&128){oBST={offset:e.readUInt32LE(ro),length:e.readUInt32LE(ro+4)};ro+=8}
var oSU=0,hSU=false;if(fl&256){oSU=e.readUInt32LE(ro);hSU=true;ro+=4}
var oMIST=Z;if(fl&512){oMIST={offset:e.readUInt32LE(ro),length:e.readUInt32LE(ro+4)};ro+=8}
var W=[],u=0;
function put(buf){var of=u;W.push([of,buf]);u+=buf.length+1;return{offset:of,length:buf.length}}
function putA(buf){var m=u%128,pad=m<=120?120-m:248-m;u+=pad;var of=u;W.push([of,buf]);u+=buf.length+1;return{offset:of,length:buf.length}}
function putRaw(buf){var of=u;W.push([of,buf]);u+=buf.length;return{offset:of,length:buf.length}}
var P=[],shOut=Buffer.alloc(mc*4);
for(var k=0;k<mc;k++){var m=M[k];
var nameP=put(s(e,m.name));
var edited=false,cbuf;
if(SP){var pk=SP[k];if(pk===BIN){cbuf=s(e,m.contents)}else{cbuf=Buffer.from(pk,"utf-8");edited=!cbuf.equals(s(e,m.contents))}}
else{cbuf=s(e,m.contents)}
var contentsP=put(cbuf);
var smP=m.sourcemap.length?put(s(e,m.sourcemap)):Z;
var bcP=Z,miP=Z,bcopP=Z;
if(!edited){
if(m.bytecode.length)bcP=putA(s(e,m.bytecode));
if(m.moduleInfo.length)miP=put(s(e,m.moduleInfo));
if(m.bytecodeOriginPath.length)bcopP=put(s(e,m.bytecodeOriginPath))}
P.push({name:nameP,contents:contentsP,sourcemap:smP,bytecode:bcP,moduleInfo:miP,bcop:bcopP,enc:m.encoding,loader:m.loader,fmt:m.moduleFormat,side:m.side});
if(!edited&&oSH)oSH.copy(shOut,k*4,k*4,k*4+4)}
var biOut=Buffer.alloc(4+oBI.length*12);biOut.writeUInt32LE(oBI.length,0);
for(var k=0;k<oBI.length;k++){var bp=putA(s(e,oBI[k].bytes)),bo=4+k*12;
biOut.writeUInt32LE(oBI[k].id,bo);biOut.writeUInt32LE(bp.offset,bo+4);biOut.writeUInt32LE(bp.length,bo+8)}
var bstP=oBST.length?putA(s(e,oBST)):Z;
var mistP=oMIST.length?put(s(e,oMIST)):Z;
var argvP=put(s(e,t.compileExecArgvPtr));
var tbl=Buffer.alloc(mc*r);
for(var k=0;k<mc;k++){var q=P[k],o2=k*r;
tbl.writeUInt32LE(q.name.offset,o2);tbl.writeUInt32LE(q.name.length,o2+4);
tbl.writeUInt32LE(q.contents.offset,o2+8);tbl.writeUInt32LE(q.contents.length,o2+12);
tbl.writeUInt32LE(q.sourcemap.offset,o2+16);tbl.writeUInt32LE(q.sourcemap.length,o2+20);
tbl.writeUInt32LE(q.bytecode.offset,o2+24);tbl.writeUInt32LE(q.bytecode.length,o2+28);
if(r===52){tbl.writeUInt32LE(q.moduleInfo.offset,o2+32);tbl.writeUInt32LE(q.moduleInfo.length,o2+36);
tbl.writeUInt32LE(q.bcop.offset,o2+40);tbl.writeUInt32LE(q.bcop.length,o2+44);
tbl.writeUInt8(q.enc,o2+48);tbl.writeUInt8(q.loader,o2+49);tbl.writeUInt8(q.fmt,o2+50);tbl.writeUInt8(q.side,o2+51)}
else{tbl.writeUInt8(q.enc,o2+32);tbl.writeUInt8(q.loader,o2+33);tbl.writeUInt8(q.fmt,o2+34);tbl.writeUInt8(q.side,o2+35)}}
var modP=putRaw(tbl);
if(fl&32)putRaw(shOut);
if(fl&64)putRaw(biOut);
if(fl&128){var br=Buffer.alloc(8);br.writeUInt32LE(bstP.offset,0);br.writeUInt32LE(bstP.length,4);putRaw(br)}
if(fl&256){var su=Buffer.alloc(4);su.writeUInt32LE(oSU,0);putRaw(su)}
if(fl&512){var mr=Buffer.alloc(8);mr.writeUInt32LE(mistP.offset,0);mr.writeUInt32LE(mistP.length,4);putRaw(mr)}
var byteCount=u;u+=32;var magOff=u;u+=MAG.length;
var b=Buffer.alloc(u);
for(var w=0;w<W.length;w++)W[w][1].copy(b,W[w][0]);
var C=byteCount;
b.writeBigUInt64LE(BigInt(byteCount),C);C+=8;
b.writeUInt32LE(modP.offset,C);b.writeUInt32LE(modP.length,C+4);C+=8;
b.writeUInt32LE(t.entryPointId,C);C+=4;
b.writeUInt32LE(argvP.offset,C);b.writeUInt32LE(argvP.length,C+4);C+=8;
b.writeUInt32LE((fl&~16)>>>0,C);
MAG.copy(b,magOff);
return b}
'''


def main():
    dist = Path(sys.argv[1]) if len(sys.argv) > 1 else find_dist()
    native = next(dist.glob("nativeInstallation-*.mjs"))
    config = next(dist.glob("config-*.mjs"))

    # ---- nativeInstallation: extract + repack -------------------------------
    n = pristine(native)

    old_extract = (
        "d(r,n,a,(e,n,i)=>{if(t(`extractClaudeJsFromNativeInstallation: Module ${i}: ${n}`),!l(n))return;"
        "let a=s(r,e.contents);return t(`extractClaudeJsFromNativeInstallation: Found claude module, "
        "contents length=${a.length}`),a.length>0?a:void 0})||"
        "(t(`extractClaudeJsFromNativeInstallation: claude module not found in any module`),null)"
    )
    new_extract = (
        "(()=>{let P=[];d(r,n,a,(m2,n2,i2)=>{let B=s(r,m2.contents),T=B.toString(`utf-8`);"
        "P.push(Buffer.compare(Buffer.from(T,`utf-8`),B)===0?B:Buffer.from(__TWKBIN,`utf-8`))});"
        "if(!P.length)return t(`extractClaudeJsFromNativeInstallation: no modules`),null;"
        "if(P.length===1)return P[0];"
        "let SB=Buffer.from(__TWKSEP,`utf-8`),O2=[];"
        "for(let q=0;q<P.length;q++){if(q)O2.push(SB);O2.push(P[q])}"
        "t(`extractClaudeJsFromNativeInstallation: concatenated ${P.length} modules`);"
        "return Buffer.concat(O2)})()"
    )
    n = sub_once(n, old_extract, new_extract, "extract")

    n = replace_S(n, NEW_S)
    n = CONST + "\n" + n
    native.write_text(n, encoding="utf-8")

    # ---- config: the parse check -------------------------------------------
    c = pristine(config)
    # Minification renames both the function and its parameter whenever source
    # patchers change. Anchor on the parse check's unique mkdtemp call and carry
    # the actual parameter/local/fs identifiers through the insertion.
    check_re = re.compile(
        r"([$A-Za-z_][\w$]*)=>\{let ([$A-Za-z_][\w$]*);try\{\2="
        r"([$A-Za-z_][\w$]*)\.mkdtempSync\("
    )
    check_matches = list(check_re.finditer(c))
    if len(check_matches) != 1:
        sys.exit(f"FAIL: anchor for parse-check skip matched "
                 f"{len(check_matches)}x, expected 1 — tweakcc's minified "
                 "shape changed; re-derive against dist/")
    cm = check_matches[0]
    param, local, fsvar = cm.group(1), cm.group(2), cm.group(3)
    new_check = (
        f"{param}=>{{if({param}&&(Buffer.isBuffer({param})?"
        f"{param}.indexOf(__TWKSEP)!==-1:String({param}).indexOf(__TWKSEP)!==-1))return;"
        f"let {local};try{{{local}={fsvar}.mkdtempSync("
    )
    c = c[:cm.start()] + new_check + c[cm.end():]
    # A chunk is ESM; checking it as .cjs fails on its own import statements.
    c = sub_once(c, "`bundle.cjs`", "`bundle.mjs`", "parse-check extension")
    c = CONST + "\n" + c
    config.write_text(c, encoding="utf-8")

    # ---- verify -------------------------------------------------------------
    ok = True
    for p, needles in ((native, ["__TWKSEP", "concatenated ${P.length} modules",
                                  "tweakcc split mismatch", "writeBigUInt64LE",
                                  "oBI.push", "biOut.writeUInt32LE"]),
                       (config, ["__TWKSEP", "bundle.mjs"])):
        body = p.read_text(encoding="utf-8")
        for needle in needles:
            hit = needle in body
            ok &= hit
            print(f"  {'ok  ' if hit else 'MISS'} {p.name}: {needle}")
    r = subprocess.run(["node", "--input-type=module", "--check"],
                       input=native.read_text(encoding="utf-8"),
                       capture_output=True, text=True)
    print(f"  {'ok  ' if r.returncode == 0 else 'MISS'} nativeInstallation parses as ESM")
    ok &= r.returncode == 0
    r = subprocess.run(["node", "--input-type=module", "--check"],
                       input=config.read_text(encoding="utf-8"),
                       capture_output=True, text=True)
    print(f"  {'ok  ' if r.returncode == 0 else 'MISS'} config parses as ESM")
    ok &= r.returncode == 0

    print("\nPATCHED" if ok else "\nFAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
