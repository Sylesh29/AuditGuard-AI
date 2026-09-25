"""Upload validation and parsing. Everything here runs before a run is created, so bad input
gets a proper 4xx instead of a half-started stream.

Real exports are messy, so parsing accepts what spreadsheet tools actually produce:
  * UTF-8 (with or without BOM), falling back to Windows-1252 (Excel "CSV" on Windows).
  * Comma, semicolon (European Excel), tab or pipe delimiters.
  * Headers like "Lot Number" or "LOT-NUMBER", matched to lot_number. Original header names
    are kept and restored in every export.
Row IDs are positions in the file's record sequence, blank lines included, so
"spreadsheet row" (row ID + 2) matches the row number a user sees in Excel.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field

import pandas as pd

REQUIRED_COLUMNS = {"lot_number"}
DELIMITERS = ",;\t|"
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]")
_HEADER_SEPARATORS = re.compile(r"[\s\-./]+")


class UploadError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Dataset:
    name: str
    sha256: str
    frame: pd.DataFrame
    original_columns: dict[str, str]
    encoding: str
    delimiter: str
    notes: list[str] = field(default_factory=list)


def safe_filename(name: str | None) -> str:
    """Display-safe version of the client-supplied name. Never passed to the LLM."""
    base = (name or "upload.csv").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE_NAME_CHARS.sub("_", base).strip() or "upload.csv"
    return cleaned[:120]


def canonical_header(name: object) -> str:
    return _HEADER_SEPARATORS.sub("_", str(name).strip().lower()).strip("_")


def _decode(content: bytes) -> tuple[str, str]:
    try:
        return content.decode("utf-8-sig"), "UTF-8"
    except UnicodeDecodeError:
        pass
    if b"\x00" in content[:4096]:
        raise UploadError(422, "The file looks like UTF-16 or binary data. Save it as "
                               "'CSV UTF-8' and upload again.")
    try:
        return content.decode("cp1252"), "Windows-1252"
    except UnicodeDecodeError as exc:
        raise UploadError(422, "The file's text encoding is not supported. Save it as "
                               "'CSV UTF-8' and upload again.") from exc


def _delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:50])
    try:
        return csv.Sniffer().sniff(sample, delimiters=DELIMITERS).delimiter
    except csv.Error:
        header = text.splitlines()[0] if text else ""
        return max(DELIMITERS, key=header.count) if any(d in header for d in DELIMITERS) else ","


def load_csv(content: bytes, filename: str | None, max_rows: int) -> Dataset:
    name = safe_filename(filename)
    if name.lower().endswith((".xlsx", ".xls", ".xlsm")):
        raise UploadError(415, "Excel workbooks are not accepted. Use File > Save As > "
                               "'CSV UTF-8' and upload the .csv file.")
    if not name.lower().endswith((".csv", ".txt")):
        raise UploadError(415, "Only .csv files are accepted.")
    if not content.strip():
        raise UploadError(422, "The file is empty.")

    text, encoding = _decode(content)
    delimiter = _delimiter(text)
    try:
        # Everything is read as text so the original values are preserved exactly.
        frame = pd.read_csv(io.StringIO(text), sep=delimiter, dtype=str, keep_default_na=False,
                            na_values=[""], skip_blank_lines=False)
        # pandas renames repeated headers ("x", "x.1"), so validate the header row as written.
        originals = next(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter), [])
    except (ValueError, csv.Error) as exc:  # pandas' ParserError subclasses ValueError
        raise UploadError(422, f"The file could not be parsed as CSV: {exc}") from exc
    if len(originals) != len(frame.columns):
        raise UploadError(422, "The header row does not match the data columns.")
    canonical = [canonical_header(c) for c in originals]
    if any(c.startswith("unnamed") or not c for c in canonical):
        raise UploadError(422, "Every column needs a header name.")
    if len(set(canonical)) != len(canonical):
        raise UploadError(422, "The file has duplicate column names (after ignoring case and "
                               "spacing).")
    frame.columns = canonical
    missing = REQUIRED_COLUMNS - set(canonical)
    if missing:
        raise UploadError(422, f"Missing required column(s): {', '.join(sorted(missing))}. "
                               f"Found: {', '.join(originals[:20])}.")

    blank = frame.isna().all(axis=1)
    frame = frame[~blank]  # keep the original index so row numbers still match the file
    if frame.empty:
        raise UploadError(422, "The file has a header but no data rows.")
    if len(frame) > max_rows:
        raise UploadError(413, f"The file has {len(frame)} rows; the limit is {max_rows}.")

    notes = []
    if encoding != "UTF-8":
        notes.append(f"The file was decoded as {encoding}.")
    if delimiter != ",":
        notes.append(f"The file uses {'tab' if delimiter == chr(9) else repr(delimiter)} "
                     "as its delimiter.")
    if blank.any():
        notes.append(f"{int(blank.sum())} blank line(s) were ignored.")

    return Dataset(name=name, sha256=hashlib.sha256(content).hexdigest(), frame=frame,
                   original_columns=dict(zip(canonical, originals, strict=True)),
                   encoding=encoding, delimiter=delimiter, notes=notes)
