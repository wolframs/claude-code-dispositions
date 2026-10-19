"""An unactivated staged release must never become the live recovery path."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ccctl as cc


class RestoreBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.versions = self.root / 'versions'
        self.versions.mkdir()
        self.backup = self.root / 'native-binary.backup'
        self.source = self.versions / '2.1.294.stock'
        self.source.write_bytes(b'old release pristine')
        self.backup.write_bytes(self.source.read_bytes())
        for name, value in (('TWEAKCC_DIR', self.root), ('STATE', self.root / 'state.json'),
                            ('versions_dirs', lambda: [self.versions])):
            mock = patch.object(cc, name, value)
            mock.start()
            self.addCleanup(mock.stop)

    def verdict(self):
        return cc.validated_restore_backup('2.1.294', ['our marker'])

    def test_failed_staged_apply_invalidates_previously_valid_recovery(self):
        self.assertEqual(self.verdict(), self.backup)
        # A failed tweakcc stage has written its pristine backup before swap.
        self.backup.write_bytes(b'new release pristine')
        self.assertIsNone(self.verdict())
        self.assertEqual(self.source.read_bytes(), b'old release pristine')

    def test_equal_patched_bytes_are_not_stock_recovery(self):
        self.source.write_bytes(b'our marker')
        self.backup.write_bytes(b'our marker')
        self.assertIsNone(self.verdict())

    def test_unknown_source_or_marker_set_cannot_prove_recovery(self):
        self.source.unlink()
        self.assertIsNone(self.verdict())
        self.assertIsNone(cc.validated_restore_backup('2.1.294', []))

    def test_native_windows_pristine_and_changed_source(self):
        self.source.rename(self.versions / '2.1.294')
        self.assertEqual(self.verdict(), self.backup)
        (self.versions / '2.1.294').write_bytes(b'changed pristine')
        self.assertIsNone(self.verdict())

    def test_cached_equality_does_not_scan_unchanged_files(self):
        self.assertEqual(self.verdict(), self.backup)
        with patch.object(cc, 'is_stock', side_effect=AssertionError('rescanned')):
            self.assertEqual(self.verdict(), self.backup)

    def test_restore_refuses_the_failed_stage_before_invoking_tweakcc(self):
        self.backup.write_bytes(b'new release pristine')
        with patch.object(cc, 'require_cfg', return_value={}), \
                patch.object(cc, 'repo_markers', return_value=['our marker']), \
                patch.object(cc, 'cc_version', return_value='2.1.294'), \
                patch.object(cc, 'tweakcc_node', side_effect=AssertionError('restore invoked')):
            with self.assertRaises(SystemExit):
                cc.cmd_restore(None)

    def test_restore_targets_the_live_executable_and_restores_config(self):
        config = self.root / 'config.json'
        previous = '{"ccInstallationPath": "obsolete-version", "other": true}'
        config.write_text(previous)
        live = self.root / 'live'
        live.write_bytes(b'our marker')
        def restore(_argv, **_kw):
            self.assertEqual(json.loads(config.read_text())['ccInstallationPath'], str(live))
            live.write_bytes(self.backup.read_bytes())
            return SimpleNamespace(stdout='', stderr='', returncode=0)
        with patch.object(cc, 'require_cfg', return_value={}), \
                patch.object(cc, 'repo_markers', return_value=['our marker']), \
                patch.object(cc, 'cc_version', return_value='2.1.294'), \
                patch.object(cc, 'claude_binary', return_value=live), \
                patch.object(cc, 'tweakcc_node', return_value=['node', 'tweakcc']), \
                patch.object(cc, 'run', side_effect=restore), \
                patch.object(cc, 'log_entry'):
            cc.cmd_restore(None)
        self.assertEqual(config.read_text(), previous)
        self.assertEqual(live.read_bytes(), self.source.read_bytes())


if __name__ == '__main__':
    unittest.main()
