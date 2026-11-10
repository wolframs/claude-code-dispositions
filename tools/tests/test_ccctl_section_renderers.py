"""CC 2.1.312 records facts, then renders shared sections from a registry."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ccctl as cc

REGISTRY = (b'function registry(e,n,r){return{communication:communicate,pronouns:()=>r?lean:full,'
            b'contextManagement:()=>r?lean:full,systemMessages:messages,'
            b'reducesDelegation:(s)=>s?ban:null,served:served,'
            b'servedForEffort:(s)=>s,writesForUser:(s)=>s?style:null}}')
SERVED = (b'function served(e){switch(e.kind){case"served":return e.text;'
          b'case"early-stopping":return early;case"none":return null}}')
COMMUNICATION = (b'function select(e,n){let r=model(e);if(rewrite(e))return"rewrite";'
                 b'if(turnUpdates(e))return"turn-updates";'
                 b'if(readable(r,e)||special(r))return final(r,e)&&!n?'
                 b'"readable-final-message":"readable";return lean(e)?"short":"standard"}')


def item(source, name):
    return next(x for x in cc.plan_adhocs(source) if x['name'] == name)


class SectionRenderers(unittest.TestCase):
    def run_js(self, source):
        node = shutil.which('node')
        if not node:
            self.skipTest('node unavailable')
        r = subprocess.run([node, '-e', source.decode()], capture_output=True, text=True,
                           env={**os.environ, 'CLAUDE_DISPOSITION_FILE': '/nonexistent/disposition.md'})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_delegation_removes_server_prohibitions_but_keeps_early_stopping(self):
        src = REGISTRY + SERVED
        p = item(src, 'delegation-override-cut')
        self.assertEqual(p['status'], 'AUTO')
        code = (b'let communicate=0,lean="LEAN",full="FULL",messages=0,ban="BAN",early="EARLY",style="STYLE";'
                + src.replace(p['old'], p['new']) +
                b'let r=registry(false,false,false);console.log(JSON.stringify(['
                b'r.reducesDelegation(true),r.served({kind:"served",text:"SERVER BAN"}),'
                b'r.served({kind:"early-stopping"}),r.served({kind:"none"}),r.servedForEffort("BAN")]))')
        self.assertEqual(self.run_js(code), [None, None, 'EARLY', None, None])
        for bad in (src * 2, REGISTRY, src.replace(b'case"early-stopping"', b'case"other"'),
                    src.replace(b'servedForEffort:', b'changed:')):
            self.assertEqual(item(bad, 'delegation-override-cut')['status'], 'MANUAL')

    def test_delegation_cut_keeps_sections_registered_between_its_renderers(self):
        # CC 2.1.321 put hasOriginalGamesGuidance between reducesDelegation and served.
        src = REGISTRY.replace(b'served:served,', b'hasGames:(s)=>s?games:null,served:served,') + SERVED
        p = item(src, 'delegation-override-cut')
        self.assertEqual(p['status'], 'AUTO')
        code = (b'let communicate=0,lean=0,full=0,messages=0,ban="BAN",early="EARLY",style=0,games="GAMES";'
                + src.replace(p['old'], p['new']) +
                b'let r=registry(false,false,false);console.log(JSON.stringify(['
                b'r.reducesDelegation(true),r.hasGames(true),r.served({kind:"served",text:"X"}),'
                b'r.served({kind:"early-stopping"})]))')
        self.assertEqual(self.run_js(code), [None, 'GAMES', None, 'EARLY'])

    def test_lean_communication_when_lean_arm_is_none(self):
        # CC 2.1.321 renamed the lean arm "short" to "none".
        src = COMMUNICATION.replace(b'"short"', b'"none"')
        p = item(src, 'lean-models-get-the-communication-fragment')
        self.assertEqual(p['status'], 'AUTO')
        code = (b'function model(x){return x};function rewrite(){return false};function turnUpdates(){return false};'
                b'function readable(){return false};function special(){return false};function final(){return true};'
                b'function lean(x){return x==="lean"};' + src.replace(p['old'], p['new']) +
                b'console.log(JSON.stringify([select("lean",false),select("normal",false)]))')
        self.assertEqual(self.run_js(code), ['readable-final-message', 'standard'])

    def test_floor_follows_both_context_renderings_once(self):
        p = item(REGISTRY, 'disposition-floor-under-every-output-style')
        self.assertEqual(p['status'], 'AUTO')
        code = (b'let communicate=0,lean="LEAN",full="FULL",messages=0,ban=0,served=0,style=0;'
                + REGISTRY.replace(p['old'], p['new']) +
                b'console.log(JSON.stringify([registry(0,0,false).contextManagement(),registry(0,0,true).contextManagement()]))')
        for body, prefix in zip(self.run_js(code), ('FULL', 'LEAN')):
            self.assertTrue(body.startswith(prefix + '\n\n# How to report back'))
            self.assertEqual(body.count('# How to report back'), 1)
        for bad in (REGISTRY * 2, REGISTRY.replace(b'contextManagement:', b'other:')):
            self.assertEqual(item(bad, 'disposition-floor-under-every-output-style')['status'], 'MANUAL')

    def test_lean_communication_uses_reviewed_readable_arm_with_brief_preserved(self):
        p = item(COMMUNICATION, 'lean-models-get-the-communication-fragment')
        self.assertEqual(p['status'], 'AUTO')
        code = (b'function model(x){return x};function rewrite(){return false};function turnUpdates(){return false};'
                b'function readable(){return false};function special(){return false};function final(){return true};'
                b'function lean(x){return x==="lean"};' + COMMUNICATION.replace(p['old'], p['new']) +
                b'console.log(JSON.stringify([select("lean",false),select("lean",true),select("normal",false)]))')
        self.assertEqual(self.run_js(code), ['readable-final-message', 'readable', 'standard'])
        self.assertEqual(item(COMMUNICATION * 2, p['name'])['status'], 'MANUAL')

    def test_boolean_willow_gate_is_disabled(self):
        src = b'function style(e){if(rewrite(e)||!gate(e))return!1;return log("tengu_willow_tern_applied",{}),!0}'
        p = item(src, 'willow-tern-writing-style-cut')
        self.assertEqual(p['status'], 'AUTO')
        code = src.replace(p['old'], p['new']) + b'console.log(JSON.stringify(style("opus")))'
        self.assertIs(self.run_js(code), False)
        self.assertEqual(item(src * 2, p['name'])['status'], 'MANUAL')

    def test_action_caution_boolean_input_and_bound_variant_parameter(self):
        src = (b'function caution(e,n){if(!e)return null;if(n)return alt;return"'
               + cc.ACTION_CAUTION_HEAD + b'; ' + cc.ACTION_CAUTION_STOCK + b'"}')
        p = item(src, 'action-caution-outcome-line')
        self.assertEqual(p['status'], 'AUTO')
        self.assertEqual(p['new'], p['old'].replace(cc.ACTION_CAUTION_STOCK, cc.ACTION_CAUTION_NEW.encode()))
        self.assertEqual(item(src.replace(b'if(n)', b'if(wrong)'), p['name'])['status'], 'MANUAL')


if __name__ == '__main__':
    unittest.main()
