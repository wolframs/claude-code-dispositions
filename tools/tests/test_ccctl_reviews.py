"""Review approvals must expire when either side of the reviewed pair changes."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "ccctl", Path(__file__).resolve().parents[1] / "ccctl.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


class Reviews(unittest.TestCase):
    def test_feature_inventory_excludes_descriptions_and_prompts(self):
        output = ('Available patches\nAlways Applied:\n  verbose-property\n'
                  '    Verbose property — description\n'
                  '  \x1b[32mpatches-applied-indication\x1b[0m\n')
        self.assertEqual(cc.feature_patch_ids(output),
                         ["verbose-property", "patches-applied-indication"])
        with self.assertRaises(ValueError):
            cc.feature_patch_ids("Unexpected new output format")

    def test_exact_pair_and_drift(self):
        entry = {"pieces": ["Stock ", " text"], "identifiers": [0],
                 "identifierMap": {"0": "${TOOL}"}}
        digest = lambda s: hashlib.sha256(s.encode()).hexdigest()
        review = {"stockSha256": digest("Stock ${TOOL} text"),
                  "editSha256": digest("Replacement"), "reason": "Reviewed"}
        with tempfile.TemporaryDirectory() as directory:
            edit = Path(directory) / "prompt.md"
            edit.write_bytes(b"<!-- metadata -->\r\nReplacement\r\n")
            self.assertTrue(cc.fragment_review_matches(review, entry, edit))
            for invalid in (None, {}, {**review, "reason": ""},
                            {**review, "stockSha256": "old"}):
                self.assertFalse(cc.fragment_review_matches(invalid, entry, edit))
            changed = {**entry, "identifierMap": {"0": "${OTHER_TOOL}"}}
            self.assertFalse(cc.fragment_review_matches(review, changed, edit))
            changed = {**entry, "pieces": ["New stock ", " text"]}
            self.assertFalse(cc.fragment_review_matches(review, changed, edit))
            edit.write_text("Replacement changed\n")
            self.assertFalse(cc.fragment_review_matches(review, entry, edit))


if __name__ == "__main__":
    unittest.main()
