import asyncio
import io
from datetime import UTC, datetime

import pandas as pd
import pytest
from pypdf import PdfReader

from agents.fixer import run_fixer
from agents.narrator import build_report, report_stats, summary_payload, validate_summary, write_summary
from agents.ranker import rank_findings
from agents.scout import run_scout
from conftest import TODAY, FakeLLM, frame
from models import ExecutiveSummary, ReviewSuggestion
from utils.csv_export import changelog_to_csv, dataframe_to_csv, findings_to_csv, neutralise
from utils.pdf_gen import PDF_MAX_FINDINGS, render_pdf

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
CONFIG = {"AuditGuard version": "test", "Process spec SHA-256": "abc"}


def audit(df, spec, llm=None):
    scout = run_scout(df, spec, today=TODAY)
    ranked = rank_findings(scout.findings)
    _, fixer = run_fixer(df, ranked, spec)
    summary, source = asyncio.run(
        write_summary(report_stats(scout, fixer), ranked, llm or FakeLLM(enabled=False), NOW))
    report = build_report(
        reference_id="AUDIT-TEST", dataset_name="sample.csv", source_sha256="0" * 64,
        generated_at=NOW, scout=scout, ranked=ranked, fixer=fixer, review_status="disabled",
        summary=summary, summary_source=source, configuration=CONFIG)
    return report, ranked, fixer


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def test_pdf_lists_every_finding_once_signed(sample_df, spec):
    report, _, _ = audit(sample_df, spec)
    text = pdf_text(render_pdf(report))
    assert len(report.findings) == 41
    for finding in report.findings:
        assert finding["finding_id"] in text
    assert text.count("Signed:") == 1
    assert "Process spec SHA-256" in text and "fixed template" in text


def test_clean_file_produces_a_clean_report(spec):
    report, _, _ = audit(frame(["A,PROD-A,2026-01-01,100,kg,70,4.0,I,F,PASS,"]), spec)
    assert report.stats["total"] == 0
    text = pdf_text(render_pdf(report))
    assert "found no data-integrity issues" in text and "No findings." in text


def test_large_reports_point_to_findings_csv(spec):
    rows = [f"L{i},PROD-A,,100,kg,70,4.0,I,F,PASS," for i in range(PDF_MAX_FINDINGS + 5)]
    report, ranked, fixer = audit(frame(rows), spec)
    text = pdf_text(render_pdf(report))
    assert f"{PDF_MAX_FINDINGS} highest-ranked of {PDF_MAX_FINDINGS + 5}" in text
    exported = pd.read_csv(io.BytesIO(findings_to_csv(ranked, fixer)), encoding="utf-8-sig")
    assert len(exported) == PDF_MAX_FINDINGS + 5


def test_review_suggestions_and_notes_are_printed(sample_df, spec):
    report, ranked, _ = audit(sample_df, spec)
    report.review_suggestions = [{"finding_id": "F009", "rank": 1, "suggested_rank": 2,
                                  "reason": "Second opinion."}]
    report.notes = ["The file was decoded as Windows-1252."]
    text = pdf_text(render_pdf(report))
    assert "Advisory Ranking Suggestions" in text and "Second opinion." in text
    assert "Windows-1252" in text


def test_pdf_escapes_markup_and_wraps_long_tokens(spec):
    rows = ["<b>R&D</b><para>,PROD-A,,100,kg,70,4.0,I,F,PASS,",
            f"{'X' * 300},PROD-A,,100,kg,70,4.0,I,F,PASS,"]
    report, _, _ = audit(frame(rows), spec)
    text = pdf_text(render_pdf(report))
    assert "R&D" in text


# --- LLM summary validation -----------------------------------------------------------

def test_faithful_llm_summary_is_used(sample_df, spec):
    summary = ("AuditGuard AI reviewed 160 records and found 41 issues, 22 of them critical. "
               "The most serious is F009: lot LOT400 was marked PASS at 112.4 degrees.")
    llm = FakeLLM({"ExecutiveSummary": ExecutiveSummary(summary=summary)})
    report, _, _ = audit(sample_df, spec, llm)
    assert report.summary_source == "llm"
    assert report.executive_summary == summary
    assert "<audit_data>" in llm.calls[0]["user"]


@pytest.mark.parametrize("bad", [
    "AuditGuard AI found 41 issues, including F777.",           # invented finding ID
    "AuditGuard AI found 97 issues across the dataset.",        # invented count
    "AuditGuard AI found 41 issues; LOT400 read 113.9 degrees.",  # invented value
    "The dataset contains 22 critical issues.",                 # total never stated
    "41 records reviewed; no issues were found.",               # all-clear claim
    "",
    "41 " + "x" * 2000,                                         # too long
])
def test_unfaithful_llm_summary_falls_back_to_template(sample_df, spec, bad):
    report, _, _ = audit(sample_df, spec, FakeLLM({"ExecutiveSummary": ExecutiveSummary(summary=bad)}))
    assert report.summary_source == "template"
    assert "41 data-integrity issues" in report.executive_summary


def test_llm_outage_falls_back_to_template(sample_df, spec):
    report, _, _ = audit(sample_df, spec, FakeLLM())
    assert report.summary_source == "template"


def test_validation_units():
    payload = '{"total": 3, "value": 1388, "lot": "LOT400"}'
    ok = dict(source_data=payload, allowed_ids={"F001"}, total=3)
    assert validate_summary("F001 leads the 3 findings; 1,388 rows.", **ok)
    assert not validate_summary("F001 leads the 3 findings; 400 rows.", **ok)
    assert validate_summary("Nothing to report.", source_data="{}", allowed_ids=set(), total=0)


def test_summary_payload_contains_no_filename_and_is_bounded(sample_df, spec):
    scout = run_scout(sample_df, spec, today=TODAY)
    ranked = rank_findings(scout.findings)
    _, fixer = run_fixer(sample_df, ranked, spec)
    payload = summary_payload(report_stats(scout, fixer), ranked, NOW)
    assert "sample.csv" not in payload
    assert payload.count('"finding_id"') == 10


def test_suggestion_type_round_trips():
    s = ReviewSuggestion(finding_id="F001", suggested_rank=2, reason="x")
    assert ReviewSuggestion.model_validate(s.model_dump()) == s


# --- CSV exports ------------------------------------------------------------------------

def test_formula_injection_is_neutralised():
    assert neutralise('=HYPERLINK("x")') == "'=HYPERLINK(\"x\")"
    assert neutralise("@SUM(A1)") == "'@SUM(A1)"
    assert neutralise("-3.5") == "-3.5"
    assert neutralise("LOT001") == "LOT001"
    df = pd.DataFrame({"a": ["+cmd", "-2", "ok", None], "=evil": ["x", "y", "z", "w"]})
    out = dataframe_to_csv(df).decode("utf-8-sig").splitlines()
    assert out == ["a,'=evil", "'+cmd,x", "-2,y", "ok,z", ",w"]


def test_exports_restore_original_headers(sample_df, spec):
    out = dataframe_to_csv(sample_df.head(1), {"lot_number": "Lot Number"})
    assert out.decode("utf-8-sig").startswith("Lot Number,")


def test_changelog_uses_spreadsheet_rows(sample_df, spec):
    scout = run_scout(sample_df, spec, today=TODAY)
    ranked = rank_findings(scout.findings)
    _, fixer = run_fixer(sample_df, ranked, spec)
    log = pd.read_csv(io.BytesIO(changelog_to_csv(fixer.change_log)), encoding="utf-8-sig")
    first = fixer.change_log[0]
    assert log.at[0, "spreadsheet_row"] == first.row_id + 2
