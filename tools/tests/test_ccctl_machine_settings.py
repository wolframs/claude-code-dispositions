"""Tests for the machine settings ccctl keeps in the user's settings.json
(`MACHINE_SETTINGS`, `machine_settings_drift`, `ensure_machine_settings`).

Run from anywhere:  python tools/tests/test_ccctl_machine_settings.py
Every file is a temp file; the real settings.json is never read or written.

What this guards:

  * `idleCompaction` UNSET is drift. CC defaults it to on, and only an
    explicit false turns idle compaction off, so a check that accepted a
    missing key would pass on exactly the machines that need the write.
  * The write changes that key and nothing else: every other key keeps its
    value and its order, and the previous bytes stay beside the file.
  * `promptCacheTtl` must be the string "1h": a value is compared by type and
    equality, not identity, because json.loads hands back a fresh str.
  * A settings file that does not parse is never rewritten.
"""
import importlib.util
import json
import sys
import tempfile
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


tmp = Path(tempfile.mkdtemp())


def write(name, text):
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------- drift
TTL = '"promptCacheTtl": "1h"'


def drifted(text):
    return {k: h for k, _w, h in cc.machine_settings_drift(write("case.json", text))}


unset = write("unset.json", json.dumps({"model": "opus"}, indent=2) + "\n")
d = cc.machine_settings_drift(unset)
check("drift: unset keys are drift, and read as unset",
      [k for k, _w, _h in d] == ["idleCompaction", "promptCacheTtl"]
      and all(h is cc._UNSET for _k, _w, h in d), d)
check("drift: true is drift", drifted('{"idleCompaction": true, %s}' % TTL) == {"idleCompaction": True})
check("drift: null is drift, not a falsy pass",
      list(drifted('{"idleCompaction": null, %s}' % TTL)) == ["idleCompaction"])
check("drift: 0 is drift, not false",
      list(drifted('{"idleCompaction": 0, %s}' % TTL)) == ["idleCompaction"])
check("drift: false and \"1h\" are no drift",
      cc.machine_settings_drift(write("ok.json", '{"idleCompaction": false, %s}' % TTL)) == [])
check("drift: \"5m\" is drift", drifted('{"idleCompaction": false, "promptCacheTtl": "5m"}')
      == {"promptCacheTtl": "5m"})
check("drift: a missing file is all keys unset",
      [k for k, _w, _h in cc.machine_settings_drift(tmp / "absent.json")] == list(cc.MACHINE_SETTINGS))
for name, text in (("broken.json", '{"model": "opus",'), ("list.json", "[]")):
    try:
        cc.machine_settings_drift(write(name, text))
        check(f"drift: {name} raises instead of reading as drift", False)
    except ValueError:
        check(f"drift: {name} raises instead of reading as drift", True)

# ---------------------------------------------------------------- ensure
before = {"cleanupPeriodDays": 3650, "env": {"DISABLE_UPDATES": "1"}, "model": "opus",
          "statusLine": {"type": "command", "command": "echo ä"}, "autoCompactEnabled": True}
f = write("settings.json", json.dumps(before, indent=2, ensure_ascii=False) + "\n")
old_bytes = f.read_bytes()
changed = cc.ensure_machine_settings(f)
after = json.loads(f.read_text(encoding="utf-8"))
check("ensure: reports the keys it changed",
      [c[0] for c in changed] == ["idleCompaction", "promptCacheTtl"], changed)
check("ensure: the keys hold their values afterwards",
      after.get("idleCompaction") is False and after.get("promptCacheTtl") == "1h")
check("ensure: every other key keeps its value and order",
      list(after)[:-2] == list(before) and {k: after[k] for k in before} == before, list(after))
check("ensure: non-ASCII survives unescaped", "echo ä" in f.read_text(encoding="utf-8"))
check("ensure: the previous bytes are kept beside the file",
      (tmp / "settings.json.ccctl-prev").read_bytes() == old_bytes)
check("ensure: no temp file is left behind", not (tmp / "settings.json.ccctl-tmp").exists())
check("ensure: nothing drifts afterwards", cc.machine_settings_drift(f) == [])

stamp = f.read_bytes()
(tmp / "settings.json.ccctl-prev").unlink()
check("ensure: a second run changes nothing", cc.ensure_machine_settings(f) == [] and f.read_bytes() == stamp)
check("ensure: a second run writes no backup", not (tmp / "settings.json.ccctl-prev").exists())

flipped = write("flipped.json", json.dumps({"idleCompaction": True, "promptCacheTtl": "5m",
                                             "model": "opus"}, indent=2) + "\n")
cc.ensure_machine_settings(flipped)
check("ensure: drifted keys are rewritten in place, not appended",
      json.loads(flipped.read_text()) == {"idleCompaction": False, "promptCacheTtl": "1h", "model": "opus"}
      and list(json.loads(flipped.read_text())) == ["idleCompaction", "promptCacheTtl", "model"])

fresh = tmp / "sub" / "settings.json"
cc.ensure_machine_settings(fresh)
check("ensure: a missing file is created with the keys",
      json.loads(fresh.read_text()) == cc.MACHINE_SETTINGS)

broken = write("broken2.json", '{"model": "opus",')
try:
    cc.ensure_machine_settings(broken)
    check("ensure: an unparseable file is refused", False)
except ValueError:
    check("ensure: an unparseable file is refused", True)
check("ensure: and left byte-for-byte alone", broken.read_text() == '{"model": "opus",')

# ---------------------------------------------------------------- status line
cc.user_settings_path = lambda: unset
ok, text = cc.machine_settings_line()
check("status: drift is not ok and names the key and the fix",
      not ok and "idleCompaction is unset" in text and "ccctl.py apply" in text, text)
cc.user_settings_path = lambda: f
ok, text = cc.machine_settings_line()
check("status: set keys read as their values",
      ok and text == 'idleCompaction=false, promptCacheTtl="1h"', text)
cc.user_settings_path = lambda: broken
ok, text = cc.machine_settings_line()
check("status: an unparseable file is said, not raised", not ok and text.startswith("UNREADABLE"), text)

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
