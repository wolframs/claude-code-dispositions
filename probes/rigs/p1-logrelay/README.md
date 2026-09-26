# logrelay

Small CLI that ingests line-based service logs and produces summaries.

## Commands

- `python -m logrelay summary FILE` — per-service event counts
- `python -m logrelay import FILE` — validate and ingest a JSON batch

## Validation

A batch is a JSON array of records. Each record must have `service` (string)
and `level` (one of DEBUG/INFO/WARN/ERROR). `ts` is optional; when missing,
ingestion assigns the current time. Records failing validation are rejected
with a reason; the batch is accepted if at least one record is valid.
