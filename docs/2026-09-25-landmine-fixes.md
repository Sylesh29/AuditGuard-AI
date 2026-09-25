# AuditGuard AI: landmine fixes and production hardening
*2026-09-25. Follow-up to the interview grill sheet. Branch `sylesh/gallant-goldberg-vqjfe1`.*

## Summary

Every landmine (L1–L14) in the grill sheet is fixed or made accurate, and each fix has an automated test. The architecture now matches the pitch: **rules detect and decide; the LLM only explains.** LLM calls went from about 27 sequential calls (25 with unused output) to 2. Both are optional and validated. Cognee is gone. Stages hand off through a per-run blackboard.

## Landmine → fix → proof

| # | Landmine | Fix | Test |
|---|---|---|---|
| L1 | Fixer overwrote `audit_flag`, so the critical flag was lost | Flags accumulate per row in rank order in `audit_flags`, plus a long-format `flags` list. Flags on a removed duplicate move to the kept row. | `test_higher_priority_flag_is_never_lost`, `test_every_finding_gets_exactly_one_action_and_its_flag` |
| L2 | PDF dropped findings 21–33 (`ranked[:20]`) | The findings table is rendered from data, not by the LLM. Every finding is listed. | `test_pdf_lists_every_finding` (extracts PDF text and checks every ID) |
| L3 | About 25 of 27 LLM calls produced output nobody saw | Scout and Fixer LLM calls removed. Regulatory notes are a reviewed static table (`regulatory.py`). The only LLM calls left are the ranking review (shown in the UI) and the executive summary (in the PDF). | `test_review_suggestions_are_validated_and_advisory` |
| L4 | SSE double framing (`data: data:`) hidden by an empty `catch` | One framing function (`runs.sse_frame`) and a plain `StreamingResponse`. The frontend uses a real `EventSource` and logs parse errors. | `test_sse_frames_are_single_encoded`, `test_full_audit_over_http` (asserts no `data: data:`) |
| L5 | Fixer deleted and overwrote regulated records | The source is read-only. Corrections go to a proposed copy, and a change log records old value, new value, rule and reason. The SHA-256 of the upload is in the report. Missing dates stay empty. | `test_fixer_never_modifies_source`, `test_only_exact_duplicates_are_removed_and_each_is_logged`, `test_missing_dates_stay_empty` |
| L6 | "FDA 85°C threshold" doesn't exist | Limits come from `config/spec_limits.json`: per product, both min and max, labelled as example values. The rule now also catches the low side (LOT401 at 7.2°C PASS). "FDA threshold" wording is removed everywhere. | `test_contradiction_uses_configured_spec_both_sides`, `test_per_product_spec_overrides_default` |
| L7 | Unit conflicts double-counted as quantity outliers | Units are normalised before any statistics. Quantity is no longer outlier-scored (it follows order size), and out-of-spec values aren't re-reported as outliers. | `test_unit_conflict_is_not_double_counted_as_quantity_problem`, `test_out_of_spec_value_is_not_also_reported_as_outlier` |
| L8 | README claimed unit normalisation that didn't exist | Implemented as a *proposed* conversion to kg, logged in the change log, and only when the quantities reconcile after conversion. Otherwise the row is flagged. | `test_unit_normalisation_is_proposed_with_old_values` |
| L9 | 3σ masking (91–102°C not flagged) | Robust modified z-score (median/MAD, 3.5), per product baseline, with a mean-absolute-deviation fallback. | `test_robust_outliers_are_not_masked_by_other_outliers` |
| L10 | "Real Cognee memory handoffs" was mostly a dict | Cognee removed. Each run has its own blackboard (`Run.board`), so concurrent uploads are isolated. | `test_concurrent_runs_are_isolated` |
| L11 | "Most recent batch_date" logic was meaningless for exact duplicates | The lowest row is kept, and every removed copy is preserved in full in the change log. | covered by L5 tests |
| L12 | README numbers were wrong (200 rows, "under a minute", 4 vs 6 types) | README and brief rewritten with measured numbers: 160 rows, 41 findings, about 0.2 s rules-only. LLM latency is marked as not yet measured. | — |
| L13 | ISO §4.2.4 cited for records | §4.2.5 (control of records). All references live in `regulatory.py` with a disclaimer. | — |
| L14 | "Why the Judges Liked It" section | Removed. The README now just says where the project started. | — |

## Other grill-sheet items fixed along the way

- **Q9 Fail closed.** If a stage errors, later stages are skipped and the run is marked failed. There is no PDF and no "clean" empty report (`test_stage_failure_fails_closed`).
- **Q17 Concurrency.** Per-run IDs and endpoints (`/api/runs/{id}/…`). The store is bounded and evicts finished runs.
- **Q18 Duplicates.** Row hashing is NaN-safe and ignores whitespace and case (`test_exact_duplicates_with_missing_values_and_whitespace`).
- **Q19 Near-duplicates.** Broadened to "a lot number must identify one record". Reports *which* fields conflict, so an inspector mismatch is caught too.
- **Q23 Dates.** One finding per lot. Unparseable and future dates are new checks (`test_invalid_and_future_dates`).
- **Q30 / Q46 Structured outputs.** Both LLM calls use `messages.parse` with Pydantic schemas. No more substring matching.
- **Q40 Hallucination.** The summary is rejected if it cites an unknown F-ID or a number that isn't in the data (`test_unfaithful_llm_summary_falls_back_to_template`).
- **Q41 Prompt injection.** The filename never reaches the LLM (`test_filename_never_reaches_the_llm`). User data is wrapped in delimited blocks, and the prompt says to treat it as data.
- **Q44 LLM outage.** Typed SDK errors, timeout and retry settings. Every call has a deterministic fallback.
- **Q48 / Q50 / Q51 API.** `POST /api/runs` reads and validates the upload *before* starting (413/415/422). SSE is a resumable GET (`test_events_resume_from_last_event_id`).
- **Q52** `425` replaced with `409` and `404`. **Q53** CPU work runs in `asyncio.to_thread`. **Q54** `lifespan` replaces `on_event`.
- **Q55 PDF.** Every string is XML-escaped (`test_pdf_escapes_markup_from_data`), there is one signature block, and column widths are fixed.
- **Q56 CORS.** Off by default because the API serves the frontend on the same origin. Allowed origins are set through an env variable.
- **Q57 / Q59 Frontend.** No in-browser Babel. React 18.3.1 and htm are vendored (no CDN at runtime). There's no hard-coded `localhost`. The "written to Cognee" text is gone, and the LLM status comes from `/api/health`.
- **Q60 Accessibility.** The live log is an ARIA live region, finding rows work from the keyboard, and the upload control is a labelled input.
- **Q73 / Q74 Security.** Upload size and row limits. CSV formula-injection protection on every export (`test_csv_export_neutralises_formulas`). `push-to-github.bat` is deleted: it rewrote `.gitignore` and hard-coded a different git identity.
- **Ops.** Dockerfile (non-root, healthcheck), GitHub Actions CI (ruff + pytest), pinned requirements.

## Updated rapid-fire answers (§17 of the grill sheet)

| Question | New answer |
|---|---|
| How many findings on the sample? | 41 across 160 rows: 22 critical, 12 moderate, 7 minor |
| How many actions? | 13 corrections proposed, 14 escalated, 14 flagged. 18 change-log entries. 152 rows in the proposed copy. |
| Top-ranked issue? | F009: LOT400 marked PASS at 112.4°C (spec 20–85) |
| LLM calls per run? | 2, both optional: ranking review and executive summary |
| Is it deterministic? | Detection, ranking, corrections, tables and counts are. Only the summary prose isn't, and it's validated. |
| Outlier method? | Median/MAD modified z-score > 3.5, per product |
| Is Cognee used? | No. Removed in favour of a per-run blackboard. |

**Why 41 and not 33:** LOT401 (PASS at 7.2°C) is now a contradiction. The LOT301–305 unit-conflict lots also have conflicting batch dates, which is a real second issue per lot. Missing dates are reported per lot (7, not 1). Quantity outliers are gone.

## Before the interview

- Run `pytest -q` once so you can say "45 tests" first-hand.
- Time one real run with `ANTHROPIC_API_KEY` set and memorise the number. It isn't measured yet.
- Replace the example spec in `backend/config/spec_limits.json` if you demo with other data.
- Re-read the new Dive Deep story: "I audited my own output, found the flag overwrite and the missing findings, and added invariant tests so they can't come back."
