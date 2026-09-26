#!/usr/bin/env python3
"""Route historical entrypoints through ccctl using this checkout's state dir."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def state_dir(root=ROOT):
    explicit = os.environ.get("CCCTL_HOME")
    candidates = [Path(explicit).expanduser()] if explicit else [Path.cwd(), Path.home() / "ccctl"]
    for directory in candidates:
        config = directory / "ccctl.json"
        if config.is_file():
            data = json.loads(config.read_text(encoding="utf-8"))
            if (directory / data.get("repoPath", "repo")).resolve() == root.resolve():
                return directory.resolve()
    raise RuntimeError("No ccctl state directory for this checkout. Set CCCTL_HOME to "
                       "the folder containing its ccctl.json, or run ccctl.py init there.")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    os.chdir(state_dir())
    # ccctl derives its state paths at import time, after we select the folder.
    import ccctl
    if argv and argv[0] == "apply" and "--no-swap" in argv:
        if any(arg not in ("apply", "--no-pull", "--no-swap") for arg in argv):
            raise RuntimeError("Unsupported argument for apply --no-swap")
        cfg = ccctl.require_cfg()
        version = ccctl.cc_version()
        report, pristine, plan = ccctl.analyze_core(cfg, version)
        if report["overall"] != "CLEAN":
            raise RuntimeError("Staged apply requires a CLEAN analysis")
        staged = ccctl.assemble(cfg, version, pristine, plan["AUTO"])
        print(f"Verified staged binary (not activated): {staged}")
        return 0
    sys.argv = [str(ROOT / "tools" / "ccctl.py"), *argv]
    ccctl.main()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
