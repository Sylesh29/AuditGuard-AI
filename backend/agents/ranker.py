"""Agent 2 -- Ranker: Prioritizes scout findings by audit risk with deterministic rules + Claude review."""
import logging
from anthropic import AsyncAnthropic
from memory.cognee_store import read_memory, write_memory

logger = logging.getLogger(__name__)
client = AsyncAnthropic()

# Deterministic priority order (lower number = higher priority)
PRIORITY_MAP = {
    "compliance_contradiction": 1,
    "near_duplicate_lot": 2,
    "exact_duplicate": 3,
    "unit_conflict": 4,
    "statistical_outlier": 5,
    "missing_timestamp": 6,
}

SEVERITY_ORDER = {"HIGH": 0, "MED": 1, "LOW": 2}


def _ranking_reason(finding: dict, rank: int) -> str:
    itype = finding["issue_type"]
    lots = ", ".join(finding.get("lot_numbers", [])[:3])
    raw = finding.get("raw_values", {})

    if itype == "compliance_contradiction":
        temp = raw.get("temperature_c", "?")
        return (
            f"Ranked #{rank}: compliance_status='PASS' with temperature {temp}°C "
            "exceeds FDA threshold of 85°C — automatic audit failure if submitted unchanged."
        )
    elif itype == "near_duplicate_lot":
        qtys = raw.get("quantities", [])
        return (
            f"Ranked #{rank}: Lot {lots} has {len(qtys)} records with differing quantities "
            f"({', '.join(str(q) for q in qtys)}) — physical inventory verification required "
            "before submission."
        )
    elif itype == "exact_duplicate":
        n = raw.get("duplicate_rows", "?")
        return (
            f"Ranked #{rank}: Lot {lots} has {n} identical records — only one can be the "
            "true production record per ISO 13485 §4.2.4 document control requirements."
        )
    elif itype == "unit_conflict":
        units = raw.get("units_found", [])
        return (
            f"Ranked #{rank}: Lot {lots} appears as both {' and '.join(units)} — "
            "auditor cannot confirm batch weight without a canonical unit of measure."
        )
    elif itype == "statistical_outlier":
        col = raw.get("column", "value")
        val = raw.get("value", "?")
        n_std = raw.get("n_std", "?")
        return (
            f"Ranked #{rank}: {col} = {val} is {n_std}σ from the dataset mean — "
            "verify sensor reading or document process excursion before submission."
        )
    elif itype == "missing_timestamp":
        return (
            f"Ranked #{rank}: {len(finding.get('row_ids', []))} lot(s) including {lots} "
            "have no batch_date — missing production dates are an audit traceability violation."
        )
    else:
        return f"Ranked #{rank}: {itype} — review required before regulatory submission."


async def run_ranker() -> dict:
    scout_data = await read_memory("scout")
    if not scout_data:
        logger.error("Ranker: no scout data in memory")
        return {"ranked_findings": [], "summary": {}}

    findings = scout_data.get("findings", [])

    # Sort deterministically
    def sort_key(f):
        priority = PRIORITY_MAP.get(f["issue_type"], 99)
        severity = SEVERITY_ORDER.get(f["severity"], 9)
        return (priority, severity)

    sorted_findings = sorted(findings, key=sort_key)

    ranked = []
    for rank, f in enumerate(sorted_findings, start=1):
        reason = _ranking_reason(f, rank)
        ranked.append({
            "rank": rank,
            "finding_id": f["finding_id"],
            "issue_type": f["issue_type"],
            "severity": f["severity"],
            "ranking_reason": reason,
            "rows_affected": len(f.get("row_ids", [])),
            "lot_numbers": f.get("lot_numbers", []),
            "raw_values": f.get("raw_values", {}),
            "original_finding": f,
            "claude_note": ""
        })

    # Claude review — flag any ranking disagreements
    try:
        ranked_summary = [
            {"rank": r["rank"], "finding_id": r["finding_id"],
             "issue_type": r["issue_type"], "severity": r["severity"]}
            for r in ranked
        ]
        msg = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": (
                    f"Here is a ranked list of manufacturing data issues for FDA audit review:\n"
                    f"{ranked_summary}\n\n"
                    "Flag any specific ranking that seems incorrect from an FDA regulatory "
                    "perspective. For each disagreement, state the finding_id, current rank, "
                    "and your suggested rank with a one-sentence reason. "
                    "If the ranking is correct, reply: 'Ranking confirmed — no changes recommended.'"
                )
            }]
        )
        claude_feedback = msg.content[0].text.strip()

        # Attach Claude's note to any finding it mentioned
        for r in ranked:
            if r["finding_id"] in claude_feedback:
                r["claude_note"] = claude_feedback
                break
        # If Claude confirmed, attach note to first finding
        if "confirmed" in claude_feedback.lower() or "no changes" in claude_feedback.lower():
            if ranked:
                ranked[0]["claude_note"] = "Ranking confirmed by regulatory AI review."

    except Exception as e:
        logger.warning(f"Ranker Claude review failed: {e}")

    result = {
        "ranked_findings": ranked,
        "summary": scout_data.get("summary", {})
    }
    await write_memory("ranker", result)
    logger.info(f"Ranker complete: {len(ranked)} findings ranked")
    return result
