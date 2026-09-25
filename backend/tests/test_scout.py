from collections import Counter

import pytest

from agents.scout import run_scout
from conftest import TODAY, frame, normal_rows
from settings import SpecLimits


def types(result):
    return Counter(f.issue_type for f in result.findings)


def test_sample_detects_every_planted_defect(sample_df, spec):
    result = run_scout(sample_df, spec, today=TODAY)
    assert types(result) == {
        "exact_duplicate": 8,          # LOT001-008
        "compliance_contradiction": 6,  # LOT400, LOT401 (low side), LOT600-603
        "lot_conflict": 8,              # LOT200-202 quantity, LOT301-305 batch_date
        "unit_conflict": 5,             # LOT301-305
        "statistical_outlier": 7,
        "missing_timestamp": 7,         # LOT500-506, one finding per lot
    }
    assert result.rows_scanned == 160


def test_detection_is_deterministic(sample_df, spec):
    first = run_scout(sample_df, spec, today=TODAY)
    second = run_scout(sample_df.copy(), spec, today=TODAY)
    assert [f.model_dump() for f in first.findings] == [f.model_dump() for f in second.findings]


def test_scout_does_not_modify_input(sample_df, spec):
    before = sample_df.copy()
    run_scout(sample_df, spec, today=TODAY)
    assert sample_df.equals(before)


def test_exact_duplicates_with_missing_values_and_whitespace(spec):
    df = frame([
        "LOT1,PROD-A,,100,kg,70,4.0,INSP-01,FAC-01,PASS,",
        "LOT1 ,PROD-A,,100,kg,70,4.0,INSP-01,FAC-01,pass,",
        "LOT2,PROD-A,2026-01-02,100,kg,70,4.0,INSP-01,FAC-01,PASS,ok",
    ])
    result = run_scout(df, spec, today=TODAY)
    dups = [f for f in result.findings if f.issue_type == "exact_duplicate"]
    assert len(dups) == 1 and dups[0].row_ids == [0, 1]
    # The duplicate's missing date is reported once, on the kept copy.
    missing = [f for f in result.findings if f.issue_type == "missing_timestamp"]
    assert [f.row_ids for f in missing] == [[0]]


def test_unit_conflict_is_not_double_counted_as_quantity_problem(spec):
    df = frame([
        "LOT9,PROD-A,2026-01-01,180,kg,70,4.0,INSP-01,FAC-01,PASS,",
        "LOT9,PROD-A,2026-01-01,396,lbs,70,4.0,INSP-01,FAC-01,PASS,",
    ])
    result = run_scout(df, spec, today=TODAY)
    assert types(result) == {"unit_conflict": 1}
    assert result.findings[0].details["agree_after_conversion"] is True


def test_unit_conflict_with_disagreeing_quantities_is_also_a_lot_conflict(spec):
    df = frame([
        "LOT9,PROD-A,2026-01-01,180,kg,70,4.0,INSP-01,FAC-01,PASS,",
        "LOT9,PROD-A,2026-01-01,500,lbs,70,4.0,INSP-01,FAC-01,PASS,",
    ])
    result = run_scout(df, spec, today=TODAY)
    assert types(result) == {"unit_conflict": 1, "lot_conflict": 1}
    conflict = next(f for f in result.findings if f.issue_type == "lot_conflict")
    assert conflict.details["conflicting_fields"] == ["quantity"]


def test_lot_conflict_catches_any_differing_field(spec):
    df = frame([
        "LOT7,PROD-A,2026-01-01,100,kg,70,4.0,INSP-01,FAC-01,PASS,first",
        "LOT7,PROD-A,2026-01-01,100,kg,70,4.0,INSP-02,FAC-01,PASS,second",
    ])
    result = run_scout(df, spec, today=TODAY)
    assert types(result) == {"lot_conflict": 1}
    assert result.findings[0].details["conflicting_fields"] == ["inspector_id", "notes"]


def test_repeated_lot_differing_only_in_notes_is_still_reported(spec):
    df = frame([
        "LOT7,PROD-A,2026-01-01,100,kg,70,4.0,INSP-01,FAC-01,PASS,first entry",
        "LOT7,PROD-A,2026-01-01,100,kg,70,4.0,INSP-01,FAC-01,PASS,re-keyed",
    ])
    result = run_scout(df, spec, today=TODAY)
    assert types(result) == {"lot_conflict": 1}


def test_long_value_lists_are_summarised(spec):
    rows = [f"LOT7,PROD-A,2026-01-01,{100 + i},kg,70,4.0,INSP-01,FAC-01,PASS," for i in range(40)]
    result = run_scout(frame(rows), spec, today=TODAY)
    conflict = next(f for f in result.findings if f.issue_type == "lot_conflict")
    assert "and 35 more" in conflict.reason
    assert len(conflict.row_ids) == 40


def test_contradiction_uses_configured_spec_both_sides(spec):
    df = frame([
        "HOT,PROD-A,2026-01-01,100,kg,90,4.0,INSP-01,FAC-01,PASS,",
        "COLD,PROD-A,2026-01-01,100,kg,10,4.0,INSP-01,FAC-01,PASS,",
        "FAILED,PROD-A,2026-01-01,100,kg,90,4.0,INSP-01,FAC-01,FAIL,",
        "EDGE,PROD-A,2026-01-01,100,kg,85,4.0,INSP-01,FAC-01,PASS,",
    ])
    result = run_scout(df, spec, today=TODAY)
    contradictions = [f for f in result.findings if f.issue_type == "compliance_contradiction"]
    assert sorted(f.lot_numbers[0] for f in contradictions) == ["COLD", "HOT"]
    assert "FDA" not in " ".join(f.reason for f in contradictions)
    assert [f.details["violations"][0]["spec"] for f in contradictions] == ["20 to 85"] * 2


def test_per_product_spec_overrides_default():
    spec = SpecLimits.from_dict({
        "default": {"temperature_c": {"max": 85}},
        "products": {"PROD-HOT": {"temperature_c": {"max": 120}}},
    })
    df = frame([
        "A,PROD-A,2026-01-01,100,kg,100,4.0,INSP-01,FAC-01,PASS,",
        "B,PROD-HOT,2026-01-01,100,kg,100,4.0,INSP-01,FAC-01,PASS,",
    ])
    result = run_scout(df, spec, today=TODAY)
    flagged = [f.lot_numbers[0] for f in result.findings
               if f.issue_type == "compliance_contradiction"]
    assert flagged == ["A"]


def test_robust_outliers_are_not_masked_by_other_outliers(spec):
    # Several large excursions inflate a mean/std rule enough to hide the smaller ones.
    rows = normal_rows(40)
    for i, temp in enumerate([91, 95, 98, 102, 125, 130]):
        rows.append(f"X{i},PROD-A,2026-02-01,100,kg,{temp},4.0,INSP-01,FAC-01,FAIL,")
    result = run_scout(frame(rows), spec, today=TODAY)
    flagged = {f.lot_numbers[0] for f in result.findings
               if f.issue_type == "statistical_outlier" and f.details["column"] == "temperature_c"}
    assert flagged == {"X0", "X1", "X2", "X3", "X4", "X5"}


def test_out_of_spec_value_is_not_also_reported_as_outlier(spec):
    rows = normal_rows(30)
    rows.append("HOT,PROD-A,2026-02-01,100,kg,140,4.0,INSP-01,FAC-01,PASS,")
    result = run_scout(frame(rows), spec, today=TODAY)
    hot = [f.issue_type for f in result.findings if f.lot_numbers == ["HOT"]]
    assert hot == ["compliance_contradiction"]


def test_small_samples_are_not_scored(spec):
    rows = normal_rows(5)
    rows.append("ODD,PROD-A,2026-02-01,100,kg,60,9.9,INSP-01,FAC-01,FAIL,")
    result = run_scout(frame(rows), spec, today=TODAY)
    assert not [f for f in result.findings if f.issue_type == "statistical_outlier"]


def test_invalid_and_future_dates(spec):
    df = frame([
        "A,PROD-A,2026-13-45,100,kg,70,4.0,INSP-01,FAC-01,PASS,",
        "B,PROD-A,2031-01-01,100,kg,70,4.0,INSP-01,FAC-01,PASS,",
        "C,PROD-A,   ,100,kg,70,4.0,INSP-01,FAC-01,PASS,",
    ])
    result = run_scout(df, spec, today=TODAY)
    problems = {f.lot_numbers[0]: (f.issue_type, f.details.get("problem")) for f in result.findings}
    assert problems == {
        "A": ("invalid_timestamp", "unparseable"),
        "B": ("invalid_timestamp", "future"),
        "C": ("missing_timestamp", None),
    }


def test_optional_columns_can_be_absent_and_skips_are_reported(spec):
    df = frame(["A", "B", "A"], header="lot_number")
    result = run_scout(df, spec, today=TODAY)
    assert types(result) == {"exact_duplicate": 1}
    skipped = " ".join(result.skipped_checks)
    assert "compliance_status" in skipped and "batch_date" in skipped and "product_id" in skipped


def test_spec_parameter_missing_from_file_is_reported(spec):
    df = frame(["A,PASS"], header="lot_number,compliance_status")
    result = run_scout(df, spec, today=TODAY)
    assert any("temperature_c" in s for s in result.skipped_checks)


@pytest.mark.parametrize("row,column,problem", [
    ("A,PROD-A,2026-01-01,100,kg,hot,4.0,I,F,PASS,", "temperature_c", "not a number"),
    ("A,PROD-A,2026-01-01,100,kg,inf,4.0,I,F,PASS,", "temperature_c", "not a number"),
    ("A,PROD-A,2026-01-01,100,kg,70,4.0.1,I,F,PASS,", "pressure_bar", "not a number"),
    ("A,PROD-A,2026-01-01,abc,kg,70,4.0,I,F,PASS,", "quantity", "not a number"),
    ("A,PROD-A,2026-01-01,-5,kg,70,4.0,I,F,PASS,", "quantity", "not positive"),
    ("A,PROD-A,2026-01-01,0,kg,70,4.0,I,F,PASS,", "quantity", "not positive"),
    ("A,PROD-A,2026-01-01,100,kg,,4.0,I,F,PASS,", "temperature_c", "missing required measurement"),
    ("A,PROD-A,2026-01-01,100,kg,70,4.0,I,F,,", "compliance_status", "missing status"),
    ("A,PROD-A,2026-01-01,100,kg,70,4.0,I,F,OK,", "compliance_status", "unrecognised status"),
])
def test_invalid_values_are_never_silently_skipped(spec, row, column, problem):
    result = run_scout(frame([row]), spec, today=TODAY)
    invalid = [f for f in result.findings if f.issue_type == "invalid_value"]
    assert [(f.details["column"], f.details["problem"]) for f in invalid] == [(column, problem)]


def test_missing_measurement_on_fail_record_is_not_flagged(spec):
    result = run_scout(frame(["A,PROD-A,2026-01-01,100,kg,,4.0,I,F,FAIL,"]), spec, today=TODAY)
    assert not result.findings


def test_status_matching_ignores_case_and_whitespace(spec):
    result = run_scout(frame(["A,PROD-A,2026-01-01,100,kg,90,4.0,I,F, pass ,"]), spec, today=TODAY)
    assert types(result) == {"compliance_contradiction": 1}


def test_future_date_grace_for_time_zones(spec):
    df = frame([
        "A,PROD-A,2026-09-26,100,kg,70,4.0,I,F,PASS,",   # tomorrow in UTC: allowed
        "B,PROD-A,2026-09-28,100,kg,70,4.0,I,F,PASS,",   # clearly future
        "C,PROD-A,2026-09-25T23:00:00+09:00,100,kg,70,4.0,I,F,PASS,",
    ])
    result = run_scout(df, spec, today=TODAY)
    assert [f.lot_numbers[0] for f in result.findings] == ["B"]


def test_empty_product_rows_are_scored_against_all_records(spec):
    rows = normal_rows(30)
    rows.append("ODD,,2026-02-01,100,kg,70,9.9,INSP-01,FAC-01,FAIL,")
    result = run_scout(frame(rows), spec, today=TODAY)
    odd = [f for f in result.findings if f.lot_numbers == ["ODD"]]
    assert [f.details["baseline"] for f in odd] == ["all records"]


def test_outlier_threshold_is_configurable(spec):
    from dataclasses import replace
    rows = normal_rows(40)
    rows.append("MILD,PROD-A,2026-02-01,100,kg,73.5,4.0,INSP-01,FAC-01,FAIL,")
    strict = run_scout(frame(rows), replace(spec, outlier_z=3.0), today=TODAY)
    lenient = run_scout(frame(rows), replace(spec, outlier_z=10.0), today=TODAY)
    assert any(f.lot_numbers == ["MILD"] for f in strict.findings)
    assert not any(f.lot_numbers == ["MILD"] for f in lenient.findings)
