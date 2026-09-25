"""Upload validation. Everything here runs before a run is created, so bad input gets a
proper 4xx instead of a half-started stream."""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass

import pandas as pd

REQUIRED_COLUMNS = {"lot_number"}
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]")


class UploadError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Dataset:
    name: str
    sha256: str
    frame: pd.DataFrame


def safe_filename(name: str | None) -> str:
    """Display-safe version of the client-supplied name. Never passed to the LLM."""
    base = (name or "upload.csv").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE_NAME_CHARS.sub("_", base).strip() or "upload.csv"
    return cleaned[:120]


def load_csv(content: bytes, filename: str | None, max_rows: int) -> Dataset:
    name = safe_filename(filename)
    if not name.lower().endswith(".csv"):
        raise UploadError(415, "Only .csv files are accepted.")
    if not content.strip():
        raise UploadError(422, "The file is empty.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadError(422, "The file is not UTF-8 encoded text.") from exc

    try:
        # Everything is read as text so the original values are preserved exactly.
        frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False,
                            na_values=[""], skipinitialspace=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise UploadError(422, f"The file could not be parsed as CSV: {exc}") from exc

    frame.columns = [str(c).strip() for c in frame.columns]
    if len(set(frame.columns)) != len(frame.columns):
        raise UploadError(422, "The file has duplicate column names.")
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise UploadError(422, f"Missing required column(s): {', '.join(sorted(missing))}.")
    if frame.empty:
        raise UploadError(422, "The file has a header but no data rows.")
    if len(frame) > max_rows:
        raise UploadError(413, f"The file has {len(frame)} rows; the limit is {max_rows}.")

    return Dataset(name=name, sha256=hashlib.sha256(content).hexdigest(),
                   frame=frame.reset_index(drop=True))
