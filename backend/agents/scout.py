"""Stage 1, Scout: deterministic data-integrity detectors.

Every detector is a pure function of the dataset and the process spec. Nothing here calls an
LLM, so the same file and spec always produce the same findings.

Order matters:
  1. Normalise a copy for comparison (whitespace, case). The source is never touched.
  2. Exact duplicates are found first; the other detectors run on one representative per
     duplicate set so one root cause is reported once.
  3. Units are converted to the canonical unit before any statistics (a lbs row is not a
     quantity outlier, it is a unit conflict).
  4. Values already reported as out of spec on a PASS record are not re-reported as outliers.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from models import Finding, ScoutResult
from regulatory import REGULATORY_REFERENCES
from settings import SpecLimits

LOT_COL = "lot_number"
PRODUCT_COL = "product_id"
DATE_COL = "batch_date"
STATUS_COL = "compliance_status"
FREE_TEXT_COLS = {"notes"}
# Process measurements only. Quantity follows order size, so it is checked by the lot and
# unit rules instead of by statistics.
OUTLIER_COLS = ("temperature_c", "pressure_bar")

MIN_BASELINE_ROWS = 10          # fewer points than this and robust stats are meaningless
ROBUST_Z_THRESHOLD = 3.5        # Iglewicz & Hoaglin modified z-score cutoff
UNIT_CONVERSION_REL_TOL = 0.01  # records that agree within 1% after conversion are "the same"


def _normalise(df: pd.DataFrame, spec: SpecLimits) -> pd.DataFrame:
    norm = df.apply(lambda col: col.map(lambda v: v.strip() if isinstance(v, str) else v))
    norm = norm.mask(norm == "")
    if STATUS_COL in norm:
        norm[STATUS_COL] = norm[STATUS_COL].str.upper()
    if spec.units.column in norm:
        norm[spec.units.column] = norm[spec.units.column].str.lower()
    return norm


def _lot(value: Any) -> str:
    return str(value) if pd.notna(value) else "(missing lot number)"


def _canonical_quantity(norm: pd.DataFrame, spec: SpecLimits) -> pd.Series | None:
    qcol, ucol = spec.units.quantity_column, spec.units.column
    if qcol not in norm:
        return None
    qty = pd.to_numeric(norm[qcol], errors="coerce")
    if ucol not in norm:
        return qty
    factors = norm[ucol].map(spec.units.factor).astype(float)
    return qty * factors


def _robust_z(values: pd.Series) -> tuple[pd.Series, float, float] | None:
    """Modified z-score 0.6745 * (x - median) / MAD, with the mean-absolute-deviation
    fallback when more than half the values are identical (MAD = 0)."""
    median = float(values.median())
    deviations = (values - median).abs()
    mad = float(deviations.median())
    if mad > 0:
        return 0.6745 * (values - median) / mad, median, mad
    mean_ad = float(deviations.mean())
    if mean_ad > 0:
        return (values - median) / (1.253314 * mean_ad), median, mean_ad
    return None


class _Collector:
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def add(self, issue_type: str, severity: str, row_ids: list[int], lots: list[str],
            reason: str, **details: Any) -> None:
        self.findings.append(Finding(
            finding_id=f"F{len(self.findings) + 1:03d}",
            issue_type=issue_type,
            severity=severity,
            row_ids=[int(r) for r in row_ids],
            lot_numbers=lots,
            details=details,
            reason=reason,
            regulatory_reference=REGULATORY_REFERENCES[issue_type],
        ))


def _exact_duplicates(norm: pd.DataFrame, out: _Collector) -> pd.DataFrame:
    """Report each duplicate set once and return the frame with one row per set."""
    row_hash = pd.util.hash_pandas_object(norm, index=False)
    duplicated = row_hash[row_hash.duplicated(keep=False)]
    for _, group in duplicated.groupby(duplicated, sort=False):
        rows = sorted(int(i) for i in group.index)
        lots = sorted({_lot(v) for v in norm.loc[rows, LOT_COL]})
        out.add(
            "exact_duplicate", "HIGH", rows, lots,
            f"Lot {', '.join(lots)} is recorded {len(rows)} times with identical values in "
            "every column. Only one of these records can be the original.",
            copies=len(rows), kept_row=rows[0],
        )
    return norm[~row_hash.duplicated(keep="first")]


def _contradictions(unique: pd.DataFrame, spec: SpecLimits, out: _Collector) -> set[tuple[int, str]]:
    """PASS records with a measured parameter outside its spec. Returns (row, column) pairs."""
    reported: set[tuple[int, str]] = set()
    if STATUS_COL not in unique or not spec.parameters:
        return reported
    numeric = {p: pd.to_numeric(unique[p], errors="coerce") for p in spec.parameters if p in unique}
    passed = unique[unique[STATUS_COL].isin(spec.pass_statuses)]
    for row_id, row in passed.iterrows():
        product = row.get(PRODUCT_COL)
        violations = []
        for param, limit in spec.limits_for(product).items():
            if param not in numeric:
                continue
            value = numeric[param].loc[row_id]
            if pd.notna(value) and limit.violated_by(float(value)):
                violations.append({"parameter": param, "value": float(value),
                                   "spec": limit.describe(), "min": limit.min, "max": limit.max})
        if not violations:
            continue
        lot = _lot(row[LOT_COL])
        described = "; ".join(f"{v['parameter']} = {v['value']:g} (spec {v['spec']})"
                              for v in violations)
        out.add(
            "compliance_contradiction", "HIGH", [row_id], [lot],
            f"Lot {lot} is marked {row[STATUS_COL]} but {described} is outside the "
            "process specification. The record contradicts itself.",
            status=row[STATUS_COL], product_id=None if pd.isna(product) else str(product),
            violations=violations,
        )
        reported.update((int(row_id), v["parameter"]) for v in violations)
    return reported


def _lot_conflicts(unique: pd.DataFrame, canonical_qty: pd.Series | None, spec: SpecLimits,
                   out: _Collector) -> None:
    """A lot number must identify exactly one record. Characterise what disagrees."""
    ucol, qcol = spec.units.column, spec.units.quantity_column
    compared = [c for c in unique.columns if c not in FREE_TEXT_COLS | {ucol, qcol, LOT_COL}]
    lots = unique[unique[LOT_COL].notna()]
    for lot, group in lots.groupby(LOT_COL, sort=False):
        if len(group) < 2:
            continue
        rows = sorted(int(i) for i in group.index)
        conflicting = [c for c in compared if group[c].nunique(dropna=False) > 1]

        units = sorted(group[ucol].dropna().unique()) if ucol in group else []
        quantities = group[qcol].tolist() if qcol in group else []
        qty_conflict = False
        agree_after_conversion = None
        if qcol in group:
            canon = canonical_qty.loc[group.index] if canonical_qty is not None else None
            if len(units) > 1 and canon is not None and canon.notna().all():
                # Published conversion factors are often rounded (2.2 lb/kg), hence a tolerance.
                spread = float(canon.max() - canon.min())
                agree_after_conversion = spread <= UNIT_CONVERSION_REL_TOL * float(canon.abs().max())
                qty_conflict = not agree_after_conversion
            else:
                qty_conflict = group[qcol].nunique(dropna=False) > 1
        if qty_conflict:
            conflicting.append(qcol)

        if len(units) > 1:
            convertible = all(spec.units.factor(u) is not None for u in units)
            conversion_note = (
                f" Converted to {spec.units.canonical}, the quantities "
                + ("agree." if agree_after_conversion else "still disagree.")
                if convertible else " At least one unit has no known conversion."
            )
            out.add(
                "unit_conflict", "MED", rows, [str(lot)],
                f"Lot {lot} is recorded in {' and '.join(units)} "
                f"(quantities {', '.join(map(str, quantities))}).{conversion_note}",
                units_found=units, quantities=quantities, convertible=convertible,
                agree_after_conversion=agree_after_conversion,
            )

        if conflicting:
            values = {c: [None if pd.isna(v) else v for v in group[c].tolist()] for c in conflicting}
            shown = "; ".join(f"{c}: {', '.join(str(v) for v in vals)}" for c, vals in values.items())
            out.add(
                "lot_conflict", "HIGH", rows, [str(lot)],
                f"Lot {lot} has {len(rows)} different records that disagree on {shown}. "
                "Only one version can be correct.",
                conflicting_fields=conflicting, values=values,
            )


def _outliers(unique: pd.DataFrame, spec: SpecLimits, already_reported: set[tuple[int, str]],
              out: _Collector) -> None:
    columns = [c for c in dict.fromkeys((*OUTLIER_COLS, *sorted(spec.parameters))) if c in unique]
    for col in columns:
        values = pd.to_numeric(unique[col], errors="coerce").dropna()

        for baseline, targets, label in _baselines(values, unique):
            scored = _robust_z(baseline)
            if scored is None:
                continue
            z, median, spread = scored
            for row_id in targets:
                score = float(z.loc[row_id])
                if abs(score) <= ROBUST_Z_THRESHOLD or (int(row_id), col) in already_reported:
                    continue
                lot = _lot(unique.loc[row_id, LOT_COL])
                value = float(values.loc[row_id])
                direction = "above" if score > 0 else "below"
                out.add(
                    "statistical_outlier", "MED", [row_id], [lot],
                    f"Lot {lot}: {col} = {value:g} is far {direction} the normal range for {label} "
                    f"(typical value {median:g}).",
                    column=col, value=value, baseline=label, median=round(median, 4),
                    spread=round(spread, 4), robust_z=round(score, 1),
                )


def _baselines(values: pd.Series, unique: pd.DataFrame):
    """Yield (baseline values, rows to score, label). Products with enough history get their
    own baseline; rows of smaller products are scored against all records."""
    if PRODUCT_COL not in unique:
        if len(values) >= MIN_BASELINE_ROWS:
            yield values, values.index, "all records"
        return
    leftover: list[pd.Index] = []
    for product, group in values.groupby(unique.loc[values.index, PRODUCT_COL], sort=True):
        if len(group) >= MIN_BASELINE_ROWS:
            yield group, group.index, f"product {product}"
        else:
            leftover.append(group.index)
    if leftover and len(values) >= MIN_BASELINE_ROWS:
        yield values, leftover[0].append(leftover[1:]), "all records"


def _timestamps(unique: pd.DataFrame, out: _Collector, today: date) -> None:
    if DATE_COL not in unique:
        return
    raw = unique[DATE_COL]
    parsed = pd.to_datetime(raw, errors="coerce", format="ISO8601")
    for row_id in raw.index:
        lot = _lot(unique.loc[row_id, LOT_COL])
        value = raw.loc[row_id]
        if pd.isna(value):
            out.add("missing_timestamp", "LOW", [row_id], [lot],
                    f"Lot {lot} has no batch date, so it cannot be placed in the production "
                    "timeline.")
        elif pd.isna(parsed.loc[row_id]):
            out.add("invalid_timestamp", "MED", [row_id], [lot],
                    f"Lot {lot} has batch date '{value}', which is not a valid YYYY-MM-DD date.",
                    value=str(value), problem="unparseable")
        elif parsed.loc[row_id].date() > today:
            out.add("invalid_timestamp", "MED", [row_id], [lot],
                    f"Lot {lot} has batch date {value}, which is in the future.",
                    value=str(value), problem="future")


def run_scout(df: pd.DataFrame, spec: SpecLimits, today: date | None = None) -> ScoutResult:
    today = today or datetime.now(UTC).date()
    norm = _normalise(df, spec)
    out = _Collector()

    unique = _exact_duplicates(norm, out)
    canonical_qty = _canonical_quantity(norm, spec)
    out_of_spec = _contradictions(unique, spec, out)
    _lot_conflicts(unique, canonical_qty, spec, out)
    _outliers(unique, spec, out_of_spec, out)
    _timestamps(unique, out, today)

    return ScoutResult(findings=out.findings, rows_scanned=len(df))

