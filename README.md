<div align="center">

# 🛡️ AuditGuard AI

### Pre-inspection data-integrity triage for manufacturing records

[![CI](https://github.com/Sylesh29/AuditGuard-AI/actions/workflows/ci.yml/badge.svg)](https://github.com/Sylesh29/AuditGuard-AI/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Claude](https://img.shields.io/badge/Anthropic-Claude-D97706?style=flat-square)](https://anthropic.com)

**Upload a production CSV. A four-stage pipeline finds data-integrity problems with fixed, explainable rules, ranks them by audit risk, proposes safe corrections without touching the original file, and produces a report a compliance officer can review and sign.**

*Started as a solo build at the M-AGENTS Hackathon (NYC Tech Week, June 2026, Track 01: Data Rescue).*

</div>

---

## Design principle

**Deterministic rules detect and decide. The LLM only explains.**

In a regulated setting you must be able to say *why* a record was flagged and get the same answer twice. So detection, ranking, corrections, every table and every count are plain code. Claude is used in two places, and both are optional:

| Where | What the LLM does | Guardrail |
|---|---|---|
| Ranker | Gives a second opinion on the ranking | Advisory only. The ranking never changes. Suggestions are validated against real finding IDs and shown to a human in the UI and PDF. |
| Narrator | Writes the executive-summary paragraph | Rejected, and a template used instead, unless every finding ID and number in it appears in the data it was shown. It must also state the true total and must not claim the data is clean. |

Both calls use structured outputs and run concurrently. Timeouts, rate limits, outages, truncated output and refusals all fall back to deterministic text. They never fail the audit. Without an API key, the full pipeline still runs.

## Pipeline

```
Browser ── POST /api/runs (CSV) ──► size check before reading ─► parse & validate ── 4xx on bad input
                                     │ 202 {run_id}   (runs beyond the concurrency limit queue)
        ◄── GET /api/runs/{id}/events (SSE, resumable via Last-Event-ID)
                                     │
   ┌─────────── per-run blackboard (no state shared between uploads) ───────────┐
   │ Scout     rules → findings  (normalise → dedupe → values → spec → lots/units → outliers → dates)
   │ Ranker    fixed priority order; advisory LLM review starts in the background
   │ Fixer     proposed corrected copy + change log + accumulated row flags
   │ Narrator  report built from data + validated LLM summary → PDF + CSV exports
   └──── fail closed: a stage error skips the rest and marks the run failed ─────┘
                                     │
   GET /api/runs/{id}/findings | report.pdf | findings.csv | corrected.csv | changelog.csv
```

## What it detects

| Issue | Rule | Severity | Action |
|---|---|---|---|
| Status contradicts spec | A PASS record with a parameter outside the **configured process spec** (per product, low and high side) | Critical | Escalated |
| Conflicting lot records | The same lot number appears as different records. Reports which fields disagree. | Critical | Escalated |
| Exact duplicate | Identical after whitespace and case normalisation (NaN-safe) | Critical | Removal proposed. The original is kept in the change log. |
| Missing or invalid value | Non-numeric or infinite measurement, quantity ≤ 0, a PASS record missing a spec'd measurement, missing or unrecognised status | Moderate | Flagged |
| Unit conflict | The same lot is recorded in different units | Moderate | Conversion to the canonical unit is proposed only when the quantities reconcile. Otherwise flagged. |
| Statistical outlier | Robust modified z-score (median/MAD, \|z\| > 3.5 by default) per product, on process measurements | Moderate | Flagged |
| Invalid batch date | Not an ISO date, or more than a day in the future | Moderate | Flagged |
| Missing batch date | Empty | Minor | Flagged. The field stays empty and is never guessed. |

Order matters:
- Duplicates are resolved first, so one root cause is reported once.
- Units are normalised before quantities are compared.
- A value already reported as out of spec isn't reported again as an outlier.
- Any check that can't run (for example, a missing column) is listed in the report's scope notes. Nothing is skipped silently.

On `data/sample.csv` (160 rows) it reports **41 findings**: 22 critical, 12 moderate and 7 minor. That's 13 corrections proposed, 14 escalated and 14 flagged.

## Data-integrity guarantees

- **The source is never modified.** Corrections go into a separate *proposed* copy (`corrected.csv`, with your original headers). Every change is logged with old value, new value, rule, finding ID and reason (`changelog.csv`). The report records the SHA-256 of the upload.
- **Flags never overwrite each other.** A row with several findings carries all of them in rank order in an `audit_flags` column. If your file already has one, it's left alone.
- **Every finding is in the report package.** The PDF table is rendered from data. Beyond 1,000 findings the PDF says so explicitly and points to `findings.csv`, which lists every finding.
- **Unknown truths are escalated, not guessed.** For example, which of two conflicting lot records is real, a missing production date, or a unit whose conversion doesn't reconcile.
- **Reproducible.** Each report prints the app version, the SHA-256 of the process spec, the model used (or "not used"), and how the file was decoded.

> Spec limits in `backend/config/spec_limits.json` are **example values, not regulatory limits**. Acceptance criteria come from each manufacturer's validated process specification. Replace them before real use. Regulatory references in the report are orientation for the quality team (ISO 13485:2016 clauses and ALCOA+), not legal conclusions.

## Real-world input

Uploads accept what spreadsheet tools actually export:
- UTF-8 (with or without BOM), or Windows-1252 from Excel's plain "CSV" format.
- Comma, semicolon, tab or pipe delimiters.
- Headers such as `Lot Number` or `LOT-NUMBER`.

Row numbers in every output are **spreadsheet rows** (the header is row 1), so they match what the user sees in Excel even when the file contains blank lines. Excel workbooks get a clear "save as CSV UTF-8" message.

## Performance

Measured on a 500,000-row synthetic file (4,344 findings) on one CPU:

| Stage | Time |
|---|---|
| Parse and validate | 3.5 s |
| Scout (all detectors) | 5.5 s |
| Fixer | 1.8 s |
| CSV exports | 6.3 s |
| PDF | 3.8 s |
| **Total, rules only** | **≈ 21 s** (23 s end to end in the browser) |

The sample runs in about 0.3 s. With an API key, the two Claude calls run concurrently, so wall time grows by about one call. I haven't benchmarked that yet. A regression test fails if 100k rows take longer than 15 s.

## Quickstart

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # optional: add ANTHROPIC_API_KEY
uvicorn main:app --port 8000
```

Open <http://localhost:8000> and drop in `data/sample.csv`. The API serves the frontend, so there's no separate web server and no CORS setup.

**Docker:**

```bash
docker build -t auditguard .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... auditguard
```

The image runs as a non-root user and has a healthcheck. CI builds it and audits the sample inside it.

## Testing

```bash
pytest -q                                   # from the repository root, ~1 minute
HYPOTHESIS_PROFILE=thorough pytest -q -k properties   # thousands of generated datasets
ruff check backend
```

139 tests at 98% line coverage, with warnings treated as errors:
- **Detectors** (`test_scout.py`): every rule, its edge cases, and one regression test per past bug.
- **Invariants** (`test_properties.py`, Hypothesis): for any messy input, the source is untouched, every finding gets exactly one action and a flag on a surviving row, only true duplicates are removed, change logs hold the real old values, and exports never lose a finding or emit a formula cell. The parser never crashes on arbitrary bytes.
- **Real SDK** (`test_llm.py`): the Anthropic client runs against a mock HTTP transport. It checks the exact request (model, schema, prompt) and the fallback on every error class, on truncation and on refusal.
- **API** (`test_api.py`): end to end over HTTP, including SSE resume, queueing, run isolation, fail-closed behaviour, limits, security headers and spec validation.
- **Performance** (`test_performance.py`).

## Configuration

All settings are environment variables. See `backend/.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables the LLM review and summary |
| `AUDITGUARD_LLM_MODEL` | `claude-sonnet-4-6` | Model for both LLM calls |
| `AUDITGUARD_SPEC_FILE` | `backend/config/spec_limits.json` | Process spec: per-product limits, pass/fail statuses, unit conversions, outlier threshold (`outlier_robust_z`). Validated at startup. |
| `AUDITGUARD_MAX_UPLOAD_MB` / `AUDITGUARD_MAX_ROWS` | 50 / 500000 | Upload limits |
| `AUDITGUARD_MAX_CONCURRENT_RUNS` | 2 | Further uploads queue |
| `AUDITGUARD_RUN_TTL_MINUTES` / `AUDITGUARD_MAX_STORED_RUNS` | 120 / 20 | Data retention |
| `AUDITGUARD_ALLOWED_ORIGINS` | none | CORS, only for split frontend deployments |

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/runs` | Multipart `file`. Returns `202` with `run_id`. Returns `411`, `413`, `415` or `422` for bad input, and `503` when full. |
| `GET` | `/api/runs/{id}` | Status of each stage |
| `GET` | `/api/runs/{id}/events` | Server-Sent Events. Resumable with `Last-Event-ID`. Includes a `queued` event when waiting. |
| `GET` | `/api/runs/{id}/findings` | Ranked findings with actions, summary and scope notes. Returns `409` until complete. |
| `GET` | `/api/runs/{id}/report.pdf` · `findings.csv` · `corrected.csv` · `changelog.csv` | Downloads |
| `GET` | `/api/health` | Version, LLM status and limits |

## Security

- **Strict Content Security Policy**: `default-src 'self'`, no inline scripts, `frame-ancestors 'none'`. Also `nosniff`, `no-referrer`, and `no-store` on API responses.
- **No third-party requests.** React, htm and fonts are vendored (licenses in `frontend/vendor/LICENSES.txt`).
- **Upload limits.** Size is checked from `Content-Length` before the body is read, and both parsing and runs have concurrency limits.
- **CSV formula injection** is neutralised in every export.
- **Prompt injection.** User data goes to the LLM only inside delimited blocks, the filename never reaches it, and its output is validated.
- Internal errors are logged server-side and never sent to clients.

## Deployment notes and known limits

- **Authentication is not built in.** Deploy behind your SSO or reverse proxy. Run IDs are 128-bit random values, but they aren't access control.
- **Runs live in process memory**, so run a single worker. Measured: a 35 MB, 500k-row file peaks at about 560 MB RAM while it is processed, and a finished run then holds about the upload's size in downloads until it expires. With the defaults (2 concurrent runs, 20 stored), plan for about 1.2 GB plus 20× your typical upload. To scale out, move run state to Postgres/Redis and object storage.
- **The outlier rule has a known false-alarm rate.** On normally distributed readings, a 3.5 cutoff flags about 5 in 10,000 values per column. Raise `outlier_robust_z` in the spec for very large files, and rely on the spec limits for pass/fail.
- **Corrections are proposals.** There is no in-app approval workflow yet. Approval happens in your change-control process.
- **Not Part 11 compliant by itself.** Compliance is a property of a validated system in use. AuditGuard helps find data-integrity gaps before an inspection.

## Project layout

```
backend/
  main.py          HTTP API: app factory, limits, security headers
  pipeline.py      stage orchestration, fail-closed, concurrent LLM calls
  runs.py          per-run state, event log, SSE framing, retention
  ingest.py        upload parsing: encodings, delimiters, header aliases, validation
  settings.py      env settings + validated process-spec loader
  llm.py           Anthropic client wrapper with deterministic fallback
  models.py        typed stage contracts (Pydantic)
  regulatory.py    reviewed reference text and instructions per issue type
  version.py
  agents/          scout.py · ranker.py · fixer.py · narrator.py
  utils/           pdf_gen.py · csv_export.py
  config/          spec_limits.json
  tests/           139 tests
frontend/          static React + htm, vendored, no build step
docs/              dated change reports
```
