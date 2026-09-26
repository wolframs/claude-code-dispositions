#!/usr/bin/env python3
"""Build the pinned tweakcc checkout with this repo's temporary source overlay.

The upstream checkout is restored before this script exits. Only ignored build
output in ``dist/`` remains, then ``patch_tweakcc.py`` adapts that output for
Claude Code's code-split Bun module graph.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
# PR 1005's head (prompt data through CC 2.1.278), reviewed under spec/45's
# trust gate on 2026-09-20: official-repo branch, MEMBER author, one added
# generated file, parent is main's merged PR 1003. `git diff 2a4ac735 f5aaf1e7
# -- . ':(exclude)data/prompts'` is EMPTY, as it was for every previous pin
# move — the commits between pins add nothing but generated prompt maps, and
# TWEAKCC ITSELF IS UNCHANGED at 4.3.3.
#
# PR 1005 has since MERGED upstream as `54048e9`, and the pin still does not
# move: the merge was a squash, so `f5aaf1e` is not an ancestor of `main` and
# this builder compares SHAs exactly. What was checked instead — independently
# on all three machines' own clones — is that the pin's tree and `main`'s are
# the same object (`6a2fac35`) and that `main` publishes the reviewed blob
# (`68eab8bd`). Reach the pin with `git fetch origin pull/1005/head`; do NOT
# `git switch --detach origin/main`. See spec/45, "After upstream merges".
#
# The OVERLAY is the thing that adapts to the CC bundle, not to upstream
# tweakcc. `diff 4.3.3-cc-2.1.261.patch 4.3.3-cc-2.1.268.patch` is not empty
# because 2.1.268 changed the third declarator of the model-list function the
# 2.1.261 anchor matched and took the custom-model menu down with it. The
# 2.1.270 overlay also repairs session-memory gate locators and uses
# replacer callbacks to preserve dollar signs in captured minified names; the
# .273 and .278 overlays are identical in content to it, because the patched
# code structures have not moved across those releases — only minified names.
# The filename is the coverage record `ccctl.py analyze` reads; the build
# below tests those repairs before adapting the generated bundle.
#
# The values live in release.json (`tweakcc.commit`, `tweakcc.version`,
# `tweakcc.overlay`) so that the build pin, the reviewed prompt map and the A/B
# generator cannot drift apart; move the pin there, not here.
RELEASE = json.loads((ROOT / "release.json").read_text(encoding="utf-8"))
EXPECTED_SHA = RELEASE["tweakcc"]["commit"]
EXPECTED_VERSION = RELEASE["tweakcc"]["version"]
OVERLAY = ROOT / RELEASE["tweakcc"]["overlay"]


def corepack_cmd() -> list[str]:
    """argv prefix that runs corepack.

    corepack ships inside every node distribution, but a machine that links only
    node/npm/npx into its bin directory (macOS here: ~/.local/bin -> ~/.hermes/node)
    has no ``corepack`` on PATH. Resolve it beside the *real* node before falling
    back to the bare name, so the failure — if any — is corepack's own.
    """
    found = shutil.which("corepack")
    if found:
        return [found]
    node = shutil.which("node")
    if node:
        real = Path(node).resolve()
        # a launcher beside the real node; win32 ships .cmd (nodejs.org, scoop,
        # nvm-windows), never an extensionless one subprocess could run
        names = ("corepack.cmd", "corepack.exe", "corepack.bat") if sys.platform == "win32" else ("corepack",)
        for name in names:
            sibling = real.parent / name
            if sibling.exists():
                return [str(sibling)]
        # node's own copy of corepack, runnable without a launcher. Two layouts:
        # POSIX/nvm keeps node in <prefix>/bin with the modules in <prefix>/lib,
        # while a nodejs.org win32 install puts node.exe at the prefix root and
        # has no lib/ at all (measured on win32, 2026-09-11).
        for script in (
            real.parent.parent / "lib" / "node_modules" / "corepack" / "dist" / "corepack.js",
            real.parent / "node_modules" / "corepack" / "dist" / "corepack.js",
        ):
            if script.exists():
                return [str(real), str(script)]
    return ["corepack"]


COREPACK = corepack_cmd()


def run(command: list[str], cwd: Path, capture: bool = False) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=capture,
        check=False,
    )
    if result.returncode:
        detail = ""
        if capture:
            detail = f"\n{result.stdout}{result.stderr}".rstrip()
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}{detail}")
    return result.stdout.strip() if capture else ""


def git(clone: Path, *args: str, capture: bool = False) -> str:
    return run(
        ["git", "-c", f"safe.directory={clone.as_posix()}", "-C", str(clone), *args],
        ROOT,
        capture=capture,
    )


def verify_checkout(clone: Path) -> None:
    if not (clone / ".git").exists() or not (clone / "package.json").exists():
        raise RuntimeError(f"not a tweakcc checkout: {clone}")
    head = git(clone, "rev-parse", "HEAD", capture=True)
    if head != EXPECTED_SHA:
        raise RuntimeError(
            f"overlay is pinned to {EXPECTED_SHA}, but checkout is {head}; "
            "fetch and detach at the reviewed PR commit first"
        )
    version = json.loads((clone / "package.json").read_text(encoding="utf-8"))["version"]
    if version != EXPECTED_VERSION:
        raise RuntimeError(
            f"overlay expects tweakcc {EXPECTED_VERSION}, checkout reports {version}"
        )
    dirty = git(
        clone,
        "status",
        "--porcelain",
        "--untracked-files=no",
        capture=True,
    )
    if dirty:
        raise RuntimeError(
            "tweakcc has tracked source changes; preserve or revert them before "
            f"building:\n{dirty}"
        )
    git(clone, "apply", "--check", "--ignore-space-change", str(OVERLAY))


def build(clone: Path, install: bool = True) -> None:
    verify_checkout(clone)
    print(f"Applying temporary source overlay to {clone}")
    git(clone, "apply", "--ignore-space-change", str(OVERLAY))
    try:
        if install:
            run([*COREPACK, "pnpm", "install", "--frozen-lockfile"], clone)
        run([*COREPACK, "pnpm", "lint"], clone)
        run([*COREPACK, "pnpm", "test"], clone)
        run([*COREPACK, "pnpm", "build"], clone)
    finally:
        print("Restoring the upstream tweakcc source checkout")
        git(clone, "apply", "-R", "--ignore-space-change", str(OVERLAY))

    dirty = git(
        clone,
        "status",
        "--porcelain",
        "--untracked-files=no",
        capture=True,
    )
    if dirty:
        raise RuntimeError(f"source checkout was not restored cleanly:\n{dirty}")

    run([sys.executable, str(ROOT / "tools" / "patch_tweakcc.py"), str(clone / "dist")], ROOT)
    print("Local tweakcc build is ready; tracked upstream source is clean.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("clone", type=Path, help="path to the ordinary tweakcc clone")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify commit, cleanliness, and overlay applicability without writing",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="reuse the clone's existing pinned node_modules",
    )
    args = parser.parse_args()
    clone = args.clone.expanduser().resolve()
    if args.check:
        verify_checkout(clone)
        print(f"OK: overlay applies cleanly to {clone} at {EXPECTED_SHA}")
    else:
        build(clone, install=not args.skip_install)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
