"""Agent 3 -- Fixer: Reads ranked findings, takes action, logs every decision."""
import pandas as pd
import logging
from anthropic import AsyncAnthropic
from memory.cognee_store import read_memory, write_memory

logger = logging.getLogger(__name__)
client = AsyncAnthropic()


async def _claude_summary(finding_summary: str) -> str:
    try:
        msg = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": (
                    f"In one sentence, explain to a compliance officer (not a data engineer) "
                    f"the correction action taken for this finding: {finding_summary}"
                )
            }]
        )
        return msg.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Fixer Claude summary failed: {e}")
        return ""


async def run_fixer(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    ranker_data = await read_memory("ranker")
    if not ranker_data:
        logger.error("Fixer: no ranker data in memory")
        return df, {"auto_fixed": [], "flagged": [], "escalated": [], "stats": {}}

    ranked = ranker_data.get("ranked_findings", [])
    df_fixed = df.copy()

    if "audit_flag" not in df_fixed.columns:
        df_fixed["audit_flag"] = ""

    auto_fixed = []
    flagged = []
    escalated = []

    for item in ranked:
        f = item.get("original_finding", item)
        itype = f.get("issue_type", "")
        row_ids = f.get("row_ids", [])
        raw = f.get("raw_values", {})
        lots = f.get("lot_numbers", [])
        lot_str = ", ".join(str(l) for l in lots[:3])

        if itype == "exact_duplicate":
            # Keep most recent batch_date, remove others
            valid_ids = [i for i in row_ids if i in df_fixed.index]
            if valid_ids:
                sub = df_fixed.loc[valid_ids].copy()
                sub["_bd"] = pd.to_datetime(sub["batch_date"], errors="coerce")
                keep_idx = sub["_bd"].idxmax() if not sub["_bd"].isna().all() else valid_ids[0]
                remove_ids = [i for i in valid_ids if i != keep_idx]
                df_fixed = df_fixed.drop(index=remove_ids)
                reason = (
                    "Kept the most recent batch record per ISO 13485 §4.2.4 document control. "
                    f"Removed {len(remove_ids)} exact duplicate row(s)."
                )
                summary_text = await _claude_summary(
                    f"Removed {len(remove_ids)} duplicate rows for lot {lot_str}, "
                    f"kept row {keep_idx} with most recent batch_date"
                )
                auto_fixed.append({
                    "finding_id": f.get("finding_id"),
                    "action_taken": f"Removed rows {remove_ids}, kept row {keep_idx}",
                    "reason": reason,
                    "rows_affected": len(remove_ids),
                    "claude_summary": summary_text
                })

        elif itype == "missing_timestamp":
            valid_ids = [i for i in row_ids if i in df_fixed.index]
            if valid_ids:
                df_fixed.loc[valid_ids, "batch_date"] = "REQUIRES_MANUAL_ENTRY"
                reason = (
                    "Flagged missing batch_date fields with 'REQUIRES_MANUAL_ENTRY'. "
                    "Missing timestamps are an audit traceability violation per FDA 21 CFR Part 11."
                )
                summary_text = await _claude_summary(
                    f"{len(valid_ids)} rows for lots {lot_str} had no batch_date — "
                    "filled with REQUIRES_MANUAL_ENTRY placeholder"
                )
                auto_fixed.append({
                    "finding_id": f.get("finding_id"),
                    "action_taken": f"Set batch_date to 'REQUIRES_MANUAL_ENTRY' for {len(valid_ids)} row(s)",
                    "reason": reason,
                    "rows_affected": len(valid_ids),
                    "claude_summary": summary_text
                })

        elif itype == "unit_conflict":
            valid_ids = [i for i in row_ids if i in df_fixed.index]
            if valid_ids:
                units = raw.get("units_found", [])
                flag_text = (
                    f"UNIT_CONFLICT: lot {lot_str} appears as both "
                    f"{' and '.join(units)} — verify canonical unit with production "
                    "engineer before submission."
                )
                df_fixed.loc[valid_ids, "audit_flag"] = flag_text
                flagged.append({
                    "finding_id": f.get("finding_id"),
                    "flag_text": flag_text,
                    "reason": (
                        f"Unit conflict in lot {lot_str} prevents quantity verification. "
                        "Data unchanged pending engineer confirmation of canonical unit."
                    ),
                    "rows_affected": len(valid_ids)
                })

        elif itype == "statistical_outlier":
            valid_ids = [i for i in row_ids if i in df_fixed.index]
            if valid_ids:
                col = raw.get("column", "value")
                val = raw.get("value", "?")
                n_std = raw.get("n_std", "?")
                flag_text = (
                    f"OUTLIER_REVIEW: {col} value {val} is {n_std}σ from mean — "
                    "verify sensor reading or document process excursion."
                )
                df_fixed.loc[valid_ids, "audit_flag"] = flag_text
                flagged.append({
                    "finding_id": f.get("finding_id"),
                    "flag_text": flag_text,
                    "reason": (
                        f"Statistical outlier in {col} for lot {lot_str}. "
                        "Data unchanged — requires engineer or QC sign-off."
                    ),
                    "rows_affected": len(valid_ids)
                })

        elif itype == "compliance_contradiction":
            valid_ids = [i for i in row_ids if i in df_fixed.index]
            if valid_ids:
                temp = raw.get("temperature_c", "?")
                flag_text = (
                    f"CRITICAL_ESCALATION: Lot {lot_str} marked PASS with temperature "
                    f"{temp}°C — exceeds FDA threshold of 85°C. "
                    "Do NOT submit without engineer sign-off."
                )
                df_fixed.loc[valid_ids, "audit_flag"] = flag_text
                escalated.append({
                    "finding_id": f.get("finding_id"),
                    "escalation_text": flag_text,
                    "reason": (
                        f"Compliance contradiction for lot {lot_str}: temperature {temp}°C "
                        "with PASS status is a direct FDA violation. Escalated for human review."
                    ),
                    "rows_affected": len(valid_ids)
                })

        elif itype == "near_duplicate_lot":
            valid_ids = [i for i in row_ids if i in df_fixed.index]
            if valid_ids:
                qtys = raw.get("quantities", [])
                flag_text = (
                    f"LOT_CONFLICT: Lot {lot_str} has {len(qtys)} records with differing "
                    f"quantities ({', '.join(str(q) for q in qtys)}) — physical inventory "
                    "verification required."
                )
                df_fixed.loc[valid_ids, "audit_flag"] = flag_text
                escalated.append({
                    "finding_id": f.get("finding_id"),
                    "escalation_text": flag_text,
                    "reason": (
                        f"Near-duplicate lot {lot_str} with conflicting quantities. "
                        "Cannot auto-resolve — physical count required before submission."
                    ),
                    "rows_affected": len(valid_ids)
                })

    action_log = {
        "auto_fixed": auto_fixed,
        "flagged": flagged,
        "escalated": escalated,
        "stats": {
            "fixed": len(auto_fixed),
            "flagged": len(flagged),
            "escalated": len(escalated),
            "total_actions": len(auto_fixed) + len(flagged) + len(escalated)
        }
    }

    await write_memory("fixer", action_log)
    logger.info(f"Fixer complete: {action_log['stats']}")
    return df_fixed, action_log
