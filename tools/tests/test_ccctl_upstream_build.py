"""An upstream pin with native graph support must bypass legacy adaptation."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ccctl as cc

spec = importlib.util.spec_from_file_location('builder', Path(__file__).resolve().parents[1] / 'build_local_tweakcc.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class UpstreamBuildTests(unittest.TestCase):
    def test_restore_backup_is_promoted_only_after_target_activation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backup = root / 'native-binary.backup'
            backup.write_bytes(b'old recovery')
            pristine = root / 'incoming'
            pristine.write_bytes(b'new recovery')
            with patch.object(cc, 'TWEAKCC_DIR', root), \
                    patch.object(cc, 'pristine_source', return_value=pristine) as source:
                with patch.object(cc, 'cc_version', return_value='old'):
                    with self.assertRaises(SystemExit):
                        cc.promote_restore_backup('new', ['marker'])
                self.assertEqual(backup.read_bytes(), b'old recovery')
                source.assert_not_called()
                with patch.object(cc, 'cc_version', return_value='new'):
                    cc.promote_restore_backup('new', ['marker'])
                source.assert_called_once_with('new', ['marker'])
                self.assertEqual(backup.read_bytes(), b'new recovery')
                self.assertFalse(list(root.glob('ccctl-restore-*')))

    def test_update_plan_total_excludes_explicit_noops(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.multiple(cc, cc_version=lambda: '2.1.301',
                                repo_markers=lambda cfg: ['marker'], repo_edits=lambda cfg: [],
                                prompt_data_status=lambda ver: 'cached',
                                run=lambda *args, **kwargs: SimpleNamespace(stdout='4.4.0'),
                                tweakcc_node=lambda: ['node', 'tweakcc'], tweakcc_latest=lambda: '4.4.0',
                                tweakcc_build_state=lambda *args: {'verdict': 'ok', 'line': 'reviewed'},
                                pristine_source=lambda *args: root / 'pristine', live_blob=lambda p: b'',
                                load_snapshot=lambda ver: {'present': {}},
                                plan_fragments=lambda *args: [{'name': 'edit', 'status': 'AUTO'}],
                                plan_adhocs=lambda blob: [{'name': 'absent gate', 'status': 'NOOP'}],
                                repo_commit=lambda cfg: 'reviewed', write_report=lambda *args: None):
                report, _, plan = cc.analyze_core({'repoPath': str(root)}, '2.1.301')
            self.assertEqual(report['overall'], 'CLEAN')
            self.assertEqual(report['planTotal'], len(plan['AUTO']))
            self.assertEqual(report['planTotal'], 1)

    def test_stage_gets_matching_backup_and_live_recovery_survives_success_or_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                backup = root / 'native-binary.backup'
                backup.write_bytes(b'previous release recovery')
                staged = root / 'incoming'
                staged.write_bytes(b'incoming pristine release')
                with patch.object(cc, 'TWEAKCC_DIR', root):
                    try:
                        with cc.staged_tweakcc_backup(staged):
                            self.assertEqual(backup.read_bytes(), staged.read_bytes())
                            backup.write_bytes(b'tweakcc changed its backup')
                            if fail:
                                raise RuntimeError('patch failed')
                    except RuntimeError:
                        self.assertTrue(fail)
                self.assertEqual(backup.read_bytes(), b'previous release recovery')
                self.assertFalse(list(root.glob('ccctl-backup-*')))

    def test_stage_does_not_leave_a_backup_when_none_existed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staged = root / 'incoming'
            staged.write_bytes(b'incoming pristine')
            with patch.object(cc, 'TWEAKCC_DIR', root):
                with cc.staged_tweakcc_backup(staged):
                    self.assertEqual((root / 'native-binary.backup').read_bytes(), staged.read_bytes())
            self.assertFalse((root / 'native-binary.backup').exists())

    def test_no_overlay_build_never_calls_the_legacy_dist_adapter(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(builder, 'OVERLAY', None), \
                patch.object(builder, 'verify_checkout'), \
                patch.object(builder, 'git', return_value='') as git, \
                patch.object(builder, 'run', return_value='') as run:
            builder.build(Path(temp), install=False)
            self.assertEqual(len(run.call_args_list), 3)
            self.assertEqual([c.args[0][-1] for c in run.call_args_list], ['lint', 'test', 'build'])
            self.assertTrue(all('apply' not in c.args for c in git.call_args_list))

    def test_reviewed_upstream_pin_and_unverified_release_are_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'release.json').write_text(json.dumps({'tweakcc': {
                'version': '4.4.0', 'commit': 'a' * 40, 'overlay': None,
                'verifiedCcVersion': '2.1.296'}}))
            cfg = {'repoPath': str(root)}
            with patch.object(cc, 'run', return_value=SimpleNamespace(stdout='4.4.0')), \
                    patch.object(cc, 'tweakcc_node', return_value=['node', 'tweakcc']), \
                    patch.object(cc, 'tweakcc_clone', return_value=root), \
                    patch.object(cc, 'clone_head', return_value=('a' * 40, False)):
                self.assertEqual(cc.tweakcc_build_state(cfg, '2.1.296')['verdict'], 'ok')
                self.assertEqual(cc.tweakcc_build_state(cfg, '2.1.297')['verdict'], 'uncovered')
                with patch.object(cc, 'clone_head', return_value=('b' * 40, False)):
                    self.assertEqual(cc.tweakcc_build_state(cfg, '2.1.296')['verdict'], 'stale-clone')
                with patch.object(cc, 'clone_head', return_value=('a' * 40, True)):
                    self.assertEqual(cc.tweakcc_build_state(cfg, '2.1.296')['verdict'], 'dirty-clone')


if __name__ == '__main__':
    unittest.main()
