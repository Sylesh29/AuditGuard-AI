from __future__ import annotations

import io
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from settings import DEFAULT_SPEC_PATH, SpecLimits  # noqa: E402

SAMPLE_CSV = BACKEND.parent / "data" / "sample.csv"
TODAY = date(2026, 9, 25)

HEADER = ("lot_number,product_id,batch_date,quantity,unit,temperature_c,pressure_bar,"
          "inspector_id,facility_code,compliance_status,notes")


class FakeLLM:
    """Stands in for AnthropicLLM. Returns canned objects keyed by schema name."""

    model = "fake-model"

    def __init__(self, responses: dict | None = None, enabled: bool = True) -> None:
        self.responses = responses or {}
        self.enabled = enabled
        self.calls: list[dict] = []

    async def parse(self, *, system, user, schema, max_tokens):
        self.calls.append({"system": system, "user": user, "schema": schema.__name__})
        return self.responses.get(schema.__name__)


def frame(rows: list[str], header: str = HEADER) -> pd.DataFrame:
    text = "\n".join([header, *rows]) + "\n"
    return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, na_values=[""])


def normal_rows(n: int, product: str = "PROD-A", start: int = 1) -> list[str]:
    """n unremarkable, unique records with a little natural variation."""
    rows = []
    for i in range(n):
        temp = 70 + (i % 5) * 0.5
        pressure = 4.0 + (i % 3) * 0.1
        rows.append(f"LOT{start + i:04d},{product},2026-01-{(i % 28) + 1:02d},{100 + i},kg,"
                    f"{temp},{pressure:.1f},INSP-01,FAC-01,PASS,ok")
    return rows


@pytest.fixture
def spec() -> SpecLimits:
    return SpecLimits.load(DEFAULT_SPEC_PATH)


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.read_csv(SAMPLE_CSV, dtype=str, keep_default_na=False, na_values=[""],
                       encoding="utf-8-sig")
