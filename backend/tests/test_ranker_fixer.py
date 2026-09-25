import asyncio
import random

from agents.fixer import FLAG_COLUMN, run_fixer
from agents.ranker import rank_findings, review_ranking
from agents.scout import run_scout
from conftest import TODAY, FakeLLM, frame
from models import RankingReview, ReviewSuggestion


def ranked_sample(sample_df, spec):
    return rank_findings(run_scout(sample_df, spec, today=TODAY).findings)


def test_ranking_is_independent_of_input_order(sample_df, spec):
    findings = run_scout(sample_df, spec, today=TODAY).findings
    shuffled = findings[:]
    random.Random(7).shuffle(shuffled)
    assert [r.finding_id for r in rank_findings(findings)] == \
           [r.finding_id for r in rank_findings(shuffled)]


def test_contradictions_rank_first(sample_df, spec):
    ranked = ranked_sample(sample_df, spec)
    assert ranked[0].issue_type == "compliance_contradiction"
    assert ranked[0].lot_numbers == ["LOT400"]
    assert [r.rank for r in ranked] == list(range(1, len(ranked) + 1))


def test_review_suggestions_are_validated_and_advisory(sample_df, spec):
    ranked = ranked_sample(sample_df, spec)
    order_before = [r.finding_id for r in ranked]
    llm = FakeLLM({"RankingReview": RankingReview(suggestions=[
        ReviewSuggestion(finding_id=ranked[5].finding_id, suggested_rank=1, reason="valid"),
        ReviewSuggestion(finding_id="F999", suggested_rank=1, reason="unknown id"),
        ReviewSuggestion(finding_id=ranked[2].finding_id, suggested_rank=500, reason="bad rank"),
    ])})
    result = asyncio.run(review_ranking(ranked, llm))
    assert result.review_status == "reviewed"
    assert [r.finding_id for r in result.ranked] == order_before
    with_suggestion = [r.finding_id for r in result.ranked if r.review_suggestion]
    assert with_suggestion == [ranked[5].finding_id]
    assert "<findings>" in llm.calls[0]["user"]


def test_review_degrades_when_llm_unavailable(sample_df, spec):
    ranked = ranked_sample(sample_df, spec)
    assert asyncio.run(review_ranking(ranked, FakeLLM())).review_status == "unavailable"
    assert asyncio.run(review_ranking(ranked, FakeLLM(enabled=False))).review_status == "disabled"


def test_fixer_never_modifies_source(sample_df, spec):
    before = sample_df.copy()
    run_fixer(sample_df, ranked_sample(sample_df, spec), spec)
    assert sample_df.equals(before)


def test_higher_priority_flag_is_never_lost(spec):
    # One row that is both out of spec on a PASS record and missing its date.
    df = frame(["LOTX,PROD-A,,100,kg,112.4,4.0,INSP-01,FAC-01,PASS,"])
    ranked = rank_findings(run_scout(df, spec, today=TODAY).findings)
    corrected, result = run_fixer(df, ranked, spec)
    flags = corrected.at[0, FLAG_COLUMN].split(" | ")
    assert flags[0].startswith("CRITICAL")
    assert flags[1].startswith("MISSING_BATCH_DATE")


def test_every_finding_gets_exactly_one_action_and_its_flag(sample_df, spec):
    ranked = ranked_sample(sample_df, spec)
    corrected, result = run_fixer(sample_df, ranked, spec)
    assert sorted(a.finding_id for a in result.actions) == sorted(r.finding_id for r in ranked)
    flagged_ids = {fl.finding_id for fl in result.flags}
    assert flagged_ids == {r.finding_id for r in ranked}
    for fl in result.flags:
        assert fl.row_id in corrected.index
        assert f"({fl.finding_id})" in corrected.at[fl.row_id, FLAG_COLUMN]


def test_only_exact_duplicates_are_removed_and_each_is_logged(sample_df, spec):
    ranked = ranked_sample(sample_df, spec)
    corrected, result = run_fixer(sample_df, ranked, spec)
    removed = [c for c in result.change_log if c.rule == "remove_exact_duplicate"]
    assert len(corrected) == len(sample_df) - len(removed) == 152
    for entry in removed:
        assert entry.row_id not in corrected.index
        assert entry.old_value and '"lot_number"' in entry.old_value


def test_unit_normalisation_is_proposed_with_old_values(sample_df, spec):
    ranked = ranked_sample(sample_df, spec)
    corrected, result = run_fixer(sample_df, ranked, spec)
    lbs_row = sample_df.index[(sample_df.lot_number == "LOT302") & (sample_df.unit == "lbs")][0]
    assert corrected.at[lbs_row, "unit"] == "kg"
    assert abs(float(corrected.at[lbs_row, "quantity"]) - 396 * 0.45359237) < 0.001
    changes = {(c.field, c.old_value) for c in result.change_log if c.row_id == lbs_row}
    assert changes == {("quantity", "396"), ("unit", "lbs")}


def test_missing_dates_stay_empty(sample_df, spec):
    corrected, _ = run_fixer(sample_df, ranked_sample(sample_df, spec), spec)
    missing = sample_df.index[sample_df.batch_date.isna()]
    assert corrected.loc[missing, "batch_date"].isna().all()
