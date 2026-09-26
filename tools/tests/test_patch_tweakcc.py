#!/usr/bin/env python3
"""Regression checks for the local tweakcc code-split adapter."""

import importlib.util
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "patch_tweakcc", ROOT / "tools" / "patch_tweakcc.py"
)
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"FAIL {label}: {detail}")
    print(f"PASS {label}")


replacement = "function S(e,t,n,r){return Buffer.alloc(0)}\n"

old_neighbor = (
    "prefix;function S(e,t,n,r){let i=[];return i}"
    "function C(e,t,r,i=void 0){return i}suffix"
)
old_result = patch.replace_S(old_neighbor, replacement)
check("serializer: brackets the older default-parameter neighbor",
      old_result == "prefix;" + replacement
      + "function C(e,t,r,i=void 0){return i}suffix")

current_neighbor = (
    "prefix;function S(e,t,n,r){let i=[];return i}"
    "function C(e,t,r,i){return i}suffix"
)
current_result = patch.replace_S(current_neighbor, replacement)
check("serializer: brackets tweakcc 4.3.3's current neighbor",
      current_result == "prefix;" + replacement
      + "function C(e,t,r,i){return i}suffix")

renamed_neighbor = (
    "prefix;function S(e,t,n,r){let i=[];return i}"
    "function $z(e){return e}suffix"
)
renamed_result = patch.replace_S(renamed_neighbor, replacement)
check("serializer: does not depend on the next helper's minified name",
      renamed_result == "prefix;" + replacement
      + "function $z(e){return e}suffix")

# Execute NEW_S against a minimal graph carrying one non-empty builtin table.
# CC 2.1.251 had count=0; CC 2.1.257 has 134 records. Each record is
# {u32 internal-module id, StringPointer bytecode}; both the pointer and its
# 128-byte-aligned target must be relocated during a rebuild.
serializer_probe = f"""
const o=Buffer.from("\\n---- Bun! ----\\n");
{patch.CONST}
function s(e,p){{return e.subarray(p.offset,p.offset+p.length)}}
function d(e,t,r,visit){{
  const count=t.modulesPtr.length/r;
  for(let k=0;k<count;k++){{
    let q=t.modulesPtr.offset+k*r;
    const ptr=off=>({{offset:e.readUInt32LE(q+off),length:e.readUInt32LE(q+off+4)}});
    const m={{name:ptr(0),contents:ptr(8),sourcemap:ptr(16),bytecode:ptr(24),
      moduleInfo:ptr(32),bytecodeOriginPath:ptr(40),encoding:e[q+48],loader:e[q+49],
      moduleFormat:e[q+50],side:e[q+51]}};
    visit(m,s(e,m.name).toString("utf8"),k);
  }}
}}
{patch.NEW_S}
const e=Buffer.alloc(900);
function put(at,bytes){{Buffer.from(bytes).copy(e,at);return{{offset:at,length:Buffer.byteLength(bytes)}}}}
const name=put(0,"cli"),contents=put(16,"console.log(1)"),empty={{offset:0,length:0}};
const bytecode=put(120,Buffer.from([1,2,3,4])),builtin=put(248,Buffer.from([9,8,7,6,5]));
const bst=put(376,Buffer.from([4,3,2,1])),mist=put(420,"module-info"),argv=put(460,"--smol");
const modulesOffset=512;
for(const [off,p] of [[0,name],[8,contents],[16,empty],[24,bytecode],[32,empty],[40,empty]]){{
  e.writeUInt32LE(p.offset,modulesOffset+off);e.writeUInt32LE(p.length,modulesOffset+off+4);
}}
e[modulesOffset+48]=1;e[modulesOffset+49]=2;e[modulesOffset+50]=3;e[modulesOffset+51]=4;
let tail=modulesOffset+52;e.writeUInt32LE(0x12345678,tail);tail+=4;
e.writeUInt32LE(1,tail);e.writeUInt32LE(77,tail+4);e.writeUInt32LE(builtin.offset,tail+8);
e.writeUInt32LE(builtin.length,tail+12);tail+=16;
e.writeUInt32LE(bst.offset,tail);e.writeUInt32LE(bst.length,tail+4);tail+=8;
e.writeUInt32LE(1,tail);tail+=4;
e.writeUInt32LE(mist.offset,tail);e.writeUInt32LE(mist.length,tail+4);
const t={{modulesPtr:{{offset:modulesOffset,length:52}},compileExecArgvPtr:argv,
  entryPointId:0,flags:0x3ff}};
const out=S(e,t,null,52), offsets=out.length-o.length-32;
const modOff=out.readUInt32LE(offsets+8),modLen=out.readUInt32LE(offsets+12);
const bi=modOff+modLen+4;
const count=out.readUInt32LE(bi),id=out.readUInt32LE(bi+4);
const ptr={{offset:out.readUInt32LE(bi+8),length:out.readUInt32LE(bi+12)}};
console.log(JSON.stringify({{count,id,mod:ptr.offset%128,bytes:[...s(out,ptr)]}}));
"""
probe = subprocess.run(["node"], input=serializer_probe, text=True,
                       capture_output=True)
check("serializer: synthetic builtin graph executes", probe.returncode == 0,
      probe.stderr)
result = json.loads(probe.stdout)
check("serializer: preserves a non-empty builtin-bytecode table",
      result == {"count": 1, "id": 77, "mod": 120,
                 "bytes": [9, 8, 7, 6, 5]}, str(result))

print("\nALL PASS")
