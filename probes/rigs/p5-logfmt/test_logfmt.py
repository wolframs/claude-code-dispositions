from logfmt import parse

def test_basic():
    assert parse("a=1 b=two") == {"a": "1", "b": "two"}

def test_empty_string():
    assert parse("") == {}
