"""Stage 3, Fixer: proposes corrections without ever touching the source records.

Design rules (21 CFR 11.10(e): changes must not obscure previously recorded information):
  * The uploaded data is read-only. Corrections go into a separate *proposed* copy.
  * Every proposed change is a ChangeLogEntry with old value, new value, rule and reason.
  * Flags accumulate per row in rank order. A lower-priority flag never replaces a higher one.
  * Anything whose true value is unknown is escalated or flagged, never guessed.
"""
from __future__ import annotations

import json
from collections import defaultdict

import pandas as pd

from models import Action, ChangeLogEntry, FixerResult, RankedFinding, RowFlag
from settings import SpecLimits

FLAG_COLUMN = "audit_flags"
FLAG_SEPARATOR = " | "


def _text(value: object) -> str | None:
    return None if pd.isna(value) else str(value)


def _format_quantity(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


class _Fixer:
    def __init__(self, source: pd.DataFrame, spec: SpecLimits) -> None:
        self.spec = spec
        self.corrected = source.copy(deep=True)
        self.actions: list[Action] = []
        self.changes: list[ChangeLogEntry] = []
        self.flags: list[RowFlag] = []
        self.replaced_by: dict[int, int] = {}  # removed duplicate row -> kept row

    def _lot(self, row_id: int) -> str:
        return _text(self.corrected.at[row_id, "lot_number"]) or ""

    def _live(self, row_ids: list[int]) -> list[int]:
        """Map removed duplicates onto the kept copy so their flags are not lost."""
        return sorted({self.replaced_by.get(r, r) for r in row_ids})

    def flag(self, finding: RankedFinding, text: str, row_ids: list[int] | None = None) -> list[int]:
        rows = self._live(row_ids if row_ids is not None else finding.row_ids)
        for row_id in rows:
            self.flags.append(RowFlag(row_id=row_id, finding_id=finding.finding_id,
                                      rank=finding.rank, text=text))
        return rows

    def change(self, finding: RankedFinding, row_id: int, field: str, new: str | None,
               rule: str, reason: str) -> None:
        old = _text(self.corrected.at[row_id, field])
        self.changes.append(ChangeLogEntry(
            row_id=row_id, lot_number=self._lot(row_id), field=field, old_value=old,
            new_value=new, finding_id=finding.finding_id, rule=rule, reason=reason))
        self.corrected.at[row_id, field] = new

    def act(self, finding: RankedFinding, action: str, description: str, reason: str,
            rows: int) -> None:
        self.actions.append(Action(finding_id=finding.finding_id, action=action,
                                   description=description, reason=reason, rows_affected=rows))

    # --- handlers, one per issue type ---------------------------------------------------

    def exact_duplicate(self, f: RankedFinding) -> None:
        keep, *remove = sorted(f.row_ids)
        for row_id in remove:
            record = {k: _text(v) for k, v in self.corrected.loc[row_id].items()}
            self.changes.append(ChangeLogEntry(
                row_id=row_id, lot_number=self._lot(row_id), field="(entire row)",
                old_value=json.dumps(record), new_value=None, finding_id=f.finding_id,
                rule="remove_exact_duplicate",
                reason=f"Identical copy of row {keep}; the original row is kept unchanged."))
            self.replaced_by[row_id] = keep
        self.corrected = self.corrected.drop(index=remove)
        self.flag(f, f"DUPLICATE_REMOVED: {len(remove)} identical cop"
                     f"{'y' if len(remove) == 1 else 'ies'} removed ({f.finding_id})", [keep])
        self.act(f, "corrected", f"Proposed removal of {len(remove)} identical copy/copies; "
                 f"row {keep} kept.",
                 "Every column is identical, so no information is lost by keeping one copy. "
                 "The removed rows are preserved in the change log.", len(remove))

    def unit_conflict(self, f: RankedFinding) -> None:
        units = self.spec.units
        converted = []
        for row_id in self._live(f.row_ids):
            factor = units.factor(self.corrected.at[row_id, units.column])
            unit = _text(self.corrected.at[row_id, units.column])
            quantity = pd.to_numeric(self.corrected.at[row_id, units.quantity_column],
                                     errors="coerce")
            if factor is None or pd.isna(quantity) or unit.strip().lower() == units.canonical:
                continue
            new_qty = _format_quantity(float(quantity) * factor)
            reason = f"Converted {quantity:g} {unit} to {units.canonical} (x{factor:g})."
            self.change(f, row_id, units.quantity_column, new_qty, "normalise_unit", reason)
            self.change(f, row_id, units.column, units.canonical, "normalise_unit", reason)
            converted.append(row_id)

        if converted and f.details.get("agree_after_conversion"):
            self.flag(f, f"UNIT_NORMALISED: converted to {units.canonical}; confirm with "
                         f"production engineer ({f.finding_id})")
            self.act(f, "corrected",
                     f"Proposed conversion of {len(converted)} row(s) to {units.canonical}.",
                     "After conversion the records agree, so the conversion is safe to propose. "
                     "An engineer must confirm the canonical unit.", len(converted))
        else:
            self.flag(f, f"UNIT_CONFLICT: quantities disagree even after conversion "
                         f"({f.finding_id})")
            self.act(f, "flagged", "Flagged; quantity left unchanged.",
                     "The quantities do not reconcile after conversion, so the true batch "
                     "weight is unknown.", len(f.row_ids))

    def compliance_contradiction(self, f: RankedFinding) -> None:
        violations = "; ".join(f"{v['parameter']}={v['value']:g} (spec {v['spec']})"
                               for v in f.details.get("violations", []))
        rows = self.flag(f, f"CRITICAL: marked {f.details.get('status', 'PASS')} but "
                            f"{violations}. Do not submit without QA sign-off ({f.finding_id})")
        self.act(f, "escalated", "Escalated for QA sign-off. Status left unchanged.",
                 "Only a person with the batch record can decide whether the status or the "
                 "measurement is wrong.", len(rows))

    def lot_conflict(self, f: RankedFinding) -> None:
        fields = ", ".join(f.details.get("conflicting_fields", []))
        rows = self.flag(f, f"LOT_CONFLICT: records disagree on {fields}; verify against the "
                            f"batch record ({f.finding_id})")
        self.act(f, "escalated", "Escalated. All versions kept.",
                 "The data cannot say which version is true; a physical count or the batch "
                 "record must decide.", len(rows))

    def statistical_outlier(self, f: RankedFinding) -> None:
        d = f.details
        rows = self.flag(f, f"OUTLIER_REVIEW: {d.get('column')}={d.get('value'):g} is far "
                            f"outside the normal range for {d.get('baseline')} ({f.finding_id})")
        self.act(f, "flagged", "Flagged for review. Value left unchanged.",
                 "An extreme value may be a sensor fault or a real process excursion; either "
                 "way it needs a documented explanation.", len(rows))

    def missing_timestamp(self, f: RankedFinding) -> None:
        rows = self.flag(f, f"MISSING_BATCH_DATE: enter from the batch record ({f.finding_id})")
        self.act(f, "flagged", "Flagged for manual entry. Field left empty.",
                 "A production date cannot be inferred and must not be estimated.", len(rows))

    def invalid_timestamp(self, f: RankedFinding) -> None:
        rows = self.flag(f, f"INVALID_BATCH_DATE: '{f.details.get('value')}' "
                            f"({f.details.get('problem')}) ({f.finding_id})")
        self.act(f, "flagged", "Flagged for correction. Value left unchanged.",
                 "The correct date must come from the batch record.", len(rows))


def run_fixer(source: pd.DataFrame, ranked: list[RankedFinding], spec: SpecLimits
              ) -> tuple[pd.DataFrame, FixerResult]:
    fixer = _Fixer(source, spec)
    # Resolve duplicates first so later flags on a removed copy land on the kept row.
    for finding in sorted(ranked, key=lambda f: f.issue_type != "exact_duplicate"):
        getattr(fixer, finding.issue_type)(finding)
    fixer.actions.sort(key=lambda a: next(f.rank for f in ranked if f.finding_id == a.finding_id))

    by_row: dict[int, list[RowFlag]] = defaultdict(list)
    for flag in fixer.flags:
        by_row[flag.row_id].append(flag)
    corrected = fixer.corrected
    corrected[FLAG_COLUMN] = [
        FLAG_SEPARATOR.join(fl.text for fl in sorted(by_row.get(row_id, []), key=lambda x: x.rank))
        for row_id in corrected.index
    ]

    return corrected, FixerResult(
        actions=fixer.actions,
        change_log=fixer.changes,
        flags=sorted(fixer.flags, key=lambda fl: (fl.row_id, fl.rank)),
        rows_in=len(source),
        rows_out=len(corrected),
    )
