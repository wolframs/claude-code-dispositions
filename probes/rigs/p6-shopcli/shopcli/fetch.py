import json, pathlib

def fetch_upstream(cfg):
    # offline fixture standing in for the API during development
    fixture = pathlib.Path(__file__).parent / "fixture_upstream.json"
    return json.loads(fixture.read_text())
