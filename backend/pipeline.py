"""Runs the four stages for one upload. Fails closed: if a stage errors, later stages are
skipped and the run is marked failed, so a broken audit can never look like a clean one."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import pandas as pd

from agents.fixer import run_fixer
from agents.narrator import run_narrator
from agents.ranker import rank_findings, review_ranking
from agents.scout import run_scout
from llm import StructuredLLM
from models import ISSUE_LABELS, FixerResult, RankerResult, ScoutResult
from runs import STAGES, Run
from settings import SpecLimits
from utils.csv_export import changelog_to_csv, dataframe_to_csv
from utils.pdf_gen import render_pdf

logger = logging.getLogger(__name__)


async def _scout(run: Run, df: pd.DataFrame, spec: SpecLimits, llm: StructuredLLM) -> dict:
    result: ScoutResult = await asyncio.to_thread(run_scout, df, spec)
    run.board["scout"] = result
    return result.summary


async def _ranker(run: Run, df: pd.DataFrame, spec: SpecLimits, llm: StructuredLLM) -> dict:
    ranked = rank_findings(run.board["scout"].findings)
    result: RankerResult = await review_ranking(ranked, llm)
    run.board["ranker"] = result
    return {"ranked": len(result.ranked), "review": result.review_status}


async def _fixer(run: Run, df: pd.DataFrame, spec: SpecLimits, llm: StructuredLLM) -> dict:
    corrected, result = await asyncio.to_thread(run_fixer, df, run.board["ranker"].ranked, spec)
    run.board["fixer"] = result
    run.artifacts["corrected.csv"] = await asyncio.to_thread(dataframe_to_csv, corrected)
    run.artifacts["changelog.csv"] = changelog_to_csv(result.change_log)
    return result.stats


async def _narrator(run: Run, df: pd.DataFrame, spec: SpecLimits, llm: StructuredLLM) -> dict:
    ranker: RankerResult = run.board["ranker"]
    report = await run_narrator(
        reference_id=run.reference_id, dataset_name=run.dataset_name,
        source_sha256=run.source_sha256, generated_at=datetime.now(UTC),
        scout=run.board["scout"], ranked=ranker.ranked, fixer=run.board["fixer"],
        review_status=ranker.review_status, llm=llm,
    )
    run.board["narrator"] = report
    run.artifacts["report.pdf"] = await asyncio.to_thread(render_pdf, report)
    return {"findings_in_report": len(report.findings), "summary": report.summary_source}


STAGE_FUNCS = {"scout": _scout, "ranker": _ranker, "fixer": _fixer, "narrator": _narrator}


async def execute(run: Run, df: pd.DataFrame, spec: SpecLimits, llm: StructuredLLM) -> None:
    for index, stage in enumerate(STAGES):
        await run.set_stage(stage, "running")
        try:
            summary = await STAGE_FUNCS[stage](run, df, spec, llm)
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
    findings = []
    for r in ranker.ranked:
        action = fixer.action_for(r.finding_id)
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
            "csv_lines": [row + 2 for row in r.row_ids],
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
        "summary_source": run.board["narrator"].summary_source,
        "stats": {**scout.summary, **fixer.stats, "rows_after_correction": fixer.rows_out},
        "findings": findings,
    }
