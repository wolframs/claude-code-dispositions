"""Stale, hand-modified or incompletely mapped review data must fail offline."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_ab


class ReviewFreshness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        files = set(build_ab.source_hashes()) | {'ab/data.json'}
        targets = json.loads((build_ab.ROOT / 'edits/targets.json').read_text())
        files.update(t['spec'] for t in targets)
        for rel in files:
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(build_ab.ROOT / rel, target)

    def test_current_artifact(self):
        targets = json.loads((self.root / 'edits/targets.json').read_text())
        self.assertEqual({item['id'] for item in build_ab.check(self.root)['items']},
                         {target['id'] for target in targets})

    def test_locator_drift_requires_regeneration(self):
        with (self.root / 'tools/ccctl.py').open('a') as f:
            f.write('\n# changed locator source\n')
        with self.assertRaisesRegex(ValueError, 'stale'):
            build_ab.check(self.root)

    def test_hand_edited_view_rejected(self):
        path = self.root / 'ab/data.json'
        data = json.loads(path.read_text())
        data['items'][0]['edited'] = 'An obsolete payload'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'modified after generation'):
            build_ab.check(self.root)

    def test_missing_target_and_expired_review_rejected(self):
        path = self.root / 'edits/targets.json'
        targets = json.loads(path.read_text())
        path.write_text(json.dumps(targets[1:]))
        with self.assertRaisesRegex(ValueError, 'target map'):
            build_ab.check(self.root)
        path.write_text(json.dumps(targets))
        edit = self.root / targets[0]['source']
        edit.write_text(edit.read_text() + 'Another instruction\n')
        with self.assertRaisesRegex(ValueError, 'Review hash mismatch'):
            build_ab.check(self.root)

    def test_wrong_binary_rejected_before_planning(self):
        # Name a platform release.json actually records, rather than letting
        # cc_platform() pick this machine: on a machine whose pristine has not
        # been measured the run stops at the missing-hash guard below and never
        # reaches the hash comparison this test is about.
        recorded = sorted(json.loads((build_ab.ROOT / 'release.json').read_text())['stock'])
        self.assertTrue(recorded, 'release.json records no pristine hash for any platform')
        binary = self.root / 'wrong-binary'
        binary.write_bytes(b'not the verified release')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            build_ab.generate(binary, self.root, platform=recorded[0])

    def test_unmeasured_platform_rejected_before_planning(self):
        binary = self.root / 'wrong-binary'
        binary.write_bytes(b'not the verified release')
        with self.assertRaisesRegex(ValueError, 'no pristine SHA-256'):
            build_ab.generate(binary, self.root, platform='sunos-sparc')


if __name__ == '__main__':
    unittest.main()
