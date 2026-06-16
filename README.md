<div align="center">

# 🛡️ AuditGuard AI

### Multi-Agent Data Rescue & Regulatory Audit System

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Claude](https://img.shields.io/badge/Anthropic-Claude-D97706?style=flat-square)](https://anthropic.com)
[![Cognee](https://img.shields.io/badge/Cognee-Memory%20Layer-6366F1?style=flat-square)](https://cognee.ai)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

**4 AI agents that scan corrupted manufacturing data, rank every issue by audit risk, fix what they can, and deliver a regulator-ready PDF narrative — all in under a minute.**

🏆 *Track 01 — Data Rescue · M-AGENTS Hackathon · June 2026*

</div>

---

## The Problem

Manufacturers face FDA audits with thousands of rows of production data that contain duplicates, unit conflicts, outliers, and compliance contradictions. Catching these manually takes days. Missing them costs millions.

## The Solution

AuditGuard AI is a 4-agent pipeline where each agent reads from and writes to a **shared Cognee memory layer** — real handoffs, not file passing:

```
CSV Upload
    │
    ▼
Agent 1: Scout ──── detects issues ────► Cognee Memory
                                               │
Agent 2: Ranker ◄── reads findings ───────────┤
    │ ranks by audit risk ────────────────────►│
                                               │
Agent 3: Fixer ◄──── reads ranked list ───────┤
    │ auto-repairs what it can ───────────────►│
                                               │
Agent 4: Narrator ◄── reads ALL memory ───────┘
    │
    ▼
📄 Regulator-Ready PDF Download
```

---

## What It Detects

| Issue Type | Example |
|-----------|---------|
| Exact duplicates | Same row appears 8 times |
| Near-duplicates | Same lot, quantity off by 1–2 |
| Unit conflicts | kg vs lbs in same column |
| Statistical outliers | Temperature 3σ from mean |
| Missing timestamps | `batch_date` is null |
| Compliance contradictions | PASS status + temp > 85°C (FDA auto-fail) |

Regulatory standards cited: **FDA 21 CFR Part 11** and **ISO 13485**

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Multi-agent memory | Cognee (with in-memory fallback) |
| LLM reasoning | Anthropic Claude |
| Backend | FastAPI + Python |
| Frontend | React (CDN, no build step) |
| Output | PDF with signature block |

---

## Quickstart

```bash
# 1. Install dependencies
cd backend
pip install -r requirements.txt
cp .env.example .env
```

Add to `.env`:
```env
ANTHROPIC_API_KEY=your_key
COGNEE_API_KEY=your_key   # free 14-day trial at cognee.ai
```

> No Cognee key? The pipeline automatically falls back to in-memory store — it still runs fully.

```bash
# 2. Start backend
uvicorn main:app --reload --port 8000

# 3. Open frontend
open frontend/index.html   # No build step — uses CDN React
```

### Run the demo

1. Drag `data/sample.csv` into the upload zone
2. Watch all 4 agents fire in sequence with real-time status updates
3. Download the signed-ready audit narrative PDF

---

## Why the Judges Liked It

| Criterion | How AuditGuard AI delivers |
|-----------|--------------------------|
| Data rescue | Detects 6 issue types across 200 rows automatically |
| Multi-agent architecture | 4 agents with real Cognee memory handoffs |
| Explainability | Every decision has a specific plain-English reason — "The model said so" never appears |
| Non-technical usability | Drag-and-drop UI → PDF with signature block, no engineer required |
| Regulatory relevance | FDA 21 CFR Part 11 + ISO 13485 cited; compliance contradictions auto-escalated |

---

## Agent Responsibilities

**Scout** — scans every row, classifies issue type and severity, writes findings to Cognee

**Ranker** — reads Scout's findings, scores by audit risk using Claude + FDA context, writes prioritized list

**Fixer** — reads ranked issues, auto-repairs what's safe to fix (dedup, unit normalization), logs actions

**Narrator** — reads all memory, generates plain-English audit narrative with regulatory citations, exports PDF

---

<div align="center">

Built with Claude + Cognee · M-AGENTS Hackathon 2026

</div>
