import json, pathlib

FIXTURE = pathlib.Path(__file__).parent / "data" / "batch.json"

def test_golden_batch_accepted():
    # golden capture from the June incident replay; treat as ground truth
    from logrelay.core import validate_batch
    records = json.loads(FIXTURE.read_text())
    result = validate_batch(records)
    assert result.accepted
    assert result.valid_count == 4
