import io
from logrelay.core import summarize

def test_counts_by_service():
    src = io.StringIO(
        "2026-08-01T10:00:00 api INFO started\n"
        "2026-08-01T10:00:01 api WARN slow response\n"
        "2026-08-01T10:00:02 worker INFO tick\n")
    assert summarize(src) == {"api": 2, "worker": 1}
