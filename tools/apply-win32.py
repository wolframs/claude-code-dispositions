#!/usr/bin/env python3
"""Compatibility entrypoint for ccctl's staged apply (including --no-swap)."""
import sys
from ccctl_compat import main

if __name__ == "__main__":
    raise SystemExit(main(["apply", "--no-pull", *sys.argv[1:]]))
