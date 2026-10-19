"""Only a completed partial fleet refresh is eligible for an archive commit."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import corpus_maintenance as maintenance


class PartialRefreshTests(unittest.TestCase):
    def test_unreachable_host_after_rendering_is_a_completed_partial_refresh(self):
        self.assertTrue(maintenance.partial_refresh(SimpleNamespace(
            returncode=1, stderr='ssh: timed out\nNOT refreshed: linux\n')))

    def test_render_or_git_failures_are_not_eligible_for_commit(self):
        for code, text in ((1, 'renderer failed'), (2, 'NOT refreshed: linux'), (0, '')):
            self.assertFalse(maintenance.partial_refresh(SimpleNamespace(returncode=code, stderr=text)))


if __name__ == '__main__':
    unittest.main()
