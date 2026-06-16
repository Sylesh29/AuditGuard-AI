"""Agent 4 -- Narrator: Reads all agent memory, generates plain-English audit narrative PDF."""
import json
import logging
from datetime import date
from anthropic import AsyncAnthropic
from memory.cognee_store import read_all_memory, write_memory

logger = logging.getLogger(__name__)
client = AsyncAnthropic()

NARRATIVE_SYSTEM_PROMPT = """
You are writing an official data integrity correction summary for a
manufacturing compliance officer to submit to a regulatory auditor.

Rules:
- Write in plain English. Zero technical jargon.
- Every claim must be traceable to a specific finding ID (F001, F002, etc.)
- Severity language: use "critical" for HIGH, "moderate" for MED, "minor" for LOW
- Do not say "AI", "model", "algorithm", or "automated system" -- say "AuditGuard AI"
- Every finding must have a reason a human can follow and sign off on
- The tone is formal but readable -- this is a legal document
- Do not make up any findings. Only report what is in the data provided.
- Format tables with markdown pipe syntax exactly as instructed.
"""


async def run_narrator(original_filename: str) -> str:
    all_memory = await read_all_memory()

    scout = all_memory.get("scout", {})
    ranker = all_memory.get("ranker", {})
    fixer = all_memory.get("fixer", {})

    scout_findings = scout.get("findings", [])
    ranked = ranker.get("ranked_findings", [])
    scout_summary = scout.get("summary", {})
    fixer_stats = fixer.get("stats", {})
    auto_fixed = fixer.get("auto_fixed", [])
    flagged_items = fixer.get("flagged", [])
    escalated_items = fixer.get("escalated", [])

    open_items = escalated_items + flagged_items

    context = {
        "dataset": original_filename,
        "total_rows_scanned": scout_summary.get("rows_scanned", 0),
        "scout_findings_count": scout_summary,
        "ranked_priorities": [
            {
                "rank": r["rank"],
                "finding_id": r["finding_id"],
                "issue_type": r["issue_type"],
                "severity": r["severity"],
                "ranking_reason": r["ranking_reason"],
                "rows_affected": r["rows_affected"]
            }
            for r in ranked[:20]
        ],
        "actions_taken": {
            "auto_fixed": [{"finding_id": a["finding_id"], "action": a["action_taken"], "reason": a["reason"]} for a in auto_fixed],
            "flagged": [{"finding_id": f["finding_id"], "flag": f["flag_text"]} for f in flagged_items],
            "escalated": [{"finding_id": e["finding_id"], "escalation": e["escalation_text"]} for e in escalated_items]
        },
        "open_items": open_items,
        "stats": {
            "total_issues": scout_summary.get("total", 0),
            "HIGH": scout_summary.get("HIGH", 0),
            "MED": scout_summary.get("MED", 0),
            "LOW": scout_summary.get("LOW", 0),
            "auto_fixed": fixer_stats.get("fixed", 0),
            "flagged": fixer_stats.get("flagged", 0),
            "escalated": fixer_stats.get("escalated", 0)
        }
    }

    today = date.today().strftime("%B %d, %Y")
    date_code = date.today().strftime("%Y%m%d")
    context_json = json.dumps(context, indent=2)

    user_message = f"""Generate a complete data integrity correction summary using this audit data:
{context_json}

Use EXACTLY this structure:

# Data Integrity Correction Summary
**Dataset:** {original_filename}
**Audit Date:** {today}
**Prepared by:** AuditGuard AI
**Reference ID:** AUDIT-{date_code}

## Executive Summary
[3 sentences: what was scanned, total issues by severity, what was resolved]

## Findings & Actions Taken

| Finding ID | Issue | Severity | Rows | Action | Reason |
|------------|-------|----------|------|--------|--------|
[one row per finding from the ranked list, plain English, no jargon]

## Open Items Requiring Human Sign-Off
[bullet list of all escalated and flagged items with specific instructions for the compliance officer]

## Auto-Corrections Applied
[bullet list of all auto-fixed items with what was done and why]

## Certification
I certify that this Data Integrity Correction Summary accurately reflects
the automated review conducted by AuditGuard AI on {today}. All
auto-corrections were logged with traceable reasons. Open items have been
escalated for human verification prior to regulatory submission.

Signed: _________________________  Title: _________________________

Date: __________________________  Facility: ______________________"""

    try:
        msg = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=NARRATIVE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}]
        )
        narrative = msg.content[0].text.strip()
    except Exception as e:
        logger.error(f"Narrator Claude call failed: {e}")
        narrative = f"# Data Integrity Correction Summary\n\nError generating narrative: {e}\n"

    await write_memory("narrator", {"narrative": narrative, "date": today})
    logger.info("Narrator complete")
    return narrative
