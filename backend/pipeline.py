"""Runs the four stages for one upload.

Fails closed: if a stage errors, later stages are skipped and the run is marked failed, so a
broken audit can never look like a clean one.

The two LLM calls are independent (the ranking review needs only the ranking, the summary
needs only the counts), so the review starts as soon as ranking is done and overlaps with the
Fixer and the summary. Neither can change a fact in the report.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from agents.fixer import run_fixer
from agents.narrator import build_report, report_stats, write_summary
from agents.ranker import rank_findings, review_ranking
from agents.scout import run_scout
from ingest import Dataset
from llm import StructuredLLM
from models import ISSUE_LABELS, FixerResult, RankerResult, ScoutResult
from runs import STAGES, Run
from settings import SpecLimits
from utils.csv_export import changelog_to_csv, dataframe_to_csv, findings_to_csv, spreadsheet_row
from utils.pdf_gen import render_pdf
from version import __version__

logger = logging.getLogger(__name__)


class Context:
    """Everything a stage may read. Stage outputs go on the run's blackboard."""

    def __init__(self, run: Run, dataset: Dataset, spec: SpecLimits, llm: StructuredLLM) -> None:
        self.run, self.dataset, self.spec, self.llm = run, dataset, spec, llm


async def _scout(ctx: Context) -> dict:
    result: ScoutResult = await asyncio.to_thread(run_scout, ctx.dataset.frame, ctx.spec)
    ctx.run.board["scout"] = result
    return result.summary


async def _ranker(ctx: Context) -> dict:
    ranked = rank_findings(ctx.run.board["scout"].findings)
    ctx.run.board["ranked"] = ranked
    ctx.run.board["review_task"] = asyncio.create_task(review_ranking(ranked, ctx.llm))
    return {"ranked": len(ranked)}


async def _fixer(ctx: Context) -> dict:
    corrected, result = await asyncio.to_thread(
        run_fixer, ctx.dataset.frame, ctx.run.board["ranked"], ctx.spec)
    ctx.run.board["fixer"] = result
    ctx.run.artifacts["corrected.csv"] = await asyncio.to_thread(
        dataframe_to_csv, corrected, ctx.dataset.original_columns)
    ctx.run.artifacts["changelog.csv"] = changelog_to_csv(result.change_log)
    return result.stats


async def _narrator(ctx: Context) -> dict:
    run, scout, fixer = ctx.run, ctx.run.board["scout"], ctx.run.board["fixer"]
    ranked = run.board["ranked"]
    generated_at = datetime.now(UTC)
    review, (summary, source) = await asyncio.gather(
        run.board["review_task"],
        write_summary(report_stats(scout, fixer), ranked, ctx.llm, generated_at),
    )
    run.board["ranker"] = review
    report = build_report(
        reference_id=run.reference_id, dataset_name=run.dataset_name,
        source_sha256=run.source_sha256, generated_at=generated_at, scout=scout,
        ranked=review.ranked, fixer=fixer, review_status=review.review_status,
        summary=summary, summary_source=source, configuration=configuration(ctx),
    )
    report.notes.extend(ctx.dataset.notes)
    run.board["narrator"] = report
    run.artifacts["findings.csv"] = findings_to_csv(review.ranked, fixer)
    run.artifacts["report.pdf"] = await asyncio.to_thread(render_pdf, report)
    return {"in_report": len(report.findings), "review": review.review_status,
            "summary": source}


STAGE_FUNCS = {"scout": _scout, "ranker": _ranker, "fixer": _fixer, "narrator": _narrator}


def configuration(ctx: Context) -> dict[str, str]:
    return {
        "AuditGuard version": __version__,
        "Process spec SHA-256": ctx.spec.sha256 or "(built-in)",
        "Language model": ctx.llm.model if ctx.llm.enabled else "not used",
        "File encoding": f"{ctx.dataset.encoding}, delimiter {ctx.dataset.delimiter!r}",
    }


async def execute(run: Run, dataset: Dataset, spec: SpecLimits, llm: StructuredLLM,
                  slots: asyncio.Semaphore) -> None:
    ctx = Context(run, dataset, spec, llm)
    if slots.locked():
        await run.emit({"type": "run", "status": "queued"})
    async with slots:
        started = time.perf_counter()
        try:
            await _execute(ctx)
        finally:
            task = run.board.get("review_task")
            if task and not task.done():
                task.cancel()
        logger.info("run %s %s in %.2fs (%d rows)", run.run_id, run.status,
                    time.perf_counter() - started, len(dataset.frame))


async def _execute(ctx: Context) -> None:
    run = ctx.run
    for index, stage in enumerate(STAGES):
        await run.set_stage(stage, "running")
        try:
            summary = await STAGE_FUNCS[stage](ctx)
        except Exception:
            logger.exception("run %s: stage %s failed", run.run_id, stage)
            await run.set_stage(stage, "error", message="Internal error in this stage.")
            for skipped in STAGES[index + 1:]:
                await run.set_stage(skipped, "skipped")
            await run.finish("failed", f"The {stage} stage failed, so no report was produced.")
            return
        await run.set_stage(stage, "complete", summary=summary)
    await run.finish("complete")


def findings_payload(run: Run) -> dict:
    """The joined view the UI renders: one row per finding with its action."""
    ranker: RankerResult = run.board["ranker"]
    fixer: FixerResult = run.board["fixer"]
    scout: ScoutResult = run.board["scout"]
    report = run.board["narrator"]
    actions = fixer.action_map()
    findings = []
    for r in ranker.ranked:
        action = actions.get(r.finding_id)
        findings.append({
            "rank": r.rank,
            "finding_id": r.finding_id,
            "issue_type": r.issue_type,
            "issue_label": ISSUE_LABELS[r.issue_type],
            "severity": r.severity,
            "reason": r.reason,
            "ranking_reason": r.ranking_reason,
            "regulatory_reference": r.regulatory_reference,
            "rows_affected": len(r.row_ids),
            "spreadsheet_rows": [spreadsheet_row(x) for x in r.row_ids],
            "lot_numbers": r.lot_numbers,
            "action": action.action if action else None,
            "action_description": action.description if action else None,
            "action_reason": action.reason if action else None,
            "review_suggestion": r.review_suggestion.model_dump() if r.review_suggestion else None,
        })
    return {
        "run_id": run.run_id,
        "reference_id": run.reference_id,
        "review_status": ranker.review_status,
        "summary_source": report.summary_source,
        "executive_summary": report.executive_summary,
        "skipped_checks": scout.skipped_checks,
        "notes": report.notes,
        "stats": {**scout.summary, **fixer.stats, "rows_after_correction": fixer.rows_out},
        "findings": findings,
    }
