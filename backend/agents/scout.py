"""Agent 1 -- Scout: Reads dataset, detects all data quality issues, enriches with FDA context."""
import pandas as pd
import numpy as np
import logging
from anthropic import AsyncAnthropic
from memory.cognee_store import write_memory

logger = logging.getLogger(__name__)
client = AsyncAnthropic()


async def _enrich_with_claude(finding: dict) -> str:
    """Ask Claude for the specific FDA/ISO violation in plain English."""
    try:
        msg = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    f"Given this manufacturing data issue: {finding['issue_type']} — "
                    f"{finding['reason']}. "
                    "In exactly one sentence, what specific FDA 21 CFR Part 11 or ISO 13485 "
                    "requirement does this violate? Write in plain English a compliance officer "
                    "can understand. No jargon."
                )
            }]
        )
        return msg.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Claude enrichment failed: {e}")
        return ""


async def run_scout(df: pd.DataFrame) -> dict:
    findings = []
    fid = 1

    def make_id():
        nonlocal fid
        fid_str = f"F{fid:03d}"
        fid += 1
        return fid_str

    # 1. EXACT DUPLICATES
    dup_mask = df.duplicated(keep=False)
    if dup_mask.any():
        dup_groups = df[dup_mask].groupby(list(df.columns))
        for _, group in dup_groups:
            row_ids = list(group.index)
            lot_nums = list(group["lot_number"].unique())
            findings.append({
                "finding_id": make_id(),
                "issue_type": "exact_duplicate",
                "severity": "HIGH",
                "row_ids": row_ids,
                "lot_numbers": lot_nums,
                "raw_values": {"duplicate_rows": len(row_ids)},
                "reason": (
                    f"Lot(s) {', '.join(lot_nums)} appear {len(row_ids)} times with identical "
                    "values in every column. Duplicate records cannot both be true."
                ),
                "audit_risk": (
                    "Exact duplicate records indicate a data entry or system error. "
                    "Regulators require each lot record to be unique and traceable."
                ),
                "regulatory_note": ""
            })

    # 2. NEAR-DUPLICATE LOT NUMBERS (same lot, quantity differs by small amount)
    lot_groups = df.groupby("lot_number")
    for lot, group in lot_groups:
        if len(group) > 1:
            # Check if they're not exact duplicates but differ only in quantity
            cols_except_qty = [c for c in df.columns if c != "quantity"]
            if group[cols_except_qty].duplicated(keep=False).all():
                quantities = list(group["quantity"].unique())
                if len(quantities) > 1 and max(quantities) - min(quantities) <= 5:
                    row_ids = list(group.index)
                    findings.append({
                        "finding_id": make_id(),
                        "issue_type": "near_duplicate_lot",
                        "severity": "HIGH",
                        "row_ids": row_ids,
                        "lot_numbers": [lot],
                        "raw_values": {"quantities": quantities},
                        "reason": (
                            f"Lot {lot} has {len(group)} records with differing quantities "
                            f"({', '.join(str(q) for q in quantities)}). "
                            "Only one quantity can be correct."
                        ),
                        "audit_risk": (
                            "Conflicting quantity records for the same lot make it impossible "
                            "to verify the actual batch size — a fundamental traceability failure."
                        ),
                        "regulatory_note": ""
                    })

    # 3. UNIT CONFLICTS
    if "unit" in df.columns:
        for lot, group in df.groupby("lot_number"):
            units = group["unit"].dropna().unique()
            if len(units) > 1:
                row_ids = list(group.index)
                findings.append({
                    "finding_id": make_id(),
                    "issue_type": "unit_conflict",
                    "severity": "MED",
                    "row_ids": row_ids,
                    "lot_numbers": [lot],
                    "raw_values": {"units_found": list(units)},
                    "reason": (
                        f"Lot {lot} appears with units {' and '.join(units)}. "
                        "The same batch cannot have two different units of measure."
                    ),
                    "audit_risk": (
                        "Unit conflicts prevent quantity verification. "
                        "An auditor cannot confirm the batch weight without knowing the canonical unit."
                    ),
                    "regulatory_note": ""
                })

    # 4. STATISTICAL OUTLIERS
    numeric_cols = ["temperature_c", "pressure_bar", "quantity"]
    for col in numeric_cols:
        if col not in df.columns:
            continue
        vals = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(vals) < 10:
            continue
        mean = vals.mean()
        std = vals.std()
        if std == 0:
            continue
        threshold = 3.0
        outlier_idx = vals[abs(vals - mean) > threshold * std].index
        for idx in outlier_idx:
            val = df.loc[idx, col]
            n_std = abs(float(val) - mean) / std
            findings.append({
                "finding_id": make_id(),
                "issue_type": "statistical_outlier",
                "severity": "MED",
                "row_ids": [int(idx)],
                "lot_numbers": [str(df.loc[idx, "lot_number"])],
                "raw_values": {
                    "column": col,
                    "value": float(val),
                    "mean": round(mean, 2),
                    "std": round(std, 2),
                    "n_std": round(n_std, 1)
                },
                "reason": (
                    f"Lot {df.loc[idx, 'lot_number']}: {col} = {val}, which is "
                    f"{n_std:.1f} standard deviations from the dataset mean of {mean:.1f}. "
                    "This is statistically extreme."
                ),
                "audit_risk": (
                    f"Extreme {col} values may indicate a faulty sensor reading or "
                    "a process excursion that was not properly documented."
                ),
                "regulatory_note": ""
            })

    # 5. MISSING TIMESTAMPS
    if "batch_date" in df.columns:
        missing_mask = df["batch_date"].isnull() | (df["batch_date"].astype(str).str.strip() == "")
        missing_idx = list(df[missing_mask].index)
        if missing_idx:
            lot_nums = list(df.loc[missing_idx, "lot_number"].unique())
            findings.append({
                "finding_id": make_id(),
                "issue_type": "missing_timestamp",
                "severity": "LOW",
                "row_ids": missing_idx,
                "lot_numbers": lot_nums,
                "raw_values": {"missing_count": len(missing_idx)},
                "reason": (
                    f"{len(missing_idx)} rows have no batch_date. "
                    "Without a production date, the lot cannot be placed in the manufacturing timeline."
                ),
                "audit_risk": (
                    "Missing batch dates are a traceability violation. "
                    "Regulators require every lot to have a documented production date."
                ),
                "regulatory_note": ""
            })

    # 6. COMPLIANCE CONTRADICTIONS (CRITICAL)
    if "compliance_status" in df.columns and "temperature_c" in df.columns:
        critical_mask = (
            (df["compliance_status"].str.upper() == "PASS") &
            (pd.to_numeric(df["temperature_c"], errors="coerce") > 85.0)
        )
        crit_idx = list(df[critical_mask].index)
        for idx in crit_idx:
            temp = df.loc[idx, "temperature_c"]
            lot = df.loc[idx, "lot_number"]
            findings.append({
                "finding_id": make_id(),
                "issue_type": "compliance_contradiction",
                "severity": "HIGH",
                "row_ids": [int(idx)],
                "lot_numbers": [str(lot)],
                "raw_values": {
                    "temperature_c": float(temp),
                    "compliance_status": "PASS",
                    "fda_threshold": 85.0
                },
                "reason": (
                    f"Lot {lot} is marked PASS but temperature_c = {temp}°C, "
                    "which exceeds the FDA threshold of 85°C. "
                    "This record would cause automatic audit failure."
                ),
                "audit_risk": (
                    "A PASS status on a lot with critically high temperature is a direct "
                    "contradiction. Submitting this to an FDA auditor without correction "
                    "is a regulatory violation."
                ),
                "regulatory_note": ""
            })

    # Enrich HIGH findings with Claude context
    for f in findings:
        if f["severity"] == "HIGH":
            note = await _enrich_with_claude(f)
            f["regulatory_note"] = note

    summary = {
        "total": len(findings),
        "HIGH": sum(1 for f in findings if f["severity"] == "HIGH"),
        "MED": sum(1 for f in findings if f["severity"] == "MED"),
        "LOW": sum(1 for f in findings if f["severity"] == "LOW"),
        "rows_scanned": len(df)
    }

    result = {"findings": findings, "summary": summary}
    await write_memory("scout", result)
    logger.info(f"Scout complete: {summary}")
    return result
