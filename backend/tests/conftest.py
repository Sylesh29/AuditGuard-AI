from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import HealthCheck
from hypothesis import settings as hypothesis_settings

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from ingest import load_csv  # noqa: E402
from settings import DEFAULT_SPEC_PATH, Settings, SpecLimits  # noqa: E402

# HYPOTHESIS_PROFILE=thorough runs thousands of generated datasets instead of the CI default.
_slow_ok = [HealthCheck.too_slow, HealthCheck.data_too_large]
hypothesis_settings.register_profile("ci", max_examples=150, deadline=None,
                                     suppress_health_check=_slow_ok)
hypothesis_settings.register_profile("thorough", max_examples=3000, deadline=None,
                                     suppress_health_check=_slow_ok)
hypothesis_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))

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
    """Parse rows exactly the way an upload is parsed."""
    text = "\n".join([header, *rows]) + "\n"
    return load_csv(text.encode(), "test.csv", 1_000_000).frame


def normal_rows(n: int, product: str = "PROD-A", start: int = 1) -> list[str]:
    """n unremarkable, unique records with a little natural variation."""
    rows = []
    for i in range(n):
        temp = 70 + (i % 5) * 0.5
        pressure = 4.0 + (i % 3) * 0.1
        rows.append(f"LOT{start + i:05d},{product},2026-01-{(i % 28) + 1:02d},{100 + i},kg,"
                    f"{temp},{pressure:.1f},INSP-01,FAC-01,PASS,ok")
    return rows


@pytest.fixture
def spec() -> SpecLimits:
    return SpecLimits.load(DEFAULT_SPEC_PATH)


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return load_csv(SAMPLE_CSV.read_bytes(), "sample.csv", 1_000_000).frame


@pytest.fixture
def settings() -> Settings:
    return Settings(
        anthropic_api_key=None, llm_enabled=False, llm_model="none", llm_timeout_s=5,
        llm_max_retries=0, max_upload_bytes=1_000_000, max_rows=10_000, max_stored_runs=5,
        run_ttl_s=3600, max_concurrent_runs=2, allowed_origins=[], spec_path=DEFAULT_SPEC_PATH,
        frontend_dir=None,
    )


def with_settings(base: Settings, **changes) -> Settings:
    return replace(base, **changes)
