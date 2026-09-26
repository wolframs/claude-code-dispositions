"""Legacy entrypoints must select state belonging to their own checkout."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "compat", Path(__file__).resolve().parents[1] / "ccctl_compat.py")
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


class StateSelection(unittest.TestCase):
    def test_explicit_state_and_wrong_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            root = state / "checkout"
            root.mkdir()
            config = state / "ccctl.json"
            config.write_text(json.dumps({"repoPath": "checkout"}))
            with patch.dict(os.environ, {"CCCTL_HOME": str(state)}):
                self.assertEqual(compat.state_dir(root), state.resolve())
                with self.assertRaises(RuntimeError):
                    compat.state_dir(state / "another-checkout")
            with patch.dict(os.environ, {"CCCTL_HOME": str(state / "missing")}):
                with self.assertRaises(RuntimeError):
                    compat.state_dir(root)


if __name__ == "__main__":
    unittest.main()
