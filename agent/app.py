"""AutoRNA Agent API — Week 1 skeleton (FastAPI).

Endpoints:
  GET  /health            — liveness check
  POST /jobs              — validate a structured job spec, create a run (queued)
  GET  /jobs              — list runs (newest first)
  GET  /jobs/{run_id}     — one run, from SQLite
  POST /jobs/{run_id}/launch — launch Nextflow in a background thread
  GET  /jobs/{run_id}/qc  — STUB (Week 3): returns not_implemented,
                             never fake QC numbers

Run with:
  uvicorn agent.app:app --reload --port 8000
  curl http://localhost:8000/health

The API never claims the LLM planner is live: POST /jobs accepts a
structured spec only. Natural-language parsing is a stub in
agent/planner.py (TODO, Week 2).
"""
import json
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException

from . import db as rundb
from .planner import parse_job_spec
from .runner import launch_run, should_retry

PROJECT_ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(title="AutoRNA Agent", version="0.1.0-week1")


def _conn():
    return rundb.connect()


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0-week1", "llm_planner": "stub (Week 2 TODO)"}


@app.post("/jobs")
def create_job(spec: dict):
    try:
        job = parse_job_spec(spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    conn = _conn()
    try:
        rundb.insert_run(
            conn, run_id,
            samples=json.dumps([s["sample"] for s in job.samples]),
            contrast=job.contrast,
            genome=job.genome,
            resources=json.dumps({"profile": job.profile, "outdir": job.outdir}),
        )
        # Store the full spec alongside the run for launch.
        run_dir = PROJECT_ROOT / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "job_spec.json").write_text(json.dumps(spec, indent=2))
    finally:
        conn.close()
    return {"run_id": run_id, "status": "queued", "contrast": job.contrast}


@app.get("/jobs")
def list_jobs(limit: int = 50):
    conn = _conn()
    try:
        return rundb.list_runs(conn, limit=limit)
    finally:
        conn.close()


@app.get("/jobs/{run_id}")
def get_job(run_id: str):
    conn = _conn()
    try:
        row = rundb.get_run(conn, run_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    return row


def _launch_in_background(run_id: str):
    """Background worker: run Nextflow, then log the real outcome."""
    run_dir = PROJECT_ROOT / "runs" / run_id
    spec_raw = json.loads((run_dir / "job_spec.json").read_text())
    job = parse_job_spec(spec_raw)
    conn = _conn()
    try:
        rundb.update_run(conn, run_id, status="running")
    finally:
        conn.close()
    result = launch_run(job, run_id=run_id)
    conn = _conn()
    try:
        if result["status"] == "succeeded":
            rundb.update_run(conn, run_id, status="succeeded", wall_time_s=result["wall_time_s"])
        else:
            # Log the failure honestly. Week 1 does NOT auto-retry;
            # record whether a retry would be recommended (OOM, first try).
            retry = should_retry(result["error_kind"], attempt=0)
            error_text = result["error"] + (f" | retry_recommended={retry} (Week 3 TODO: auto-retry)" if retry else "")
            rundb.update_run(
                conn, run_id, status="failed",
                wall_time_s=result["wall_time_s"], error=error_text,
            )
    finally:
        conn.close()


@app.post("/jobs/{run_id}/launch")
def launch_job(run_id: str):
    conn = _conn()
    try:
        row = rundb.get_run(conn, run_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    if row["status"] not in ("queued", "failed"):
        raise HTTPException(status_code=409, detail=f"run is {row['status']}; cannot launch")
    thread = threading.Thread(target=_launch_in_background, args=(run_id,), daemon=True)
    thread.start()
    return {"run_id": run_id, "status": "running"}


@app.get("/jobs/{run_id}/qc")
def get_qc(run_id: str):
    """STUB (Week 3). Returns not_implemented — never fake QC numbers."""
    conn = _conn()
    try:
        row = rundb.get_run(conn, run_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    return {
        "run_id": run_id,
        "status": "not_implemented",
        "todo": "Week 3: parse MultiQC data + Salmon logs per docs/qc-gates.md; "
                "do not report qc_pass until that parser exists.",
        "run_status": row["status"],
    }
