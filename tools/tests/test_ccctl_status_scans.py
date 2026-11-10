"""`status` reuses its binary scans; nothing that writes a binary does.

Run from anywhere:  python tools/tests/test_ccctl_status_scans.py
On CC 2.1.321 (342 MB) the session-start check took 5.4 s of its 10 s timeout:
`pristine_source` ran twice, each time scanning the patched binary and then
the parked one, and the live bundle was read six times. Also covers the
commands that keep state in the workspace refusing to run anywhere else.
"""
import importlib.util, shutil, subprocess, sys, tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "ccctl.py"
spec = importlib.util.spec_from_file_location("ccctl", str(TOOL))
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
    vdir = sandbox / "versions"
    vdir.mkdir()
    patched, stock = vdir / VER, vdir / (VER + ".stock")
    patched.write_bytes(b"NEW-CONTENT " + MARKERS[0].encode())
    stock.write_bytes(b"STOCK-BINARY-CONTENT " * 50)
    cc.versions_dirs = lambda: [vdir]
    cc.TWEAKCC_DIR = sandbox / "no-tweakcc"

    scans = []
    real_contains = cc.binary_contains
    def counting(binary, needles, live=True):
        scans.append((Path(binary).name, live))
        return real_contains(binary, needles, live)
    cc.binary_contains = counting

    check("pristine_source: the parked copy is the answer",
          cc.pristine_source(VER, MARKERS) == stock)
    check("pristine_source: and the patched build beside it is not scanned to find it",
          scans == [(stock.name, False)], str(scans))

    del scans[:]
    cc.pristine_source(VER, MARKERS)
    cc.pristine_source(VER, MARKERS)
    check("outside read_only_scans every question is asked of the file again",
          len(scans) == 2, str(scans))

    del scans[:]
    with cc.read_only_scans():
        first = cc.pristine_source(VER, MARKERS)
        again = cc.pristine_source(VER, MARKERS)
        blob = cc.live_blob(stock)
        check("read_only_scans: the same binary is scanned once",
              first == again == stock and len(scans) == 1, str(scans))
        check("read_only_scans: and its live bundle is read once",
              cc.live_blob(stock) is blob)
        with cc.read_only_scans():
            check("read_only_scans: nesting keeps the outer answers",
                  cc.live_blob(stock) is blob)
        check("read_only_scans: an inner exit does not end the outer scope",
              cc.live_blob(stock) is blob)
    check("read_only_scans: nothing is kept once it ends", cc._SCAN_CACHE is None)

    # A write between two reads must be seen: this is why the cache is scoped.
    stock.write_bytes(b"STOCK " + MARKERS[0].encode())
    check("after the scope a rewritten file is read fresh",
          cc.is_stock(stock, MARKERS) is False and cc.live_blob(stock).startswith(b"STOCK "))
    cc.binary_contains = real_contains

    # State lives in the workspace. `flags` run from the repo checkout compared
    # against nothing and left a state file there, which was then committed
    # (2026-11-09).
    elsewhere = sandbox / "elsewhere"
    elsewhere.mkdir()
    for cmd in ("flags", "snapshot", "status"):
        r = subprocess.run([sys.executable, str(TOOL), cmd], cwd=elsewhere,
                           capture_output=True, text=True, timeout=60)
        check(f"{cmd}: refuses a directory that is not a workspace, and writes nothing",
              r.returncode != 0 and "ccctl workspace" in (r.stdout + r.stderr)
              and not any(elsewhere.iterdir()), (r.stdout + r.stderr)[-200:])
    ignored = (TOOL.parent.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    check("the repo ignores workspace state at its root", "/ccctl-state.json" in ignored)

    print()
    print("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}")
finally:
    shutil.rmtree(sandbox, ignore_errors=True)
sys.exit(1 if fails else 0)
