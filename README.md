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

In a regulated setting you must be able to say *why* a record was flagged and get the same answer twice. So detection, ranking and corrections are plain code. Claude is used in two places, and both are optional:

| Where | What the LLM does | Guardrail |
|---|---|---|
| Ranker | Gives a second opinion on the ranking | Advisory only. The ranking never changes. Suggestions are validated against real finding IDs and shown to a human. |
| Narrator | Writes the executive-summary paragraph | Every table, count and open item is rendered from code. The prose is rejected, and a template is used instead, if it cites a finding ID or number that doesn't exist. |

Without an API key, the full pipeline still runs and the report uses a template summary.

## Pipeline

```
Browser ── POST /api/runs (CSV) ──► validate (size, type, UTF-8, required columns) ── 4xx on bad input
                                     │ 202 {run_id}
        ◄── GET /api/runs/{id}/events (SSE, resumable via Last-Event-ID)
                                     │
   ┌─────────── per-run blackboard (no state shared between uploads) ───────────┐
   │ Scout     rules → findings (normalise → dedupe → units → spec → outliers → dates)
   │ Ranker    fixed priority order + advisory LLM review
   │ Fixer     proposed corrected copy + change log + accumulated row flags
   │ Narrator  report built from data + LLM summary (validated) → PDF
   └──── fail closed: a stage error skips the rest and marks the run failed ─────┘
                                     │
   GET /api/runs/{id}/findings | report.pdf | corrected.csv | changelog.csv
```

## What it detects

| Issue | Rule | Severity | Action |
|---|---|---|---|
| Status contradicts spec | A PASS record with a parameter outside the **configured process spec** (per product, low and high side) | Critical | Escalated |
| Conflicting lot records | The same lot number appears as different records. Reports which fields disagree. | Critical | Escalated |
| Exact duplicate | Identical after whitespace and case normalisation (NaN-safe) | Critical | Removal proposed, original kept in the change log |
| Unit conflict | The same lot is recorded in different units | Moderate | Conversion to the canonical unit is proposed when the quantities reconcile. Otherwise flagged. |
| Statistical outlier | Robust modified z-score (median/MAD, \|z\| > 3.5) per product, on process measurements | Moderate | Flagged |
| Invalid batch date | Not a YYYY-MM-DD date, or a date in the future | Moderate | Flagged |
| Missing batch date | Empty | Minor | Flagged. The field stays empty and is never guessed. |

Detection order matters. Duplicates are resolved first, so one root cause is reported once. Units are normalised before any statistics. A value already reported as out of spec is not reported again as an outlier.

On `data/sample.csv` (160 rows) it reports **41 findings** (22 critical, 12 moderate, 7 minor): 13 corrections proposed, 14 escalated, and 14 flagged. The rules-only pipeline takes about 0.2 s. With an API key it adds two sequential Claude calls. I haven't measured that latency yet.

## Data-integrity guarantees

- **The source is never modified.** Corrections go into a separate *proposed* copy (`corrected.csv`), and each change is logged with its old value, new value, rule, finding ID and reason (`changelog.csv`). The report records the SHA-256 of the uploaded file.
- **Flags never overwrite each other.** A row with several findings carries all of them in rank order, in the `audit_flags` column.
- **Every finding is in the report.** The findings table is rendered from data, not by the LLM.
- **Unknown truths are escalated, not guessed.** For example: which of two conflicting lot records is real, or a missing production date.

> Spec limits in `backend/config/spec_limits.json` are **example values, not regulatory limits**. Acceptance criteria come from each manufacturer's validated process specification. Replace them before real use. Regulatory references in the report are orientation for the quality team (ISO 13485:2016 clauses and ALCOA+), not legal conclusions.

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

**Tests and lint** (also run in CI):

```bash
pytest -q          # from the repository root
ruff check backend
```

## Configuration

All settings are environment variables. See `backend/.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables the LLM review and summary |
| `AUDITGUARD_LLM_MODEL` | `claude-sonnet-4-6` | Model for both LLM calls |
| `AUDITGUARD_SPEC_FILE` | `backend/config/spec_limits.json` | Process spec: per-product limits, PASS statuses, unit conversions |
| `AUDITGUARD_MAX_UPLOAD_MB` / `AUDITGUARD_MAX_ROWS` | 20 / 500000 | Upload limits |
| `AUDITGUARD_ALLOWED_ORIGINS` | none | CORS origins, only for split frontend deployments |

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/runs` | Multipart `file`. Returns `202` with `run_id`. Returns `413`, `415` or `422` for bad input. |
| `GET` | `/api/runs/{id}` | Status of each stage |
| `GET` | `/api/runs/{id}/events` | Server-Sent Events. Resumable with `Last-Event-ID`. |
| `GET` | `/api/runs/{id}/findings` | Ranked findings joined with actions. Returns `409` until the run completes. |
| `GET` | `/api/runs/{id}/report.pdf` · `corrected.csv` · `changelog.csv` | Downloads. CSV cells are protected against formula injection. |
| `GET` | `/api/health` | Includes whether the LLM is enabled |

## Deployment notes and known limits

- **Authentication is not built in.** Deploy behind your SSO or reverse proxy. Run IDs are 128-bit random values, but they aren't access control.
- **Runs live in process memory** (at most 50, oldest finished runs evicted first). Run a single worker, or move run state to Postgres/Redis before scaling out.
- **Corrections are proposals.** There is no in-app approval workflow yet. Approval happens in your change-control process.
- **Not Part 11 compliant by itself.** Compliance is a property of a validated system in use. AuditGuard helps find data-integrity gaps before an inspection.

## Project layout

```
backend/
  main.py          HTTP API (app factory)
  pipeline.py      stage orchestration, fail-closed
  runs.py          per-run state, event log, SSE framing
  ingest.py        upload validation
  settings.py      env settings + process-spec loader
  llm.py           Anthropic client wrapper with graceful fallback
  models.py        typed stage contracts (Pydantic)
  regulatory.py    reviewed reference text per issue type
  agents/          scout.py · ranker.py · fixer.py · narrator.py
  utils/           pdf_gen.py · csv_export.py
  config/          spec_limits.json
  tests/           45 tests: detectors, invariants, report, API
frontend/          static React (vendored, no build step)
docs/              change reports
```
