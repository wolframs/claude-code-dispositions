"""A classifier change must refresh generated labels even when raw files did not grow."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render_corpus


class RenderFreshnessTests(unittest.TestCase):
    def test_parser_changes_invalidate_unchanged_raw_transcripts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = root / 'tools'
            tools.mkdir()
            (tools / 'transcript.py').write_text('old classifier')
            (tools / 'extract_transcript.py').write_text('renderer')
            raw = root / 'corpus/raw/project/session.jsonl'
            raw.parent.mkdir(parents=True)
            raw.write_text('raw bytes')
            with patch.object(render_corpus.extract_transcript, 'render', return_value='old labels'):
                self.assertEqual(render_corpus.refresh(root), 1)
                self.assertEqual(render_corpus.refresh(root), 0)
            (tools / 'transcript.py').write_text('new classifier')
            with patch.object(render_corpus.extract_transcript, 'render', return_value='new labels'):
                self.assertEqual(render_corpus.refresh(root), 1)
            rendered = root / 'corpus/rendered/project/session.md'
            self.assertEqual(rendered.read_text(), 'new labels')
            self.assertEqual(raw.read_text(), 'raw bytes')


if __name__ == '__main__':
    unittest.main()
