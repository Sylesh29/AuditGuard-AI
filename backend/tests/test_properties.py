"""Property-based tests: invariants that must hold for any input, not just the sample."""
import csv
import io
from datetime import UTC, datetime

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from agents.fixer import FLAG_SEPARATOR, run_fixer
from agents.narrator import build_report
from agents.ranker import rank_findings
from agents.scout import _normalise, run_scout
from conftest import HEADER, TODAY
from ingest import UploadError, load_csv
from settings import DEFAULT_SPEC_PATH, SpecLimits
from utils.csv_export import dataframe_to_csv, findings_to_csv
from utils.pdf_gen import render_pdf

SPEC = SpecLimits.load(DEFAULT_SPEC_PATH)
COLUMNS = HEADER.split(",")

messy_number = st.one_of(
    st.floats(min_value=-50, max_value=200, allow_nan=False).map(lambda x: f"{x:.1f}"),
    st.sampled_from(["", " ", "70", "70.0", "abc", "inf", "-0", "1e2", " 71 "]),
)
cell = {
    "lot_number": st.sampled_from(["L1", "L2", "L3", " L1", "L4", "", "=cmd"]),
    "product_id": st.sampled_from(["PROD-A", "PROD-B", "", "prod-a"]),
    "batch_date": st.sampled_from(["2026-01-01", "2026-01-02", "", "2026-13-01", "2031-01-01",
                                   "01/02/2026", "2026-01-01T10:00:00+02:00"]),
    "quantity": st.one_of(st.integers(-5, 500).map(str), st.sampled_from(["", "x", "100.5"])),
    "unit": st.sampled_from(["kg", "lbs", "KG", "oz", "", " kg "]),
    "temperature_c": messy_number,
    "pressure_bar": messy_number,
    "inspector_id": st.sampled_from(["I1", "I2"]),
    "facility_code": st.sampled_from(["F1"]),
    "compliance_status": st.sampled_from(["PASS", "FAIL", "pass", " PASS", "OK", ""]),
    "notes": st.sampled_from(["", "ok", "re-keyed", 'has "quotes", commas']),
}
rows = st.lists(st.fixed_dictionaries(cell), min_size=1, max_size=40)


def to_frame(records: list[dict]) -> pd.DataFrame | None:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(records)
    try:
        return load_csv(buf.getvalue().encode(), "p.csv", 10_000).frame
    except UploadError:
        return None  # e.g. every row blank; rejection is the correct outcome


@given(rows)
def test_scout_invariants(records):
    df = to_frame(records)
    if df is None:
        return
    before = df.copy()
    result = run_scout(df, SPEC, today=TODAY)
    again = run_scout(df, SPEC, today=TODAY)

    assert df.equals(before)
    assert [f.model_dump() for f in result.findings] == [f.model_dump() for f in again.findings]
    ids = [f.finding_id for f in result.findings]
    assert ids == [f"F{i:03d}" for i in range(1, len(ids) + 1)]
    for f in result.findings:
        assert f.row_ids and set(f.row_ids) <= set(df.index)
        assert f.reason and f.regulatory_reference


@given(rows)
def test_fixer_invariants(records):
    df = to_frame(records)
    if df is None:
        return
    before = df.copy()
    ranked = rank_findings(run_scout(df, SPEC, today=TODAY).findings)
    corrected, result = run_fixer(df, ranked, SPEC)

    # The source is never modified.
    assert df.equals(before)
    # Exactly one action per finding, in rank order.
    assert [a.finding_id for a in result.actions] == [r.finding_id for r in ranked]
    # Only exact duplicates of a kept row are removed, and each removal is logged.
    removed = set(df.index) - set(corrected.index)
    logged = {c.row_id for c in result.change_log if c.rule == "remove_exact_duplicate"}
    assert removed == logged
    norm = _normalise(df, SPEC)
    kept = {tuple(norm.loc[i].fillna("\0")) for i in corrected.index}
    assert all(tuple(norm.loc[i].fillna("\0")) in kept for i in removed)
    # Field changes record the true old value.
    for change in result.change_log:
        if change.field != "(entire row)":
            original = df.at[change.row_id, change.field]
            assert change.old_value == (None if pd.isna(original) else original)
    # Every finding's flag is on a surviving row, and flags are ordered by rank.
    for flag in result.flags:
        assert flag.row_id in corrected.index
        assert flag.text in corrected.at[flag.row_id, "audit_flags"]
    for row_id in corrected.index:
        texts = [t for t in corrected.at[row_id, "audit_flags"].split(FLAG_SEPARATOR) if t]
        ranks = [next(fl.rank for fl in result.flags if fl.text == t and fl.row_id == row_id)
                 for t in texts]
        assert ranks == sorted(ranks)


@settings(max_examples=25)
@given(rows)
def test_report_and_exports_never_lose_findings(records):
    df = to_frame(records)
    if df is None:
        return
    scout = run_scout(df, SPEC, today=TODAY)
    ranked = rank_findings(scout.findings)
    corrected, fixer = run_fixer(df, ranked, SPEC)
    report = build_report(
        reference_id="R", dataset_name="p.csv", source_sha256="0", generated_at=datetime.now(UTC),
        scout=scout, ranked=ranked, fixer=fixer, review_status="disabled", summary="s",
        summary_source="template", configuration={})
    assert [f["finding_id"] for f in report.findings] == [r.finding_id for r in ranked]
    assert render_pdf(report).startswith(b"%PDF")

    exported = pd.read_csv(io.BytesIO(findings_to_csv(ranked, fixer)), encoding="utf-8-sig",
                           dtype=str, keep_default_na=False)
    assert exported["finding_id"].tolist() == [r.finding_id for r in ranked]
    back = pd.read_csv(io.BytesIO(dataframe_to_csv(corrected)), encoding="utf-8-sig", dtype=str,
                       keep_default_na=False)
    assert len(back) == len(corrected)
    assert not back.map(lambda v: str(v).startswith(("=", "+", "@"))).any().any()


@given(st.binary(max_size=400), st.sampled_from(["a.csv", "b.txt", "c.xlsx"]))
def test_ingest_never_crashes_on_arbitrary_bytes(content, name):
    try:
        load_csv(content, name, 1000)
    except UploadError as exc:
        assert exc.status_code in {413, 415, 422}
