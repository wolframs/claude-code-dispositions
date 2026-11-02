"""The 2.1.301+ rewrite must not bypass reviewed text or hide moved anchors."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ccctl as cc


def planned(blob, name):
    return next(i for i in cc.plan_adhocs(blob) if i['name'] == name)


class LeanRewrite(unittest.TestCase):
    # The shared predicate behind both leanRewrite caches (2.1.301 through
    # 2.1.303 on darwin-arm64 and win32-x64).
    PREDICATE = (b'function $gate(e){if(e===void 0)return!1;let n=ctx(e),'
                 b'r=served(n,"temporal_hare",e);if(r===!1)return!1;return r===!0}')
    CACHES = b'class W{leanRewrite=eo($gate);leanRewriteCompiledOnly=eo($gate)}'

    def test_rewrite_off_for_both_execution_paths(self):
        item = planned(self.PREDICATE + self.CACHES, 'lean-rewrite-off')
        self.assertEqual(item['status'], 'AUTO')
        node = shutil.which('node')
        if not node:
            self.skipTest('node unavailable')
        # Neither live server assignment nor compiled capability can reach
        # the alternate fragments, and disabling it causes no lookup effects.
        code = (b'let calls=0;function ctx(){calls++;throw Error("lookup ran")}'
                b'function served(){calls++;return!0}' + item['new']
                + self.PREDICATE[len(item['old']):] +
                b'console.log(JSON.stringify([$gate("opus"),$gate(void 0),calls]))')
        result = subprocess.run([node, '-e', code.decode()], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [False, False, 0])

    def test_rewrite_absent_ambiguous_and_unknown(self):
        self.assertEqual(planned(b'older release', 'lean-rewrite-off')['status'], 'NOOP')
        both = self.PREDICATE + self.CACHES
        self.assertEqual(planned(both * 2, 'lean-rewrite-off')['status'], 'MANUAL')
        self.assertEqual(planned(both.replace(b'let n=', b'const n='),
                                 'lean-rewrite-off')['status'], 'MANUAL')
        self.assertEqual(planned(self.PREDICATE, 'lean-rewrite-off')['status'], 'MANUAL')

    def test_server_assignment_is_visible_and_conflicting(self):
        rows = cc.client_data_rows({'temporal_hare': True})
        self.assertEqual(rows[0][4], 'lean-rewrite-off')
        self.assertTrue(cc.arm_conflicts('temporal_hare'))
        self.assertTrue(cc.catalogue_drift({'claude-new': frozenset({'temporal_hare'})}))

    def test_turn_rule_joins_the_style_reminder(self):
        # A made-up fallback and its own digest stand in for the stock one, as
        # with the other digest locators.
        syn = b'"Synthetic fallback words stand in here."'
        saved = cc.TURN_REMINDER_FALLBACK
        cc.TURN_REMINDER_FALLBACK = cc.StockSentence(
            b'"Synthetic', len(syn), cc.hashlib.sha256(syn).hexdigest())
        self.addCleanup(setattr, cc, 'TURN_REMINDER_FALLBACK', saved)
        src = (b'return Zl([Ie({content:`${v0(e.style)} output style is active. '
               b'${e.turnReminder??' + syn + b'}`,isMeta:!0})])')
        item = planned(src, 'turn-rule-file')
        self.assertEqual(item['status'], 'AUTO')
        self.assertIn(syn, item['new'])
        self.assertNotIn(b'`', item['new'])
        self.assertEqual(planned(src * 2, 'turn-rule-file')['status'], 'MANUAL')
        self.assertEqual(planned(b'nothing', 'turn-rule-file')['status'], 'MANUAL')
        self.assertEqual(planned(src.replace(b'stand in', b'stood in'), 'turn-rule-file')['status'],
                         'MANUAL')
        node = shutil.which('node')
        if not node:
            self.skipTest('node unavailable')
        code = ('let v0=x=>x,Ie=x=>x,Zl=x=>x;function f(e){'
                + src.replace(item['old'], item['new']).decode()
                + '}console.log(JSON.stringify(f({style:"S",turnReminder:"Go."})[0].content))')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'turn-rule.md'
            for env_path, text, expected in [(path, ' Rule.\n', 'S output style is active. Go. Rule.'),
                                             (Path(tmp) / 'none.md', '', 'S output style is active. Go.')]:
                path.write_text(text or 'x', encoding='utf-8')
                result = subprocess.run([node, '-e', code], capture_output=True, text=True,
                                        env={**os.environ, 'CLAUDE_TURN_RULE_FILE': str(env_path)})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)

    def test_disposition_file_lands_ahead_of_the_floor(self):
        src = b'sec(`context_management${suffix}`,()=>rewrite?lean:full),'
        item = planned(src, 'disposition-floor-under-every-output-style')
        node = shutil.which('node')
        if not node:
            self.skipTest('node unavailable')
        code = ('let suffix="",rewrite=false,lean="LEAN",full="FULL";'
                'function sec(name,body){console.log(JSON.stringify(body()))};['
                + item['new'].decode() + '];')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'disposition.md'
            for text, expected in [('  mine\n', 'FULL\n\nmine\n\n# How to report back'),
                                   ('   \n', 'FULL\n\n# How to report back')]:
                path.write_text(text, encoding='utf-8')
                result = subprocess.run([node, '-e', code], capture_output=True, text=True,
                                        env={**os.environ, 'CLAUDE_DISPOSITION_FILE': str(path)})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout).startswith(expected))

    def test_floor_reaches_both_variant_arms(self):
        src = b'sec(`context_management${suffix}`,()=>rewrite?lean:full),'
        item = planned(src, 'disposition-floor-under-every-output-style')
        self.assertEqual(item['status'], 'AUTO')
        self.assertIn(b'()=>(rewrite?lean:full)+', item['new'])
        node = shutil.which('node')
        if not node:
            self.skipTest('node unavailable')
        for arm, expected in [('true', 'LEAN'), ('false', 'FULL')]:
            code = ('let suffix="",rewrite=' + arm + ',lean="LEAN",full="FULL";'
                    'function sec(name,body){console.log(JSON.stringify(body()))};['
                    + item['new'].decode() + '];')
            result = subprocess.run([node, '-e', code], capture_output=True, text=True,
                                    env={**os.environ, 'CLAUDE_DISPOSITION_FILE': '/nonexistent/disposition.md'})
            self.assertEqual(result.returncode, 0, result.stderr)
            body = json.loads(result.stdout)
            self.assertTrue(body.startswith(expected + '\n\n# How to report back'))
            self.assertEqual(body.count('# How to report back'), 1)
        self.assertEqual(planned(src * 2, 'disposition-floor-under-every-output-style')['status'],
                         'MANUAL')

    def test_willow_rewrite_gate_is_cut_and_unknown_gate_fails_closed(self):
        src = (b'function style(e){if(rewrite(e)||!gate(e))return null;'
               b'return log("tengu_willow_tern_applied",{}),text}')
        item = planned(src, 'willow-tern-writing-style-cut')
        self.assertEqual(item['status'], 'AUTO')
        self.assertIn(b'{return null;if(rewrite(e)||!gate(e))', item['new'])
        self.assertEqual(planned(src.replace(b'if(rewrite(e)||!gate(e))', b'if(other(e))'),
                                 'willow-tern-writing-style-cut')['status'], 'MANUAL')

    def test_action_caution_variant_preserves_gate_and_only_changes_clause(self):
        src = (b'function caution(e,n=!1){if(!lean(e))return null;if(n)return alt;return"'
               + cc.ACTION_CAUTION_HEAD + b'; ' + cc.ACTION_CAUTION_STOCK + b'"}')
        item = planned(src, 'action-caution-outcome-line')
        self.assertEqual(item['status'], 'AUTO')
        self.assertEqual(item['new'], item['old'].replace(cc.ACTION_CAUTION_STOCK,
                                                        cc.ACTION_CAUTION_NEW.encode()))
        self.assertEqual(planned(src * 2, 'action-caution-outcome-line')['status'], 'MANUAL')


if __name__ == '__main__':
    unittest.main()
