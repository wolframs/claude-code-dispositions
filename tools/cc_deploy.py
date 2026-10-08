#!/usr/bin/env python3
"""Reviewed-commit CC deployment on Linux/macOS, with retained binary rollback."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

import agent_run as ar


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    ar.atomic_write(path, (json.dumps(value, indent=2) + "\n").encode())


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@contextmanager
def deployment_lock(workspace):
    if os.name != "posix":
        raise ValueError("cc-deploy is for Linux/macOS")
    import fcntl
    with open(workspace / "agent-deploy.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another cc-deploy operation holds the deployment lock")
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def backup_binary(workspace, binary, launcher, head, state_path):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = workspace / "agent-rollbacks" / stamp
    directory.mkdir(parents=True, mode=0o700)
    directory.chmod(0o700)
    before = digest(binary)
    # Copy rather than link: even an accidental in-place write cannot damage
    # the saved executable. The live file itself is never opened for writing.
    shutil.copy2(binary, directory / "claude")
    if digest(directory / "claude") != before or digest(binary) != before:
        raise ValueError("live binary changed while taking the backup; nothing deployed")
    if state_path.exists():
        shutil.copy2(state_path, directory / "ccctl-state.json")
    manifest = {"id": stamp, "sha256": before, "binary": str(binary),
                "launcher": str(launcher), "repo_commit": head,
                "created": datetime.now(timezone.utc).isoformat()}
    save(directory / "manifest.json", manifest)
    return directory, manifest


def restore_binary(directory, expected_launcher):
    manifest = load(directory / "manifest.json")
    saved = directory / "claude"
    if digest(saved) != manifest["sha256"]:
        raise ValueError("rollback hash mismatch; refusing to restore")
    launcher = Path(manifest["launcher"])
    target = Path(manifest["binary"])
    if launcher != expected_launcher or launcher.resolve() != target:
        raise ValueError("launcher target changed since this backup; inspect before restoring")
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".restore-", dir=target.parent)
    os.close(fd)
    try:
        shutil.copy2(saved, tmp)
        if digest(tmp) != manifest["sha256"]:
            raise ValueError("rollback copy hash mismatch")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if digest(target) != manifest["sha256"]:
        raise ValueError("restored binary failed hash verification")
    return manifest


class Deployment:
    def __init__(self, cfg, timeout=1800):
        self.env = ar.environment(cfg)
        self.workspace = ar.expand(cfg["ccctl_workspace"])
        config = load(self.workspace / "ccctl.json")
        self.repo = Path(config["repoPath"]).expanduser()
        if not self.repo.is_absolute():
            self.repo = self.workspace / self.repo
        self.repo = self.repo.resolve()
        self.launcher = Path.home() / ".local/bin/claude"
        self.timeout = timeout
        self.state_path = self.workspace / "ccctl-state.json"
        self.log = None

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], env=self.env,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ValueError("git " + args[0] + ": " + result.stderr.strip())
        return result.stdout.strip()

    @contextmanager
    def logging(self, operation):
        directory = self.workspace / "agent-deploy-logs"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                            + "-" + uuid.uuid4().hex[:8] + "-" + operation + ".log")
        with open(path, "x", encoding="utf-8") as log:
            os.chmod(path, 0o600)
            self.log = log
            print("Log: " + str(path), flush=True)
            try:
                yield
            finally:
                self.log = None

    def execute(self, argv, cwd=None):
        print("Running: " + " ".join(map(str, argv)), flush=True)
        if self.log:
            self.log.write("\n$ " + " ".join(map(str, argv)) + "\n")
            self.log.flush()
        code = ar.run(list(map(str, argv)), cwd or self.workspace, self.env, self.timeout,
                      stdin=subprocess.DEVNULL, stdout=self.log,
                      stderr=subprocess.STDOUT if self.log else None)
        if code:
            raise ValueError("command failed (exit %s); see log: %s" % (code, argv[0]))

    def cc(self, *args):
        self.execute([sys.executable, self.repo / "tools/ccctl.py", *args])

    def status(self):
        self.cc("status")
        record = self.workspace / "agent-deployment.json"
        if record.exists():
            print(json.dumps(load(record), indent=2))

    def verify(self):
        self.cc("status", "--check")
        self.cc("status", "--delivered")
        self.execute([sys.executable, self.repo / "tools/probe_subagent_models.py", "--delegation-check"])
        self.cc("flags", "--no-record")

    def snapshot(self):
        if not self.launcher.is_symlink():
            raise ValueError("expected the POSIX Claude launcher symlink; inspect this installation")
        binary = self.launcher.resolve(strict=True)
        directory, manifest = backup_binary(self.workspace, binary, self.launcher,
                                             self.git("rev-parse", "HEAD"), self.state_path)
        print("Rollback: " + manifest["id"], flush=True)
        return directory, manifest

    def ensure_validation_inputs(self):
        release = load(self.repo / "release.json")
        required = [self.repo / release["promptData"]["path"], self.repo / "ab/data.json"]
        if all(path.is_file() for path in required):
            return
        # Older ccctl sparse deployments omit baseline/ and ab/. Include only
        # these validation inputs, not the transcript corpus or unrelated data.
        try:
            self.git("sparse-checkout", "list")
        except ValueError:
            raise ValueError("validation inputs missing from a non-sparse checkout")
        self.execute(["git", "-C", self.repo, "sparse-checkout", "add", "baseline", "ab"])
        if not all(path.is_file() for path in required):
            raise ValueError("validation inputs still missing after expanding sparse checkout")

    def recover(self, directory, reason):
        manifest = restore_binary(directory, self.launcher)
        previous_state = directory / "ccctl-state.json"
        if previous_state.exists():
            ar.atomic_write(self.state_path, previous_state.read_bytes())
        self.execute([self.launcher, "--version"])
        save(self.workspace / "agent-deployment.json", {
            "outcome": "rolled-back", "backup": manifest["id"], "reason": reason,
            "binary_sha256": manifest["sha256"], "binary_repo_commit": manifest["repo_commit"],
            "checkout_commit": self.git("rev-parse", "HEAD"),
            "note": "Binary and ccctl state restored. Checkout and settings were not rewound; "
                    "the tripwire may report differences until a compatible commit is applied."})
        print("Previous binary restored. Checkout and settings were not rewound.", flush=True)

    def apply(self, commit, fetch=True):
        if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
            raise ValueError("supply the full reviewed commit SHA, not a moving branch name")
        if self.git("status", "--porcelain", "--untracked-files=normal"):
            raise ValueError("deployment checkout is dirty; preserving it unchanged")
        self.git("symbolic-ref", "--short", "HEAD")  # detached checkout needs an explicit decision
        old_head = self.git("rev-parse", "HEAD")
        # A checkout can legitimately be ahead of its binary (including after
        # rollback). Report that state; apply is precisely how it is repaired.
        self.cc("status")
        if fetch:
            self.execute(["git", "-C", self.repo, "fetch", "origin"])
        target = self.git("rev-parse", "--verify", commit + "^{commit}")
        self.git("merge-base", "--is-ancestor", old_head, target)
        upstream = self.git("rev-parse", "--symbolic-full-name", "@{upstream}")
        self.git("merge-base", "--is-ancestor", target, upstream)
        directory, manifest = self.snapshot()
        save(self.workspace / "agent-deployment.json", {
            "outcome": "applying", "commit": target, "backup": manifest["id"],
            "previous_commit": old_head,
            "note": "If interrupted, inspect the log and use the retained backup if necessary."})
        try:
            self.execute(["git", "-C", self.repo, "merge", "--ff-only", target])
            self.ensure_validation_inputs()
            self.execute([sys.executable, self.repo / "tools/check_repo.py"], self.repo)
            self.cc("apply", "--no-pull")
            self.verify()
            save(self.workspace / "agent-deployment.json", {
                "outcome": "verified", "commit": target, "backup": manifest["id"],
                "binary_sha256": digest(self.launcher.resolve()),
                "when": datetime.now(timezone.utc).isoformat()})
            print("Applied and verified " + target, flush=True)
        except BaseException:
            # Same-version apply leaves the launcher target fixed. If another
            # tool repointed it, restore_binary refuses instead of guessing.
            try:
                unchanged = (self.launcher.resolve() == Path(manifest["binary"])
                             and digest(self.launcher) == manifest["sha256"])
            except OSError:
                unchanged = False
            if not unchanged:
                self.recover(directory, "apply or verification failed")
            else:
                save(self.workspace / "agent-deployment.json", {
                    "outcome": "failed-before-binary-change", "commit": target,
                    "backup": manifest["id"], "checkout_commit": self.git("rev-parse", "HEAD")})
                print("Deployment failed before the live binary changed. Backup: " + manifest["id"],
                      file=sys.stderr, flush=True)
            raise

    def rollback(self, ident):
        if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", ident):
            raise ValueError("invalid backup ID; use cc-deploy backups")
        directory = self.workspace / "agent-rollbacks" / ident
        manifest = load(directory / "manifest.json")
        if digest(directory / "claude") != manifest["sha256"]:
            raise ValueError("rollback hash mismatch")
        if self.launcher.resolve() != Path(manifest["binary"]):
            raise ValueError("this backup belongs to another launcher target/version")
        # Rollback itself has an undo point, retained even when its hash matches.
        self.snapshot()
        self.recover(directory, "explicit rollback")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--timeout", type=float, default=1800, help="seconds per step; 0 disables timeout")
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("status", "verify", "backups"):
        commands.add_parser(name)
    apply = commands.add_parser("apply-reviewed-commit")
    apply.add_argument("commit")
    apply.add_argument("--no-fetch", action="store_true")
    rollback = commands.add_parser("rollback")
    rollback.add_argument("backup")
    args = parser.parse_args(argv)
    deployment = Deployment(ar.load_config(args.config), args.timeout)
    if args.action == "backups":
        for path in sorted((deployment.workspace / "agent-rollbacks").glob("*/manifest.json")):
            manifest = load(path)
            print(manifest["id"], manifest["repo_commit"], manifest["sha256"])
        return 0
    with deployment_lock(deployment.workspace):
        if args.action == "status":
            deployment.status()
        else:
            with deployment.logging(args.action):
                if args.action == "verify":
                    deployment.verify()
                elif args.action == "apply-reviewed-commit":
                    deployment.apply(args.commit, not args.no_fetch)
                else:
                    deployment.rollback(args.backup)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("cc-deploy: interrupted", file=sys.stderr)
        sys.exit(130)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print("cc-deploy: " + str(exc), file=sys.stderr)
        sys.exit(2)
