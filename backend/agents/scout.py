"""Stage 1, Scout: deterministic data-integrity detectors.

Every detector is a pure function of the dataset and the process spec. Nothing here calls an
LLM, so the same file and spec always produce the same findings.

Order matters:
  1. Normalise a copy for comparison (whitespace, case). The source is never touched.
  2. Exact duplicates are found first; the other detectors run on one representative per
     duplicate set so one root cause is reported once.
  3. Units are converted to the canonical unit before quantities are compared.
  4. Values already reported as out of spec on a PASS record are not re-reported as outliers.

Detectors are vectorised; Python loops only run over rows that produce a finding, so the
cost scales with the number of problems rather than the number of rows.

A check that cannot run (for example because its column is absent) is recorded in
`ScoutResult.skipped_checks` and printed in the report. Nothing is skipped silently.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from models import Finding, ScoutResult
from regulatory import REGULATORY_REFERENCES
from settings import SpecLimits

LOT_COL = "lot_number"
PRODUCT_COL = "product_id"
DATE_COL = "batch_date"
STATUS_COL = "compliance_status"
# Process measurements scored statistically. Quantity follows order size, so it is checked by
# the lot and unit rules instead.
MEASUREMENT_COLS = ("temperature_c", "pressure_bar")

MIN_BASELINE_ROWS = 10          # fewer points than this and robust statistics are meaningless
UNIT_CONVERSION_REL_TOL = 0.01  # records within 1% after conversion describe the same quantity
FUTURE_DATE_GRACE = timedelta(days=1)  # time zones: a site ahead of UTC is not "in the future"
MAX_LISTED_VALUES = 5           # longer value lists are summarised in finding text


def _normalise(df: pd.DataFrame, spec: SpecLimits) -> pd.DataFrame:
    norm = df.copy()
    for col in norm.columns:
        if norm[col].dtype == object:
            stripped = norm[col].str.strip()
            norm[col] = stripped.mask(stripped == "")
    if STATUS_COL in norm:
        norm[STATUS_COL] = norm[STATUS_COL].str.upper()
    if spec.units.column in norm:
        norm[spec.units.column] = norm[spec.units.column].str.lower()
    return norm


def _to_number(series: pd.Series) -> pd.Series:
    numbers = pd.to_numeric(series, errors="coerce")
    return numbers.where(np.isfinite(numbers))


def _lot(value: Any) -> str:
    return str(value) if pd.notna(value) else "(missing lot number)"


def _listing(values: list) -> str:
    shown = ", ".join("(empty)" if v is None else str(v) for v in values[:MAX_LISTED_VALUES])
    extra = len(values) - MAX_LISTED_VALUES
    return f"{shown} and {extra} more" if extra > 0 else shown


class _Collector:
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.skipped: list[str] = []

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

    def skip(self, message: str) -> None:
        self.skipped.append(message)


# --- exact duplicates -------------------------------------------------------------------

def _exact_duplicates(norm: pd.DataFrame, out: _Collector) -> pd.DataFrame:
    """Report each duplicate set once and return the frame with one row per set."""
    row_hash = pd.util.hash_pandas_object(norm, index=False)
    duplicated = row_hash[row_hash.duplicated(keep=False)]
    lots = norm[LOT_COL]
    for positions in duplicated.groupby(duplicated, sort=False).indices.values():
        rows = sorted(int(r) for r in duplicated.index[positions])
        lot_list = sorted({_lot(v) for v in lots.loc[rows]})
        out.add(
            "exact_duplicate", "HIGH", rows, lot_list,
            f"Lot {', '.join(lot_list)} is recorded {len(rows)} times with identical values in "
            "every column. Only one of these records can be the original.",
            copies=len(rows), kept_row=rows[0],
        )
    return norm[~row_hash.duplicated(keep="first")]


# --- spec limits, required values and status ------------------------------------------

def _limit_bounds(unique: pd.DataFrame, spec: SpecLimits, param: str) -> tuple[pd.Series, pd.Series]:
    """Per-row min and max for `param`, applying product overrides. NaN means no bound."""
    default = spec.default.get(param)
    lo = pd.Series(np.nan if default is None or default.min is None else default.min,
                   index=unique.index, dtype=float)
    hi = pd.Series(np.nan if default is None or default.max is None else default.max,
                   index=unique.index, dtype=float)
    if PRODUCT_COL in unique:
        for product, limits in spec.products.items():
            if param in limits:
                mask = unique[PRODUCT_COL] == product
                lo[mask] = np.nan if limits[param].min is None else limits[param].min
                hi[mask] = np.nan if limits[param].max is None else limits[param].max
    return lo, hi


def _values_and_status(unique: pd.DataFrame, spec: SpecLimits, out: _Collector
                       ) -> dict[str, pd.Series]:
    """Unreadable numbers, missing required measurements and unknown statuses.
    Returns the parsed numeric columns for the detectors that follow."""
    qcol = spec.units.quantity_column
    numeric: dict[str, pd.Series] = {}
    for col in dict.fromkeys((*MEASUREMENT_COLS, *spec.parameters, qcol)):
        if col not in unique:
            continue
        raw = unique[col]
        numbers = _to_number(raw)
        numeric[col] = numbers
        for row_id in raw.index[raw.notna() & numbers.isna()]:
            lot = _lot(unique.at[row_id, LOT_COL])
            out.add("invalid_value", "MED", [row_id], [lot],
                    f"Lot {lot}: {col} is '{raw.at[row_id]}', which is not a number.",
                    column=col, value=str(raw.at[row_id]), problem="not a number")
        if col == qcol:
            for row_id in raw.index[numbers <= 0]:
                lot = _lot(unique.at[row_id, LOT_COL])
                out.add("invalid_value", "MED", [row_id], [lot],
                        f"Lot {lot}: {col} is {numbers.at[row_id]:g}, but a batch quantity "
                        "must be greater than zero.",
                        column=col, value=str(raw.at[row_id]), problem="not positive")

    if STATUS_COL not in unique:
        out.skip(f"No '{STATUS_COL}' column: status contradictions were not checked.")
        return numeric

    status = unique[STATUS_COL]
    passed = status.isin(spec.pass_statuses)
    for param in spec.parameters:
        if param not in unique:
            out.skip(f"The process spec defines limits for '{param}', but the file has no such "
                     "column: those limits were not checked.")
            continue
        lo, hi = _limit_bounds(unique, spec, param)
        required = lo.notna() | hi.notna()
        for row_id in unique.index[passed & required & unique[param].isna()]:
            lot = _lot(unique.at[row_id, LOT_COL])
            out.add("invalid_value", "MED", [row_id], [lot],
                    f"Lot {lot} is marked {status.at[row_id]} but has no {param} value, so it "
                    "cannot show that the acceptance criteria were met.",
                    column=param, value=None, problem="missing required measurement")

    known = sorted(spec.known_statuses)
    for row_id in unique.index[status.isna()]:
        lot = _lot(unique.at[row_id, LOT_COL])
        out.add("invalid_value", "MED", [row_id], [lot],
                f"Lot {lot} has no compliance status.",
                column=STATUS_COL, value=None, problem="missing status")
    for row_id in unique.index[status.notna() & ~status.isin(spec.known_statuses)]:
        lot = _lot(unique.at[row_id, LOT_COL])
        out.add("invalid_value", "MED", [row_id], [lot],
                f"Lot {lot} has status '{status.at[row_id]}', which is not one of the "
                f"configured statuses ({', '.join(known)}).",
                column=STATUS_COL, value=str(status.at[row_id]), problem="unrecognised status")
    return numeric


def _contradictions(unique: pd.DataFrame, numeric: dict[str, pd.Series], spec: SpecLimits,
                    out: _Collector) -> set[tuple[int, str]]:
    """PASS records with a measured parameter outside its spec. Returns (row, column) pairs."""
    if STATUS_COL not in unique:
        return set()
    passed = unique[STATUS_COL].isin(spec.pass_statuses)
    violations: dict[int, list[dict]] = {}
    for param in spec.parameters:
        if param not in numeric:
            continue
        values = numeric[param]
        lo, hi = _limit_bounds(unique, spec, param)
        breached = passed & values.notna() & ((values < lo) | (values > hi))
        for row_id in unique.index[breached]:
            low, high = lo.at[row_id], hi.at[row_id]
            limit_text = (f"{low:g} to {high:g}" if pd.notna(low) and pd.notna(high)
                          else f"at most {high:g}" if pd.notna(high) else f"at least {low:g}")
            violations.setdefault(int(row_id), []).append({
                "parameter": param, "value": float(values.at[row_id]), "spec": limit_text,
                "min": None if pd.isna(low) else float(low),
                "max": None if pd.isna(high) else float(high),
            })

    products = unique.get(PRODUCT_COL)
    for row_id in sorted(violations):
        found = violations[row_id]
        lot = _lot(unique.at[row_id, LOT_COL])
        status = unique.at[row_id, STATUS_COL]
        product = None if products is None else products.at[row_id]
        described = "; ".join(f"{v['parameter']} = {v['value']:g} (spec {v['spec']})" for v in found)
        out.add(
            "compliance_contradiction", "HIGH", [row_id], [lot],
            f"Lot {lot} is marked {status} but {described} is outside the process "
            "specification. The record contradicts itself.",
            status=status, product_id=None if pd.isna(product) else str(product),
            violations=found,
        )
    return {(row_id, v["parameter"]) for row_id, found in violations.items() for v in found}


# --- lot uniqueness and units -----------------------------------------------------------

def _canonical_quantity(norm: pd.DataFrame, numeric: dict[str, pd.Series], spec: SpecLimits
                        ) -> pd.Series | None:
    qcol, ucol = spec.units.quantity_column, spec.units.column
    if qcol not in norm:
        return None
    quantity = numeric.get(qcol, _to_number(norm[qcol]))
    if ucol not in norm:
        return quantity
    return quantity * norm[ucol].map(spec.units.factor).astype(float)


def _lot_conflicts(unique: pd.DataFrame, canonical_qty: pd.Series | None, spec: SpecLimits,
                   out: _Collector) -> None:
    """A lot number must identify exactly one record. Characterise what disagrees."""
    ucol, qcol = spec.units.column, spec.units.quantity_column
    lots = unique[unique[LOT_COL].notna()]
    repeated = lots[lots[LOT_COL].duplicated(keep=False)]
    if repeated.empty:
        return

    compared = [c for c in repeated.columns if c not in {ucol, qcol, LOT_COL}]
    grouped = repeated.groupby(LOT_COL, sort=False)
    differing = grouped[compared].nunique(dropna=False) > 1 if compared else None
    has_units = ucol in repeated
    has_qty = qcol in repeated
    canon = canonical_qty.loc[repeated.index] if canonical_qty is not None and has_qty else None

    for lot, positions in grouped.indices.items():
        group = repeated.iloc[positions]
        rows = sorted(int(r) for r in group.index)
        conflicting = list(differing.columns[differing.loc[lot]]) if differing is not None else []
        units = sorted(group[ucol].dropna().unique()) if has_units else []

        agree_after_conversion = None
        if has_qty:
            group_canon = canon.loc[group.index] if canon is not None else None
            if len(units) > 1 and group_canon is not None and group_canon.notna().all():
                # Published conversion factors are often rounded (2.2 lb/kg), hence a tolerance.
                spread = float(group_canon.max() - group_canon.min())
                agree_after_conversion = spread <= UNIT_CONVERSION_REL_TOL * float(group_canon.abs().max())
                if not agree_after_conversion:
                    conflicting.append(qcol)
            elif group[qcol].nunique(dropna=False) > 1:
                conflicting.append(qcol)

        if len(units) > 1:
            quantities = group[qcol].tolist() if has_qty else []
            convertible = all(spec.units.factor(u) is not None for u in units)
            if not convertible:
                note = " At least one unit has no known conversion."
            elif agree_after_conversion:
                note = f" Converted to {spec.units.canonical}, the quantities agree."
            else:
                note = f" Converted to {spec.units.canonical}, the quantities still disagree."
            out.add(
                "unit_conflict", "MED", rows, [str(lot)],
                f"Lot {lot} is recorded in {' and '.join(units)} "
                f"(quantities {_listing(quantities)}).{note}",
                units_found=units, quantities=quantities, convertible=convertible,
                agree_after_conversion=agree_after_conversion,
            )

        if conflicting:
            values = {c: list(dict.fromkeys(None if pd.isna(v) else v for v in group[c]))
                      for c in conflicting}
            shown = "; ".join(f"{c}: {_listing(vals)}" for c, vals in values.items())
            out.add(
                "lot_conflict", "HIGH", rows, [str(lot)],
                f"Lot {lot} has {len(rows)} different records that disagree on {shown}. "
                "Only one version can be correct.",
                conflicting_fields=conflicting, values=values,
            )


# --- statistical outliers ---------------------------------------------------------------

def _robust_scores(values: pd.Series, groups: pd.Series | None) -> pd.DataFrame:
    """Modified z-score 0.6745 * (x - median) / MAD per row, using the row's product as the
    baseline when that product has enough history and all records otherwise. When MAD is
    zero (over half the values identical) the mean absolute deviation is used instead."""

    def stats(key: pd.Series | None) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        if key is None:
            median = pd.Series(values.median(), index=values.index)
            deviation = (values - median).abs()
            return (median, pd.Series(deviation.median(), index=values.index),
                    pd.Series(deviation.mean(), index=values.index),
                    pd.Series(float(len(values)), index=values.index))
        by = values.groupby(key)
        median = by.transform("median")
        deviation = (values - median).abs().groupby(key)
        return (median, deviation.transform("median"), deviation.transform("mean"),
                by.transform("size").astype(float))

    median, mad, mean_ad, size = stats(None)
    label = pd.Series("all records", index=values.index)
    if groups is not None:
        key = groups.loc[values.index]
        g_median, g_mad, g_mean_ad, g_size = stats(key)
        own = g_size >= MIN_BASELINE_ROWS
        median, mad, mean_ad, size = (median.where(~own, g_median), mad.where(~own, g_mad),
                                      mean_ad.where(~own, g_mean_ad), size.where(~own, g_size))
        label = label.where(~own, "product " + key.astype(str))

    centred = values - median
    z = pd.Series(np.nan, index=values.index)
    use_mad = mad > 0
    z[use_mad] = 0.6745 * centred[use_mad] / mad[use_mad]
    use_mean = ~use_mad & (mean_ad > 0)
    z[use_mean] = centred[use_mean] / (1.253314 * mean_ad[use_mean])
    z[size < MIN_BASELINE_ROWS] = np.nan
    return pd.DataFrame({"z": z, "median": median, "spread": mad.where(use_mad, mean_ad),
                         "label": label})


def _outliers(unique: pd.DataFrame, numeric: dict[str, pd.Series], spec: SpecLimits,
              already_reported: set[tuple[int, str]], out: _Collector) -> None:
    groups = unique.get(PRODUCT_COL)
    for col in dict.fromkeys((*MEASUREMENT_COLS, *spec.parameters)):
        if col not in numeric:
            continue
        values = numeric[col].dropna()
        if len(values) < MIN_BASELINE_ROWS:
            out.skip(f"'{col}' has fewer than {MIN_BASELINE_ROWS} readable values: "
                     "statistical outliers were not checked.")
            continue
        scores = _robust_scores(values, groups)
        # Default 3.5 (Iglewicz & Hoaglin). On normally distributed data this flags about 5 in
        # 10,000 readings per column; sites tune it in the process spec.
        for row_id in scores.index[scores["z"].abs() > spec.outlier_z]:
            if (int(row_id), col) in already_reported:
                continue
            score = scores.loc[row_id]
            lot = _lot(unique.at[row_id, LOT_COL])
            value = float(values.at[row_id])
            direction = "above" if score["z"] > 0 else "below"
            out.add(
                "statistical_outlier", "MED", [row_id], [lot],
                f"Lot {lot}: {col} = {value:g} is far {direction} the normal range for "
                f"{score['label']} (typical value {score['median']:g}).",
                column=col, value=value, baseline=score["label"],
                median=round(float(score["median"]), 4), spread=round(float(score["spread"]), 4),
                robust_z=round(float(score["z"]), 1),
            )


# --- batch dates ------------------------------------------------------------------------

def _timestamps(unique: pd.DataFrame, out: _Collector, today: date) -> None:
    if DATE_COL not in unique:
        out.skip(f"No '{DATE_COL}' column: batch dates were not checked.")
        return
    raw = unique[DATE_COL]
    # utc=True lets offsets and naive timestamps coexist; only the calendar date is used.
    parsed = pd.to_datetime(raw, errors="coerce", format="ISO8601", utc=True)
    latest_allowed = pd.Timestamp(today + FUTURE_DATE_GRACE, tz="UTC") + pd.Timedelta(days=1)

    missing = raw.isna()
    unparseable = raw.notna() & parsed.isna()
    future = parsed >= latest_allowed
    for row_id in unique.index[missing | unparseable | future]:
        lot = _lot(unique.at[row_id, LOT_COL])
        value = raw.at[row_id]
        if missing.at[row_id]:
            out.add("missing_timestamp", "LOW", [row_id], [lot],
                    f"Lot {lot} has no batch date, so it cannot be placed in the production "
                    "timeline.")
        elif unparseable.at[row_id]:
            out.add("invalid_timestamp", "MED", [row_id], [lot],
                    f"Lot {lot} has batch date '{value}', which is not a valid YYYY-MM-DD date.",
                    value=str(value), problem="unparseable")
        else:
            out.add("invalid_timestamp", "MED", [row_id], [lot],
                    f"Lot {lot} has batch date {value}, which is in the future.",
                    value=str(value), problem="future")


def run_scout(df: pd.DataFrame, spec: SpecLimits, today: date | None = None) -> ScoutResult:
    today = today or datetime.now(UTC).date()
    norm = _normalise(df, spec)
    out = _Collector()

    unique = _exact_duplicates(norm, out)
    numeric = _values_and_status(unique, spec, out)
    out_of_spec = _contradictions(unique, numeric, spec, out)
    _lot_conflicts(unique, _canonical_quantity(unique, numeric, spec), spec, out)
    _outliers(unique, numeric, spec, out_of_spec, out)
    _timestamps(unique, out, today)
    if PRODUCT_COL not in unique:
        out.skip(f"No '{PRODUCT_COL}' column: outliers use one baseline for all records and "
                 "per-product spec limits do not apply.")

    return ScoutResult(findings=out.findings, rows_scanned=len(df), skipped_checks=out.skipped)
