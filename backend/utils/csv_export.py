"""CSV exports that are safe to open in Excel (formula injection is neutralised)."""
from __future__ import annotations

import csv
import io
import re

import pandas as pd

from models import ISSUE_LABELS, ChangeLogEntry, FixerResult, RankedFinding

_FORMULA_START = re.compile(r"^[=+\-@\t\r]")
_PLAIN_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$")
_FORMULA_PATTERN = r"^[=+\-@\t\r]"
_NUMBER_PATTERN = r"^[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$"


def neutralise(value: object) -> object:
    """Prefix cells that a spreadsheet would evaluate as a formula (CSV injection)."""
    if isinstance(value, str) and _FORMULA_START.match(value) and not _PLAIN_NUMBER.match(value):
        return "'" + value
    return value


def _neutralise_column(col: pd.Series) -> pd.Series:
    if col.dtype != object:
        return col
    text = col.astype("string")
    risky = text.str.match(_FORMULA_PATTERN, na=False) & ~text.str.match(_NUMBER_PATTERN, na=False)
    if not risky.any():
        return col
    col = col.copy()
    col[risky] = "'" + col[risky]
    return col


def dataframe_to_csv(df: pd.DataFrame, headers: dict[str, str] | None = None) -> bytes:
    """Export with the customer's original header names restored."""
    safe = df.apply(_neutralise_column).rename(columns=headers or {})
    safe.columns = [neutralise(c) for c in safe.columns]
    return safe.to_csv(index=False).encode("utf-8-sig")


def _rows(header: list[str], rows) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow([neutralise(v) for v in row])
    return buf.getvalue().encode("utf-8-sig")


def spreadsheet_row(row_id: int) -> int:
    """Row number as shown in Excel: row IDs are 0-based and the header is row 1."""
    return row_id + 2


def changelog_to_csv(entries: list[ChangeLogEntry]) -> bytes:
    return _rows(
        ["spreadsheet_row", "lot_number", "field", "old_value", "new_value", "finding_id",
         "rule", "reason"],
        ((spreadsheet_row(e.row_id), e.lot_number, e.field, e.old_value, e.new_value,
          e.finding_id, e.rule, e.reason) for e in entries),
    )


def findings_to_csv(ranked: list[RankedFinding], fixer: FixerResult) -> bytes:
    actions = fixer.action_map()
    return _rows(
        ["rank", "finding_id", "issue", "severity", "lot_numbers", "spreadsheet_rows",
         "reason", "action", "action_detail", "regulatory_reference", "reviewer_suggestion"],
        ((r.rank, r.finding_id, ISSUE_LABELS[r.issue_type], r.severity, "; ".join(r.lot_numbers),
          "; ".join(str(spreadsheet_row(x)) for x in r.row_ids), r.reason,
          actions[r.finding_id].action if r.finding_id in actions else "none",
          actions[r.finding_id].description if r.finding_id in actions else "",
          r.regulatory_reference,
          f"rank {r.review_suggestion.suggested_rank}: {r.review_suggestion.reason}"
          if r.review_suggestion else "")
         for r in ranked),
    )
