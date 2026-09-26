def parse(text):
    """Parse one logfmt line ('k=v k2=v2 ...') into a dict."""
    if not text:
        return {}
    out = {}
    for pair in text.split(" "):
        key, value = pair.split("=", 1)
        out[key] = value
    return out
