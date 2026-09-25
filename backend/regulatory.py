"""Deterministic regulatory orientation per issue type.

These are pointers for the quality team, not legal conclusions. Clause numbers refer to
ISO 13485:2016. Keep this table reviewed by someone who owns the QMS.
"""

REGULATORY_REFERENCES: dict[str, str] = {
    "compliance_contradiction": (
        "ISO 13485:2016 §8.2.6 (monitoring and measurement of product): release must be "
        "supported by records showing acceptance criteria were met. ALCOA+: Accurate."
    ),
    "lot_conflict": (
        "ISO 13485:2016 §7.5.9 (traceability) and §4.2.5 (control of records). "
        "ALCOA+: Consistent, Accurate."
    ),
    "exact_duplicate": "ISO 13485:2016 §4.2.5 (control of records). ALCOA+: Consistent.",
    "unit_conflict": (
        "ISO 13485:2016 §4.2.5 (records must remain legible and readily identifiable). "
        "ALCOA+: Accurate, Consistent."
    ),
    "statistical_outlier": (
        "ISO 13485:2016 §8.2.5 (monitoring and measurement of processes). ALCOA+: Accurate."
    ),
    "invalid_timestamp": "ISO 13485:2016 §4.2.5 (control of records). ALCOA+: Contemporaneous.",
    "missing_timestamp": (
        "ISO 13485:2016 §4.2.5 (control of records). ALCOA+: Contemporaneous, Complete."
    ),
}

REFERENCE_DISCLAIMER = (
    "Regulatory references are orientation for the quality team and must be confirmed "
    "against your own quality management system before submission. Spec limits come from "
    "the configured process specification, not from a regulation."
)

# What the compliance officer should do for each open item. Deterministic on purpose.
OPEN_ITEM_INSTRUCTIONS: dict[str, str] = {
    "compliance_contradiction": (
        "Pull the batch record and QC data for this lot. Either correct the status to FAIL "
        "or attach a documented, approved justification (deviation or concession) before release."
    ),
    "lot_conflict": (
        "Identify which record is the original. Confirm the true values against the batch "
        "record or a physical count, then retire the incorrect record through change control."
    ),
    "statistical_outlier": (
        "Confirm the reading against the instrument log. Document a process excursion or "
        "sensor fault, or confirm the value is valid."
    ),
    "unit_conflict": (
        "Confirm the unit of measure with the production engineer before accepting the "
        "proposed conversion."
    ),
    "missing_timestamp": (
        "Enter the production date from the batch record. Do not estimate it."
    ),
    "invalid_timestamp": (
        "Correct the date from the batch record using the YYYY-MM-DD format. Future dates "
        "must be investigated."
    ),
    "exact_duplicate": "Approve removal of the duplicate copies through change control.",
}
