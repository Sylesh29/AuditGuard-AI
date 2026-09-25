"""AuditGuard AI HTTP API.

  POST /api/runs                  upload a CSV; validated synchronously, returns 202 + run_id
  GET  /api/runs/{id}             run status per stage
  GET  /api/runs/{id}/events      Server-Sent Events (resumable with Last-Event-ID)
  GET  /api/runs/{id}/findings    ranked findings joined with actions (409 until complete)
  GET  /api/runs/{id}/report.pdf | findings.csv | corrected.csv | changelog.csv
  GET  /api/health
The static frontend is served from / so the browser talks to the API on the same origin.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from ingest import UploadError, load_csv  # noqa: E402
from llm import StructuredLLM, create_llm  # noqa: E402
from pipeline import execute, findings_payload  # noqa: E402
from runs import Run, RunStore, sse_frame  # noqa: E402
from settings import Settings, SpecLimits, get_settings  # noqa: E402
from version import __version__  # noqa: E402

logger = logging.getLogger("auditguard")

ARTIFACT_TYPES = {
    "report.pdf": "application/pdf",
    "findings.csv": "text/csv; charset=utf-8",
    "corrected.csv": "text/csv; charset=utf-8",
    "changelog.csv": "text/csv; charset=utf-8",
}
MULTIPART_OVERHEAD = 64 * 1024

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def create_app(settings: Settings | None = None, llm: StructuredLLM | None = None) -> FastAPI:
    settings = settings or get_settings()
    spec = SpecLimits.load(settings.spec_path)  # fail fast on a bad spec file
    llm = llm or create_llm(settings)
    store = RunStore(settings.max_stored_runs, settings.run_ttl_s)
    slots = asyncio.Semaphore(settings.max_concurrent_runs)
    # Parsing happens inside the upload request; bound it too so parallel uploads cannot
    # exhaust memory before a run even exists.
    parse_slots = asyncio.Semaphore(settings.max_concurrent_runs * 2)
    upload_limit = settings.max_upload_bytes + MULTIPART_OVERHEAD

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("AuditGuard %s starting (LLM %s, spec %s)", __version__,
                    f"{llm.provider}/{llm.model}" if llm.enabled else "disabled", spec.sha256[:12])
        yield
        for run in store.active():
            if run.task:
                run.task.cancel()
        if hasattr(llm, "aclose"):
            await llm.aclose()

    app = FastAPI(title="AuditGuard AI", version=__version__, lifespan=lifespan)
    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                           allow_methods=["GET", "POST"], allow_headers=["Last-Event-ID"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/api/runs":
            length = request.headers.get("content-length")
            if length is None or not length.isdigit():
                return JSONResponse({"detail": "Content-Length is required."}, status_code=411)
            if int(length) > upload_limit:
                limit_mb = settings.max_upload_bytes // 2**20
                return JSONResponse({"detail": f"File exceeds {limit_mb} MB."}, status_code=413)
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def get_run(run_id: str) -> Run:
        run = store.get(run_id)
        if run is None:
            raise HTTPException(404, "Unknown or expired run.")
        return run

    def completed(run: Run) -> Run:
        if run.status != "complete":
            raise HTTPException(409, run.error or "Audit still running.")
        return run

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__, "llm_enabled": llm.enabled,
                "llm_provider": llm.provider if llm.enabled else None,
                "llm_model": llm.model if llm.enabled else None,
                "max_upload_mb": settings.max_upload_bytes // 2**20,
                "max_rows": settings.max_rows}

    @app.post("/api/runs", status_code=202)
    async def create_run(file: UploadFile = File(...)) -> dict:
        content = await file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(413, f"File exceeds {settings.max_upload_bytes // 2**20} MB.")
        try:
            async with parse_slots:
                dataset = await asyncio.to_thread(load_csv, content, file.filename, settings.max_rows)
        except UploadError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        try:
            run = store.create(dataset.name, dataset.sha256)
        except RuntimeError as exc:
            raise HTTPException(503, "Too many audits in progress; try again shortly.") from exc
        run.task = asyncio.create_task(execute(run, dataset, spec, llm, slots))
        logger.info("run %s created: %s, %d rows, sha256 %s", run.run_id, dataset.name,
                    len(dataset.frame), dataset.sha256[:12])
        return {**run.public(), "events_url": f"/api/runs/{run.run_id}/events"}

    @app.get("/api/runs/{run_id}")
    async def run_status(run_id: str) -> dict:
        return get_run(run_id).public()

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request) -> StreamingResponse:
        run = get_run(run_id)
        last = request.headers.get("last-event-id", "0")
        after = int(last) if last.isdigit() else 0

        async def stream():
            async for event in run.follow(after):
                if await request.is_disconnected():
                    return
                yield sse_frame(event)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/findings")
    async def run_findings(run_id: str) -> dict:
        return findings_payload(completed(get_run(run_id)))

    @app.get("/api/runs/{run_id}/{artifact}")
    async def run_artifact(run_id: str, artifact: str) -> Response:
        if artifact not in ARTIFACT_TYPES:
            raise HTTPException(404, "Unknown artifact.")
        run = completed(get_run(run_id))
        stem, ext = artifact.rsplit(".", 1)
        return Response(
            run.artifacts[artifact], media_type=ARTIFACT_TYPES[artifact],
            headers={"Content-Disposition":
                     f'attachment; filename="AuditGuard_{stem}_{run.reference_id}.{ext}"'},
        )

    if settings.frontend_dir:
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
