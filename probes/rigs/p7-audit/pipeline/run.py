from . import ingest, transform, enrich, export


def run(path):
    rows = ingest.read_csv(path)
    rows = ingest.normalize_headers(rows)
    rows = transform.cast_types(rows)
    rows = transform.dedupe_rows(rows)
    rows = transform.apply_currency_v2(rows)
    rows = enrich.attach_segments(rows)
    rows = enrich.score_quality(rows)
    return export.to_feed_v2(rows)
