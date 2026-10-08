"""Recovery tests use tiny executables and temporary launchers, never live CC."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cc_deploy as dep


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cc-deploy-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.live = self.root / "version"
        self.live.write_bytes(b"old binary")
        self.live.chmod(0o755)
        self.launcher = self.root / "claude"
        try:
            self.launcher.symlink_to(self.live)
        except OSError:
            self.skipTest("symlinks unavailable")
        self.state = self.root / "ccctl-state.json"
        self.state.write_text('{"applied":"old"}')

    def backup(self):
        return dep.backup_binary(self.root, self.live, self.launcher, "a" * 40, self.state)

    def replace_live(self, data):
        staged = self.root / "new"
        staged.write_bytes(data)
        os.replace(staged, self.live)

    @unittest.skipUnless(os.name == "posix", "POSIX replacement of an open inode")
    def test_restore_preserves_backup_and_open_inode(self):
        directory, manifest = self.backup()
        with open(self.live, "rb") as running:
            self.replace_live(b"new binary")
            dep.restore_binary(directory, self.launcher)
            self.assertEqual(running.read(), b"old binary")
        self.assertEqual(self.live.read_bytes(), b"old binary")
        self.assertEqual(dep.digest(directory / "claude"), manifest["sha256"])

    def test_corrupt_backup_is_refused(self):
        directory, _ = self.backup()
        (directory / "claude").write_bytes(b"tampered")
        self.replace_live(b"new binary")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            dep.restore_binary(directory, self.launcher)
        self.assertEqual(self.live.read_bytes(), b"new binary")

    def test_changed_launcher_is_not_overwritten(self):
        directory, _ = self.backup()
        other = self.root / "other-version"
        other.write_bytes(b"other")
        self.launcher.unlink()
        self.launcher.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "launcher target changed"):
            dep.restore_binary(directory, self.launcher)
        self.assertEqual(other.read_bytes(), b"other")

    def fixture_deployment(self, fail):
        parent = self
        class FakeDeployment(dep.Deployment):
            def git(self, *args):
                if args[0] == "status":
                    return ""
                if args[0] == "merge-base":
                    return ""
                return "a" * 40

            def execute(self, argv, cwd=None):
                pass

            def ensure_validation_inputs(self):
                pass

            def cc(self, *args):
                if args[0] == "apply":
                    parent.replace_live(b"new binary")
                    parent.state.write_text('{"applied":"new"}')

            def verify(self):
                if fail:
                    raise ValueError("injected verification failure")

        obj = FakeDeployment.__new__(FakeDeployment)
        obj.workspace = self.root
        obj.repo = self.root
        obj.launcher = self.launcher
        obj.state_path = self.state
        return obj

    def test_failed_delivery_restores_binary_and_state(self):
        obj = self.fixture_deployment(True)
        with self.assertRaisesRegex(ValueError, "injected"):
            obj.apply("a" * 40, fetch=False)
        self.assertEqual(self.live.read_bytes(), b"old binary")
        self.assertEqual(json.loads(self.state.read_text()), {"applied": "old"})
        self.assertEqual(dep.load(self.root / "agent-deployment.json")["outcome"], "rolled-back")

    def test_success_retains_named_rollback(self):
        self.fixture_deployment(False).apply("a" * 40, fetch=False)
        record = dep.load(self.root / "agent-deployment.json")
        self.assertEqual(record["outcome"], "verified")
        self.assertEqual(self.live.read_bytes(), b"new binary")
        self.assertEqual((self.root / "agent-rollbacks" / record["backup"] / "claude").read_bytes(), b"old binary")

    def test_moving_ref_and_dirty_checkout_are_refused(self):
        obj = self.fixture_deployment(False)
        with self.assertRaisesRegex(ValueError, "full reviewed"):
            obj.apply("master")
        obj.git = lambda *args: " M important-work"
        with self.assertRaisesRegex(ValueError, "dirty"):
            obj.apply("a" * 40)
        self.assertEqual(self.live.read_bytes(), b"old binary")

    def test_explicit_rollback_creates_its_own_undo_point(self):
        obj = self.fixture_deployment(False)
        obj.apply("a" * 40, fetch=False)
        original = dep.load(self.root / "agent-deployment.json")["backup"]
        obj.rollback(original)
        self.assertEqual(self.live.read_bytes(), b"old binary")
        backups = list((self.root / "agent-rollbacks").glob("*/claude"))
        self.assertEqual(len(backups), 2)
        self.assertEqual({p.read_bytes() for p in backups}, {b"old binary", b"new binary"})

    def test_sparse_validation_adds_only_required_trees(self):
        obj = self.fixture_deployment(False)
        dep.save(self.root / "release.json", {"promptData": {"path": "baseline/v/prompts.json"}})
        calls = []
        def expand(argv, cwd=None):
            calls.append(argv)
            for name in ("baseline/v/prompts.json", "ab/data.json"):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}")
        obj.execute = expand
        dep.Deployment.ensure_validation_inputs(obj)
        self.assertEqual(calls[0][-3:], ["add", "baseline", "ab"])
        calls.clear()
        dep.Deployment.ensure_validation_inputs(obj)
        self.assertEqual(calls, [])

    @unittest.skipUnless(os.name == "posix", "POSIX advisory locks")
    def test_second_deployment_cannot_overlap(self):
        with dep.deployment_lock(self.root):
            with self.assertRaisesRegex(ValueError, "holds the deployment lock"):
                with dep.deployment_lock(self.root):
                    self.fail("second lock unexpectedly acquired")


if __name__ == "__main__":
    unittest.main()
