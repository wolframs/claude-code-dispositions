"""build_local_tweakcc: corepack is found beside the real node, not only on PATH.

Run from anywhere:  python tools/tests/test_build_corepack.py

macOS 2026-09-08: the 2.1.261 rebuild died with
`[Errno 2] No such file or directory: 'corepack'` because ~/.local/bin links
only node/npm/npx out of ~/.hermes/node/bin. corepack was there all along, one
readlink away. The ladder: PATH -> sibling of the resolved node -> node's own
bundled corepack script -> the bare name (so a real absence still fails with
corepack's message, not a path invented here).

win32 2026-09-11: the fixture built the POSIX layout only, so this file failed
here (rung 2 looks for `corepack.cmd` on win32, the fixture wrote `corepack`),
and rung 3 knew only the `<prefix>/lib/node_modules` layout — a nodejs.org
install keeps node.exe at the prefix root with `<prefix>\\node_modules` and no
`lib\\` (measured: C:\\Program Files\\nodejs). Both rungs are now built for the
running platform, and rung 3 tries both layouts.
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

WIN32 = sys.platform == "win32"
# what rung 2 must find beside the real node, per platform
SIBLING = "corepack.cmd" if WIN32 else "corepack"
NODE = "node.exe" if WIN32 else "node"

spec = importlib.util.spec_from_file_location(
    "build_local_tweakcc",
    str(Path(__file__).resolve().parent.parent / "build_local_tweakcc.py"))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def which_map(mapping):
    return lambda name, *a, **k: mapping.get(name)


with tempfile.TemporaryDirectory() as td:
    root = Path(td).resolve()
    real_bin = root / "dist" / "bin"
    real_bin.mkdir(parents=True)
    real_node = real_bin / NODE
    real_node.write_text("")
    link_bin = root / "local" / "bin"
    link_bin.mkdir(parents=True)
    try:
        os.symlink(real_node, link_bin / NODE)
        linked_node = str(link_bin / NODE)
        through = " (through the symlink)"
    except OSError as exc:  # win32 without Developer Mode: no symlink privilege
        print(f"NOTE symlink unavailable ({exc.__class__.__name__}); "
              "rung 2 is checked on the real path without indirection")
        linked_node = str(real_node)
        through = ""

    orig_which = b.shutil.which
    try:
        # 1. PATH wins when it has one
        b.shutil.which = which_map({"corepack": "/usr/bin/corepack", "node": linked_node})
        check("PATH corepack is used as-is", b.corepack_cmd() == ["/usr/bin/corepack"])

        # 2. sibling of the *resolved* node, under this platform's launcher name
        (real_bin / SIBLING).write_text("")
        b.shutil.which = which_map({"node": linked_node})
        got = b.corepack_cmd()
        check(f"sibling {SIBLING} of the real node{through} is found",
              got == [str(real_bin / SIBLING)], str(got))

        # 3a. node's bundled script, POSIX/nvm layout: <prefix>/bin/node, <prefix>/lib
        (real_bin / SIBLING).unlink()
        script = root / "dist" / "lib" / "node_modules" / "corepack" / "dist" / "corepack.js"
        script.parent.mkdir(parents=True)
        script.write_text("")
        got = b.corepack_cmd()
        check("falls back to node's corepack.js in the lib/ layout, run under the real node",
              got == [str(real_node), str(script)], str(got))
        script.unlink()

        # 3b. the same, nodejs.org win32 layout: node.exe at the prefix root,
        #     node_modules beside it, no lib/ anywhere
        flat_prefix = root / "nodejs"
        flat_prefix.mkdir()
        flat_node = flat_prefix / NODE
        flat_node.write_text("")
        flat_script = flat_prefix / "node_modules" / "corepack" / "dist" / "corepack.js"
        flat_script.parent.mkdir(parents=True)
        flat_script.write_text("")
        b.shutil.which = which_map({"node": str(flat_node)})
        got = b.corepack_cmd()
        check("falls back to node's corepack.js in the lib-less (nodejs.org win32) layout",
              got == [str(flat_node), str(flat_script)], str(got))
        flat_script.unlink()
        check("bare name when that layout has no corepack either",
              b.corepack_cmd() == ["corepack"], str(b.corepack_cmd()))

        # 4. nothing anywhere -> the bare name, so corepack's own error surfaces
        b.shutil.which = which_map({"node": linked_node})
        check("bare name when nothing is found", b.corepack_cmd() == ["corepack"])
        b.shutil.which = which_map({})
        check("bare name with no node either", b.corepack_cmd() == ["corepack"])
    finally:
        b.shutil.which = orig_which

check("module-level COREPACK is an argv prefix (list), spliced with *",
      isinstance(b.COREPACK, list) and len(b.COREPACK) >= 1)

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all corepack checks pass'}")
sys.exit(1 if fails else 0)
