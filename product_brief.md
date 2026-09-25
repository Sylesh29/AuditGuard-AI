# AuditGuard AI — Product Brief

**Track:** 01 — Data Rescue
**Team:** Solo builder
**Date:** June 7, 2026

---

## Who it's for

A compliance officer at a mid-size manufacturer preparing for a regulatory audit. Has never opened a database. Works in Excel. Needs answers, not code. Has an FDA audit in 4 days and has just discovered their manufacturing dataset is corrupted.

---

## What it does (one sentence)

AuditGuard AI scans a corrupted manufacturing dataset, ranks every data integrity issue by audit risk, fixes what it can automatically, and delivers a plain-English narrative the compliance officer can sign and hand to an auditor.

---

## What success looks like

A compliance officer with zero technical skills uploads a CSV, clicks one button, watches the system work in real time, and downloads a PDF they can submit to a regulator without asking an engineer a single question.

---

## How it works

1. **Scout** - Detects data-integrity issues with deterministic rules and attaches a reviewed regulatory reference to each.
2. **Ranker** - Prioritizes findings by audit risk with deterministic rules; Claude gives an advisory second opinion.
3. **Fixer** - Proposes safe corrections in a separate copy (the source is never modified), flags and escalates the rest. Every change logged with old value, new value and reason.
4. **Narrator** - Builds the report from the data (every finding included); Claude writes only the validated executive summary.

---

## What we are NOT building

- Enterprise SSO or multi-tenant authentication
- Custom database connectors (CSV upload only)
- Real-time monitoring or continuous audit pipelines

---

## Judging criteria alignment

| Criterion | How AuditGuard AI satisfies it |
|-----------|-------------------------------|
| Data rescue demonstrated | 7 issue types detected, 3 action types taken with logged reasons |
| Multi-agent architecture | 4 specialised stages handing off through a per-run shared blackboard |
| Explainability | Every ranking and fixing decision has a specific plain-English reason |
| Non-technical usability | Zero-jargon UI, drag-and-drop upload, PDF output ready to sign |
| Regulatory relevance | ISO 13485:2016 clause and ALCOA+ references per finding; source records never altered (21 CFR 11.10(e) principle) |
