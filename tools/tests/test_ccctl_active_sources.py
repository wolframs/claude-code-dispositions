"""Reachability follows Bun's records, not old source retained in the blob."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ccctl as cc


def bundle(sources, orphan=b''):
    body = bytearray(orphan + b'\0')
    records = []
    for i, source in enumerate(sources):
        name = f'chunk-{i}.js'.encode()
        no = len(body)
        body.extend(name + b'\0')
        co = len(body)
        body.extend(source + b'\0')
        runs = [(no, len(name)), (co, len(source)), *[(0, 0)] * 4]
        records.append(b''.join(struct.pack('<II', *run) for run in runs) + b'\1\0\0\0')
    mo = len(body)
    body.extend(b''.join(records))
    ao = len(body)
    body.extend(b'\0' * 4096)
    header = struct.pack('<QIIIIII', len(body), mo, len(records) * 52, 0, ao, 0, 0)
    return struct.pack('<Q', len(body) + 48) + body + header + cc.BUN_TRAILER


class ActiveSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / 'binary'

    def write(self, sources, orphan=b''):
        self.target.write_bytes(b'outside orphan\0' + bundle(sources, orphan))

    def test_read_and_marker_checks_ignore_orphaned_source(self):
        self.write([b'const active="live";'], b'const stale="dead marker";')
        self.assertIn(b'active', cc.live_blob(self.target))
        self.assertNotIn(b'stale', cc.live_blob(self.target))
        self.assertEqual(cc.binary_contains(self.target, ['dead marker']), set())
        self.assertEqual(cc.binary_contains(self.target, ['dead marker'], live=False), {'dead marker'})

    def test_writer_updates_the_active_copy_and_leaves_dead_copy_alone(self):
        source = b'const value="old target";'
        self.write([source] + [b'export{};'] * 20, source)
        ok, detail = cc.bun_source_patch(self.target, b'old target', b'new target')
        self.assertTrue(ok, detail)
        self.assertIn(b'new target', cc.live_blob(self.target))
        self.assertNotIn(b'old target', cc.live_blob(self.target))
        self.assertIn(b'old target', self.target.read_bytes())
        self.assertEqual(len(cc.bundle_sources(self.target)), 21)

    def test_two_active_copies_still_refuse_ambiguity(self):
        self.write([b'old target'] * 2 + [b'export{};'] * 19)
        before = self.target.read_bytes()
        ok, detail = cc.bun_source_patch(self.target, b'old target', b'new target')
        self.assertFalse(ok)
        self.assertIn('2x', detail)
        self.assertEqual(self.target.read_bytes(), before)

    def test_module_boundaries_cannot_create_a_marker(self):
        self.write([b'first', b'second'])
        self.assertEqual(cc.binary_contains(self.target, ['firstsecond']), set())


if __name__ == '__main__':
    unittest.main()
