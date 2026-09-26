"""Windows shell syntax checks must not route native paths through WSL."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_repo


class SyntaxBashTests(unittest.TestCase):
    def test_git_bash_wins_over_wsl_on_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'cmd').mkdir()
            (root / 'bin').mkdir()
            git = root / 'cmd' / 'git.exe'
            bash = root / 'bin' / 'bash.exe'
            git.touch()
            bash.touch()
            paths = {'git': str(git), 'bash': 'C:/Windows/System32/bash.exe'}
            with patch.object(check_repo.sys, 'platform', 'win32'), patch.object(
                    check_repo.shutil, 'which', side_effect=paths.get):
                self.assertEqual(check_repo.syntax_bash(), str(bash.resolve()))

    def test_wsl_only_is_unavailable_for_native_checks(self):
        with patch.object(check_repo.sys, 'platform', 'win32'), patch.object(
                check_repo.shutil, 'which', side_effect=lambda name:
                'C:/Windows/System32/bash.exe' if name == 'bash' else None):
            self.assertIsNone(check_repo.syntax_bash())

    def test_posix_keeps_path_bash(self):
        with patch.object(check_repo.sys, 'platform', 'linux'), patch.object(
                check_repo.shutil, 'which', return_value='/bin/bash'):
            self.assertEqual(check_repo.syntax_bash(), '/bin/bash')


if __name__ == '__main__':
    unittest.main()
