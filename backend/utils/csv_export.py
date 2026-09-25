"""CSV exports that are safe to open in Excel."""
from __future__ import annotations

import csv
import io
import re

import pandas as pd

from models import ChangeLogEntry

_FORMULA_START = re.compile(r"^[=+\-@\t\r]")
_PLAIN_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$")


def neutralise(value: object) -> object:
    """Prefix cells that a spreadsheet would evaluate as a formula (CSV injection)."""
    if isinstance(value, str) and _FORMULA_START.match(value) and not _PLAIN_NUMBER.match(value):
        return "'" + value
    return value


def dataframe_to_csv(df: pd.DataFrame) -> bytes:
    safe = df.apply(lambda col: col.map(neutralise))
    return safe.to_csv(index=False).encode("utf-8-sig")


CHANGELOG_COLUMNS = ["csv_line", "row_id", "lot_number", "field", "old_value", "new_value",
                     "finding_id", "rule", "reason"]


def changelog_to_csv(entries: list[ChangeLogEntry]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CHANGELOG_COLUMNS)
    for e in entries:
        # csv_line is the line number in the uploaded file (header is line 1).
        writer.writerow([neutralise(v) for v in (
            e.row_id + 2, e.row_id, e.lot_number, e.field, e.old_value, e.new_value,
            e.finding_id, e.rule, e.reason)])
    return buf.getvalue().encode("utf-8-sig")
