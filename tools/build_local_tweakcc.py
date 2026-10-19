#!/usr/bin/env python3
"""Build the pinned tweakcc checkout, with an overlay only when selected.

The upstream checkout is restored before this script exits. Only ignored build
output in ``dist/`` remains. Upstream 4.4 supports the native module graph;
the legacy overlay path alone runs ``patch_tweakcc.py`` afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
# release.json owns the exact source pin and optional overlay. Since upstream
# 4.4.0 the reviewed build uses its native module graph with no source or dist
# adaptation. Older overlays and patch_tweakcc.py remain historical build
# support; their release.json selects them explicitly. See spec/45 and
# notes/2026-10-12-win32-fleet-catchup.md for the transition.
RELEASE = json.loads((ROOT / "release.json").read_text(encoding="utf-8"))
EXPECTED_SHA = RELEASE["tweakcc"]["commit"]
EXPECTED_VERSION = RELEASE["tweakcc"]["version"]
OVERLAY = (ROOT / RELEASE["tweakcc"]["overlay"]
           if RELEASE["tweakcc"].get("overlay") else None)


def corepack_cmd() -> list[str]:
    """argv prefix that runs corepack.

    Some node distributions include corepack, but a machine that links only
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
    if OVERLAY:
        git(clone, "apply", "--check", "--ignore-space-change", str(OVERLAY))


def build(clone: Path, install: bool = True) -> None:
    verify_checkout(clone)
    if OVERLAY:
        print(f"Applying temporary source overlay to {clone}")
        git(clone, "apply", "--ignore-space-change", str(OVERLAY))
    else:
        print(f"Building reviewed upstream tweakcc {EXPECTED_VERSION} at {EXPECTED_SHA}")
    try:
        if install:
            run([*COREPACK, "pnpm", "install", "--frozen-lockfile"], clone)
        run([*COREPACK, "pnpm", "lint"], clone)
        run([*COREPACK, "pnpm", "test"], clone)
        run([*COREPACK, "pnpm", "build"], clone)
    finally:
        if OVERLAY:
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

    if OVERLAY:
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
    parser.add_argument("--test-bun", type=Path,
                        help="Bun executable for the optional native round-trip tests")
    args = parser.parse_args()
    if args.test_bun:
        compiler = args.test_bun.expanduser().resolve()
        if not compiler.is_file():
            raise RuntimeError(f"test Bun executable is missing: {compiler}")
        os.environ["PATH"] = str(compiler.parent) + os.pathsep + os.environ.get("PATH", "")
    clone = args.clone.expanduser().resolve()
    if args.check:
        verify_checkout(clone)
        print(f"OK: reviewed checkout at {EXPECTED_SHA}" +
              (f", overlay applies cleanly to {clone}" if OVERLAY else ", no overlay selected"))
    else:
        build(clone, install=not args.skip_install)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
