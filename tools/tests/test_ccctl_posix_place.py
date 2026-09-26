"""Exercise place()'s POSIX branch (symlink launcher + .stock parking).

Run from anywhere:  python tools/tests/test_ccctl_posix_place.py
Uses tiny fake binaries in a temp sandbox and forces the POSIX branch, so
it is meaningful on win32 too (where that branch never runs for real).
Needs symlink permission (Developer Mode on Windows); falls back to
structural assertions when unavailable.
"""
import importlib.util, os, shutil, sys, tempfile
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

MARKERS = ["PATCHED-MARKER-XYZ"]
VER = "9.9.9"

sandbox = Path(tempfile.mkdtemp())
try:
    home = sandbox / "home"
    vdir = home / ".local" / "share" / "claude" / "versions"
    bindir = home / ".local" / "bin"
    vdir.mkdir(parents=True); bindir.mkdir(parents=True)
    staging = sandbox / "staging"; staging.mkdir()

    # fake pristine already installed + launcher symlink pointing at it
    vfile = vdir / VER
    vfile.write_bytes(b"STOCK-BINARY-CONTENT " * 50)
    launcher = bindir / "claude"
    try:
        os.symlink(vfile, launcher)
        have_symlink = True
    except OSError as e:
        have_symlink = False
        print(f"note: symlinks unavailable here ({e}); launcher step will be checked structurally")

    # patch module globals to point into the sandbox and take the POSIX branch
    cc.STAGING = staging
    cc.sys = type("S", (), {"platform": "linux"})()      # POSIX branch
    cc.versions_dirs = lambda: [vdir]
    cc.launcher_path = lambda: launcher
    cc.repo_markers = lambda cfg: MARKERS

    staged = staging / f"claude-{VER}-assembled"
    staged.write_bytes(b"NEW-CONTENT " + MARKERS[0].encode() + b" tail")

    if have_symlink:
        result = cc.place({}, VER, staged, vfile)
        stock = vdir / (VER + ".stock")
        check("posix: patched binary landed at versions/<ver>",
              vfile.read_bytes().startswith(b"NEW-CONTENT"))
        check("posix: pristine parked at versions/<ver>.stock",
              stock.exists() and stock.read_bytes().startswith(b"STOCK-BINARY"))
        # NB: Windows readlink returns a \\?\-prefixed path, so compare resolved
        check("posix: launcher is a symlink resolving to versions/<ver>",
              launcher.is_symlink() and launcher.resolve() == vfile.resolve())
        check("posix: launcher resolves to patched content",
              launcher.read_bytes().startswith(b"NEW-CONTENT"))
        check("posix: no .new leftover", not (vdir / (VER + ".new")).exists())
        check("posix: no .part leftover", not list(vdir.glob("*.part")))
        check("posix: staged consumed", not staged.exists())
        check("posix: place returns the version file", result == vfile)

        # pristine_source must now find the parked .stock
        cc.pristine_source.__globals__  # ensure module-level lookup
        found = cc.pristine_source(VER, MARKERS)
        check("posix: pristine_source finds versions/<ver>.stock after update",
              found == stock, str(found))

        # idempotency: run place() again with a second staged build
        staged2 = staging / f"claude-{VER}-assembled2"
        staged2.write_bytes(b"NEWER-CONTENT " + MARKERS[0].encode())
        cc.place({}, VER, staged2, stock)
        check("posix: second place() keeps the original pristine in .stock",
              stock.read_bytes().startswith(b"STOCK-BINARY"))
        check("posix: second place() updates the live binary",
              vfile.read_bytes().startswith(b"NEWER-CONTENT"))
        check("posix: launcher still valid after second place",
              launcher.is_symlink() and launcher.read_bytes().startswith(b"NEWER-CONTENT"))
    else:
        # structural: pristine parking must happen by COPY before vfile is touched
        import inspect
        src = inspect.getsource(cc.place)
        posix_part = src.split("# POSIX:")[1]
        check("posix: never renames vfile away from under the symlink",
              "os.rename(vfile" not in posix_part)
        check("posix: lands staged next to vfile then os.replace",
              '".new"' in posix_part and "os.replace(tmp, vfile)" in posix_part)
        check("posix: parks pristine via safe_put (copy)", "safe_put(src, stock" in posix_part)

    print()
    print("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}")
finally:
    shutil.rmtree(sandbox, ignore_errors=True)
sys.exit(1 if fails else 0)
