"""FastAPI application for AuditGuard AI — 4-agent manufacturing data rescue pipeline."""
import os
import io
import json
import asyncio
import logging
from datetime import date
from typing import AsyncGenerator

import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from sse_starlette.sse import EventSourceResponse
from dotenv import load_dotenv

load_dotenv(override=True)

from agents.scout import run_scout
from agents.ranker import run_ranker
from agents.fixer import run_fixer
from agents.narrator import run_narrator
from memory.cognee_store import init_cognee, clear_session, read_memory
from utils.pdf_gen import markdown_to_pdf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AuditGuard AI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single-user in-memory session store
session = {
    "df_original": None,
    "df_fixed": None,
    "narrative_md": None,
    "narrative_pdf": None,
    "filename": None,
    "status": "idle",
    "agent_statuses": {
        "scout": "idle",
        "ranker": "idle",
        "fixer": "idle",
        "narrator": "idle"
    },
    "findings_data": None
}


@app.on_event("startup")
async def startup():
    await init_cognee()


def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@app.post("/run-audit")
async def run_audit(file: UploadFile = File(...)):
    """Accept CSV, run 4-agent pipeline, stream SSE events."""

    async def pipeline_generator() -> AsyncGenerator[str, None]:
        session["status"] = "running"
        session["filename"] = file.filename or "upload.csv"
        session["findings_data"] = None
        session["narrative_pdf"] = None

        # Reset statuses
        for k in session["agent_statuses"]:
            session["agent_statuses"][k] = "idle"

        # Parse CSV
        try:
            content = await file.read()
            df = pd.read_csv(io.BytesIO(content))
            session["df_original"] = df
        except Exception as e:
            yield _sse_event({"agent": "pipeline", "status": "error", "message": str(e)})
            return

        await clear_session()

        # Agent 1: Scout
        try:
            session["agent_statuses"]["scout"] = "running"
            yield _sse_event({"agent": "scout", "status": "running"})
            scout_result = await run_scout(df)
            session["agent_statuses"]["scout"] = "complete"
            yield _sse_event({
                "agent": "scout",
                "status": "complete",
                "summary": scout_result.get("summary", {})
            })
        except Exception as e:
            logger.error(f"Scout error: {e}")
            session["agent_statuses"]["scout"] = "error"
            yield _sse_event({"agent": "scout", "status": "error", "message": str(e)})

        # Agent 2: Ranker
        try:
            session["agent_statuses"]["ranker"] = "running"
            yield _sse_event({"agent": "ranker", "status": "running"})
            ranker_result = await run_ranker()
            session["agent_statuses"]["ranker"] = "complete"
            yield _sse_event({
                "agent": "ranker",
                "status": "complete",
                "summary": {"ranked": len(ranker_result.get("ranked_findings", []))}
            })
        except Exception as e:
            logger.error(f"Ranker error: {e}")
            session["agent_statuses"]["ranker"] = "error"
            yield _sse_event({"agent": "ranker", "status": "error", "message": str(e)})

        # Agent 3: Fixer
        try:
            session["agent_statuses"]["fixer"] = "running"
            yield _sse_event({"agent": "fixer", "status": "running"})
            df_fixed, action_log = await run_fixer(df)
            session["df_fixed"] = df_fixed
            session["agent_statuses"]["fixer"] = "complete"
            yield _sse_event({
                "agent": "fixer",
                "status": "complete",
                "summary": action_log.get("stats", {})
            })
        except Exception as e:
            logger.error(f"Fixer error: {e}")
            session["agent_statuses"]["fixer"] = "error"
            yield _sse_event({"agent": "fixer", "status": "error", "message": str(e)})

        # Agent 4: Narrator
        try:
            session["agent_statuses"]["narrator"] = "running"
            yield _sse_event({"agent": "narrator", "status": "running"})
            narrative_md = await run_narrator(session["filename"])
            session["narrative_md"] = narrative_md
            pdf_bytes = markdown_to_pdf(narrative_md, session["filename"])
            session["narrative_pdf"] = pdf_bytes
            session["agent_statuses"]["narrator"] = "complete"
            yield _sse_event({
                "agent": "narrator",
                "status": "complete",
                "summary": {"pdf_ready": True}
            })
        except Exception as e:
            logger.error(f"Narrator error: {e}")
            session["agent_statuses"]["narrator"] = "error"
            yield _sse_event({"agent": "narrator", "status": "error", "message": str(e)})

        # Build findings data for /findings endpoint
        try:
            ranker_data = await read_memory("ranker")
            fixer_data = await read_memory("fixer")
            scout_data = await read_memory("scout")

            ranked = (ranker_data or {}).get("ranked_findings", [])
            auto_fixed = {a["finding_id"]: a for a in (fixer_data or {}).get("auto_fixed", [])}
            flagged = {f["finding_id"]: f for f in (fixer_data or {}).get("flagged", [])}
            escalated = {e["finding_id"]: e for e in (fixer_data or {}).get("escalated", [])}

            findings_list = []
            for r in ranked:
                fid = r["finding_id"]
                action = "No action"
                if fid in auto_fixed:
                    action = auto_fixed[fid]["action_taken"]
                elif fid in flagged:
                    action = "Flagged for review"
                elif fid in escalated:
                    action = "Escalated — requires sign-off"

                findings_list.append({
                    "rank": r["rank"],
                    "finding_id": fid,
                    "issue_type": r["issue_type"].replace("_", " ").title(),
                    "severity": r["severity"],
                    "ranking_reason": r["ranking_reason"],
                    "rows_affected": r["rows_affected"],
                    "action_taken": action,
                    "claude_note": r.get("claude_note", ""),
                    "lot_numbers": r.get("lot_numbers", [])
                })

            scout_summary = (scout_data or {}).get("summary", {})
            fixer_stats = (fixer_data or {}).get("stats", {})

            session["findings_data"] = {
                "findings": findings_list,
                "stats": {
                    "HIGH": scout_summary.get("HIGH", 0),
                    "MED": scout_summary.get("MED", 0),
                    "LOW": scout_summary.get("LOW", 0),
                    "total": scout_summary.get("total", 0),
                    "auto_fixed": fixer_stats.get("fixed", 0),
                    "flagged": fixer_stats.get("flagged", 0),
                    "escalated": fixer_stats.get("escalated", 0)
                }
            }
        except Exception as e:
            logger.error(f"Findings assembly error: {e}")

        session["status"] = "complete"
        yield _sse_event({"agent": "pipeline", "status": "complete"})

    return EventSourceResponse(pipeline_generator())


@app.get("/findings")
async def get_findings():
    """Return ranker+fixer output as JSON for findings table."""
    if session["findings_data"] is None:
        raise HTTPException(status_code=425, detail="Audit not complete yet")
    return session["findings_data"]


@app.get("/download-narrative")
async def download_narrative():
    """Return audit narrative as downloadable PDF."""
    if session["narrative_pdf"] is None:
        raise HTTPException(status_code=425, detail="PDF not ready yet")
    date_str = date.today().strftime("%Y%m%d")
    return Response(
        content=session["narrative_pdf"],
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="AuditGuard_Narrative_{date_str}.pdf"'
        }
    )


@app.get("/status")
async def get_status():
    return {
        "status": session["status"],
        "agent_statuses": session["agent_statuses"]
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "AuditGuard AI"}
