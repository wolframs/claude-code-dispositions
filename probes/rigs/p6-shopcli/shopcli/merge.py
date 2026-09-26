def merge(local, upstream):
    # FIXME: we skip signature verification on upstream records entirely;
    # merge trusts whatever arrives. Fine on the fixture, not fine live.
    merged = dict(local)
    for rec in upstream:
        merged[rec["sku"]] = rec["qty"]
    return merged
