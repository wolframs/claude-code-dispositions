#!/usr/bin/env python3
"""Deprecated live-install entrypoint; ccctl now applies all targets together.

Historical version-bound JSON files document intent, not executable locators.
Use ccctl's staged engine so partial prompt applications cannot be called done.
"""
import os
from pathlib import Path
import sys
from ccctl_compat import state_dir, main as compat_main


def main():
    if len(sys.argv) != 2:
        print("usage: apply_adhoc.py <installed-claude-binary-path>")
        return 2
    requested = Path(sys.argv[1]).resolve()
    os.chdir(state_dir())
    import ccctl
    if requested != Path(ccctl.claude_binary()).resolve():
        print("FAIL: target is not the installed binary; use ccctl's staged apply.")
        return 2
    print("Delegating to ccctl: all fragments and adhocs are applied and verified together.")
    return compat_main(["apply", "--no-pull"])


if __name__ == "__main__":
    raise SystemExit(main())
