# AuditGuard AI: production hardening pass
*2026-09-25, second pass after the landmine fixes (see `2026-09-25-landmine-fixes.md`). Version 2.1.0.*

## Summary

This pass assumed the first pass had bugs and went looking for them. It used adversarial review, property-based fuzzing, a 500k-row load test, a real-SDK test harness, browser QA and a Docker build. It found and fixed **8 real defects**, made the pipeline **about 10× faster** at the row limit, and closed every place where a check could be skipped silently. The project now has 139 tests at 98% coverage, and a weekly CI job runs thousands of generated datasets.

## Defects found and fixed

| # | Defect | How it was found | Impact before the fix | Regression test |
|---|---|---|---|---|
| D1 | Fixer proposed a unit conversion even when the quantities **did not** reconcile, and logged the changes while saying "left unchanged" | Code review | False change-log entries in a regulated record | `test_disagreeing_units_are_flagged_not_converted` |
| D2 | A customer column named `audit_flags` was silently overwritten | Code review | Customer data lost in the proposed copy | `test_existing_audit_flags_column_is_never_overwritten` |
| D3 | Truncated (`max_tokens`) or refused structured output raised a pydantic error that escaped the LLM wrapper, so **the whole audit failed** | Real SDK behind a mock HTTP transport | One long summary could kill a run | `test_incomplete_or_refused_output_falls_back` |
| D4 | A stray carriage return made the header check raise `csv.Error`, which surfaced as HTTP 500 | Hypothesis fuzzing of arbitrary bytes | Crash on malformed input | `test_ingest_never_crashes_on_arbitrary_bytes` |
| D5 | Repeated header names slipped past the duplicate-column check (pandas renames them `x.1`) | New ingest tests | Two columns silently treated as different | `test_rejections[duplicate column]` |
| D6 | Non-numeric measurements, PASS records missing a measurement, and unknown statuses (`OK`, empty) were skipped by every rule | Code review | **False clean**: an unverifiable PASS record produced no finding | `test_invalid_values_are_never_silently_skipped` |
| D7 | Row numbers drifted when the file had blank lines | Code review | The wrong spreadsheet row cited in the change log | `test_blank_lines_keep_spreadsheet_row_numbers` |
| D8 | The summary validator rejected any decimal (`112.4`), so LLM prose almost never reached the report | Code review | The LLM feature was effectively dead | `test_faithful_llm_summary_is_used` |

Also found and fixed:
- A lot recorded twice with only a different `notes` value produced no finding.
- The pagination reset in the UI ran one render late.
- A pandas FutureWarning in the outlier statistics. Warnings are now errors in the test suite.

## Performance (500,000 rows, 4,344 findings)

| | Before | After |
|---|---|---|
| Scout | 60 s | 5.5 s |
| Fixer | 118 s | 1.8 s |
| Full pipeline, rules only | ≈ 3.5 min | ≈ 21 s (23 s in the browser) |
| Peak memory | not measured | 560 MB for a 35 MB file |

How:
- Every detector is vectorised, and Python loops only visit rows that produce a finding.
- Duplicates are dropped in one batch, and rank and action lookups use dicts.
- CSV neutralisation is vectorised.
- The two LLM calls now run concurrently.

## New capabilities

- **Real-world CSVs.** The parser handles Windows-1252 (Excel "CSV"), `;`/tab/pipe delimiters and header aliases (`Lot Number`). Original headers are restored in exports, and output row numbers are spreadsheet rows. Excel workbooks and UTF-16 get actionable messages.
- **New check: missing or invalid value.** It covers non-numeric or infinite numbers, quantity ≤ 0, PASS records without a spec'd measurement, and missing or unknown status.
- **Nothing is skipped silently.** Each check that can't run (for example, a missing column) is listed in the report and the UI.
- **Report package.** Adds `findings.csv` (every finding). The PDF shows up to 1,000 findings and says explicitly when there are more. The PDF also includes a reproducibility block (app version, spec SHA-256, model, encoding), advisory ranking suggestions and scope notes.
- **Configurable outlier threshold** (`outlier_robust_z` in the spec). The default of 3.5 flags about 5 in 10,000 readings of normal data, which is noticeable at 500k rows, so sites can tune it.
- **Validated spec file.** min ≤ max, numeric limits, the canonical unit maps to 1, and a status can't be both pass and fail. A bad spec stops the app at startup, never mid-audit.
- **Stricter LLM summary validation.** Every number must appear in the data sent to the model, the true total must be stated, and "no issues" claims are rejected when findings exist.
- **Operations.**
  - A concurrency limit with a visible queue.
  - Bounded parse concurrency.
  - A data-retention time-to-live (default 120 min).
  - Upload size is rejected from `Content-Length` before the body is read.
  - Run logs with durations.
  - A version module.
- **Security.**
  - A strict Content Security Policy (no inline scripts), `nosniff`, `no-referrer`, no framing, and `no-store` on the API.
  - Self-hosted fonts, so the UI makes zero third-party requests (verified in the browser).
- **Frontend.**
  - Search by lot, ID or issue.
  - Pagination for thousands of findings.
  - An executive-summary panel with scope notes.
  - A queued state.
  - A clear message if the server restarts mid-run.
  - Client-side size check.
  - Findings CSV download.

## Verification performed

| Check | Result |
|---|---|
| `pytest` (139 tests, warnings as errors) | pass, 98% line coverage |
| Hypothesis `thorough` profile, about 9,000 generated datasets | pass, after fixing D4 |
| Real Anthropic SDK against a mock transport: request shape, 400/401/404/429/500/529, connection errors, retries, truncation, refusal | pass, after fixing D3 |
| Browser QA (Chromium): sample flow, 4 downloads, search, filters, pagination, client and server rejections, oversize file, queued second run, 500k-row run, mobile at 390 px | pass. No console errors, no CSP violations, zero third-party requests, no horizontal overflow. |
| Docker: build, non-root user (uid 10001), healthcheck, audit the sample inside the container | pass |
| `ruff check` | pass |

**Not verified:** a call to the live Claude API (no key in this environment) and the GitHub Actions run. The CI workflow includes a Docker smoke test that will exercise the image on every push once the branch can be pushed.
