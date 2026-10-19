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
