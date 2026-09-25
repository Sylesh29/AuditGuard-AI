"""Typed contracts passed between pipeline stages."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

IssueType = Literal[
    "compliance_contradiction",
    "lot_conflict",
    "exact_duplicate",
    "unit_conflict",
    "statistical_outlier",
    "invalid_timestamp",
    "missing_timestamp",
]
Severity = Literal["HIGH", "MED", "LOW"]
ActionType = Literal["corrected", "flagged", "escalated"]

ISSUE_LABELS: dict[str, str] = {
    "compliance_contradiction": "Status contradicts spec",
    "lot_conflict": "Conflicting lot records",
    "exact_duplicate": "Exact duplicate",
    "unit_conflict": "Unit conflict",
    "statistical_outlier": "Statistical outlier",
    "invalid_timestamp": "Invalid batch date",
    "missing_timestamp": "Missing batch date",
}


class Finding(BaseModel):
    finding_id: str
    issue_type: IssueType
    severity: Severity
    row_ids: list[int]
    lot_numbers: list[str]
    details: dict[str, Any] = Field(default_factory=dict)
    reason: str
    regulatory_reference: str

    @property
    def label(self) -> str:
        return ISSUE_LABELS[self.issue_type]


class ScoutResult(BaseModel):
    findings: list[Finding]
    rows_scanned: int

    @property
    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.findings),
            "HIGH": sum(f.severity == "HIGH" for f in self.findings),
            "MED": sum(f.severity == "MED" for f in self.findings),
            "LOW": sum(f.severity == "LOW" for f in self.findings),
            "rows_scanned": self.rows_scanned,
        }


class ReviewSuggestion(BaseModel):
    finding_id: str
    suggested_rank: int
    reason: str


class RankedFinding(Finding):
    rank: int
    ranking_reason: str
    review_suggestion: ReviewSuggestion | None = None


class RankerResult(BaseModel):
    ranked: list[RankedFinding]
    review_status: Literal["reviewed", "no_changes", "unavailable", "disabled"]


class Action(BaseModel):
    finding_id: str
    action: ActionType
    description: str
    reason: str
    rows_affected: int


class ChangeLogEntry(BaseModel):
    """One proposed change to one field of one source row. The source file is never modified."""

    row_id: int
    lot_number: str
    field: str
    old_value: str | None
    new_value: str | None
    finding_id: str
    rule: str
    reason: str


class RowFlag(BaseModel):
    row_id: int
    finding_id: str
    rank: int
    text: str


class FixerResult(BaseModel):
    actions: list[Action]
    change_log: list[ChangeLogEntry]
    flags: list[RowFlag]
    rows_in: int
    rows_out: int

    @property
    def stats(self) -> dict[str, int]:
        return {
            "corrected": sum(a.action == "corrected" for a in self.actions),
            "flagged": sum(a.action == "flagged" for a in self.actions),
            "escalated": sum(a.action == "escalated" for a in self.actions),
            "changes_logged": len(self.change_log),
        }

    def action_for(self, finding_id: str) -> Action | None:
        return next((a for a in self.actions if a.finding_id == finding_id), None)


class ExecutiveSummary(BaseModel):
    """Structured output requested from the LLM. Only prose, never facts the report relies on."""

    summary: str = Field(description="Three to five plain-English sentences.")


class RankingReview(BaseModel):
    suggestions: list[ReviewSuggestion] = Field(
        description="Only findings whose rank you would change. Empty list if the ranking is sound."
    )
