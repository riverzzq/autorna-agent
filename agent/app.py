"""AutoRNA Agent API — Week 3, step 1 (QC parser).

Endpoints:
  GET  /health            — liveness check
  POST /jobs              — validate a structured job spec, create a run (queued)
  POST /jobs/natural-language — natural language -> JobSpec, create a run.
                             OpenAI LLM first (needs OPENAI_API_KEY),
                             rule-based fallback; response includes
                             "parser": "llm" or "rule" so you can see
                             which one actually parsed it.
  GET  /jobs              — list runs (newest first)
  GET  /jobs/{run_id}     — one run, from SQLite
  POST /jobs/{run_id}/launch — launch Nextflow in a background thread
  GET  /jobs/{run_id}/qc  — REAL (Week 3): parses Salmon meta_info.json
                             percent_mapped + quant.sf NumReads and the
                             MultiQC general-stats rows for this run,
                             writes runs/<run_id>/qc.json, and records
                             qc_pass (1/0) in SQLite. Never fakes numbers;
                             a run that has not succeeded returns not_ready.

Run with:
  export OPENAI_API_KEY='sk-...'   # in the same terminal, before uvicorn
  uvicorn agent.app:app --reload --port 8000
  curl http://localhost:8000/health

The LLM never launches a pipeline directly: its tool-call output must
pass parse_job_spec() validation first, and if it fails, the
rule-based parser is tried before any error is returned.
"""
import json
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from . import db as rundb
from .deseq2 import run_deseq2
from .planner import parse_job_spec, parse_natural_language_with_source
from .qc import compute_qc_for_run
from .report import build_report
from .runner import launch_run, should_retry, write_retry_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent

import os

app = FastAPI(title="AutoRNA Agent", version="0.5.0-week3-full")


def _conn():
    return rundb.connect()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "0.5.0-week3-full",
        "llm_planner": "openai (gpt-4o-mini) + rule-based fallback" if os.environ.get("OPENAI_API_KEY") else "rule-based (set OPENAI_API_KEY to enable LLM)",
    }


def _create_run_from_spec(spec: dict):
    """Shared create logic for POST /jobs and POST /jobs/natural-language."""
    job = parse_job_spec(spec)
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
        run_dir = PROJECT_ROOT / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "job_spec.json").write_text(json.dumps(spec, indent=2))
    finally:
        conn.close()
    return {"run_id": run_id, "status": "queued", "contrast": job.contrast}


@app.post("/jobs")
def create_job(spec: dict):
    try:
        return _create_run_from_spec(spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/jobs/natural-language")
def create_job_from_text(body: dict):
    """Natural language -> run. OpenAI LLM first, rule-based fallback."""
    text = (body or {}).get("text", "")
    try:
        job, source = parse_natural_language_with_source(text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    spec = {
        "samples": job.samples,
        "contrast": job.contrast,
        "genome": job.genome,
        "strandedness": job.strandedness,
        "outdir": job.outdir,
        "profile": job.profile,
    }
    result = _create_run_from_spec(spec)
    result["parser"] = source
    return result


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
    """Background worker: run Nextflow, then log the real outcome.

    Week 3: on an OOM failure on the first attempt, relaunch ONCE
    with the next memory tier (retry.config: 2->4, 4->8, 8->16 GB)
    and record the attempt. Any other failure, or a second OOM,
    stops and needs a human decision.
    """
    run_dir = PROJECT_ROOT / "runs" / run_id
    spec_raw = json.loads((run_dir / "job_spec.json").read_text())
    job = parse_job_spec(spec_raw)
    conn = _conn()
    try:
        # Clear any error left by a previous failed attempt: a retry
        # that succeeds must not keep showing the old failure message.
        rundb.update_run(conn, run_id, status="running", error=None)
        row = rundb.get_run(conn, run_id)
        try:
            resources = json.loads(row.get("resources") or "{}")
        except (json.JSONDecodeError, TypeError):
            resources = {}
        attempt = int(resources.get("attempt", 0))
    finally:
        conn.close()
    result = launch_run(job, run_id=run_id, attempt=attempt)
    if result["status"] == "failed" and should_retry(result["error_kind"], attempt):
        retry_config = write_retry_config(run_dir)
        print(f"[retry] {run_id}: OOM on attempt {attempt}, relaunching once with more memory ({retry_config})", flush=True)
        attempt += 1
        conn = _conn()
        try:
            resources["attempt"] = attempt
            rundb.update_run(conn, run_id, status="running", resources=json.dumps(resources))
        finally:
            conn.close()
        result = launch_run(job, run_id=run_id, extra_config=retry_config, attempt=attempt)
        result = dict(result)
        result["retried"] = True
    conn = _conn()
    try:
        if result["status"] == "succeeded":
            rundb.update_run(conn, run_id, status="succeeded", wall_time_s=result["wall_time_s"], error=None)
            # Week 3: parse QC gates now; a QC failure does not rewrite
            # the pipeline outcome, it is recorded as qc_pass=0.
            try:
                qc = compute_qc_for_run(run_dir)
                rundb.update_run(conn, run_id, qc_pass=qc["qc_pass"])
            except Exception as exc:
                print(f"[qc] could not parse QC for {run_id}: {exc}", flush=True)
        else:
            # Log the failure honestly. A second OOM (or any other
            # kind) does NOT auto-retry again; it needs a human.
            retry = should_retry(result["error_kind"], attempt)
            error_text = result["error"] + (f" | retry_recommended={retry} (attempt {attempt} already used; no further auto-retry)" if retry else "")
            if result.get("retried"):
                error_text += " | auto-retry already used once"
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
    if row["status"] == "failed":
        # A manual relaunch is a fresh first try: reset the auto-retry
        # counter so an OOM on this try can retry once again.
        conn = _conn()
        try:
            try:
                resources = json.loads(row.get("resources") or "{}")
            except (json.JSONDecodeError, TypeError):
                resources = {}
            resources["attempt"] = 0
            rundb.update_run(conn, run_id, resources=json.dumps(resources))
        finally:
            conn.close()
    thread = threading.Thread(target=_launch_in_background, args=(run_id,), daemon=True)
    thread.start()
    return {"run_id": run_id, "status": "running"}


@app.get("/jobs/{run_id}/qc")
def get_qc(run_id: str):
    """REAL (Week 3). Parses this run's Salmon + MultiQC files.

    Returns the QC dict (per-sample percent_mapped, NumReads, gate
    results, qc_pass 1/0) and records qc_pass in SQLite. A run that
    has not succeeded yet returns not_ready instead of numbers.
    """
    conn = _conn()
    try:
        row = rundb.get_run(conn, run_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    if row["status"] != "succeeded":
        return {"run_id": run_id, "status": "not_ready", "run_status": row["status"]}
    try:
        qc = compute_qc_for_run(PROJECT_ROOT / "runs" / run_id)
    except (FileNotFoundError, ValueError) as exc:
        return {"run_id": run_id, "status": "not_ready", "run_status": row["status"], "reason": str(exc)}
    conn = _conn()
    try:
        rundb.update_run(conn, run_id, qc_pass=qc["qc_pass"])
    finally:
        conn.close()
    return qc


def _run_deseq2_in_background(run_id: str):
    """Background worker: run DESeq2, log the outcome in deseq2/summary.json."""
    run_dir = PROJECT_ROOT / "runs" / run_id
    try:
        summary = run_deseq2(run_dir)
        print(f"[deseq2] {run_id}: done, {summary['n_significant']} significant of {summary['n_genes']} genes", flush=True)
    except Exception as exc:
        (run_dir / "deseq2").mkdir(parents=True, exist_ok=True)
        (run_dir / "deseq2" / "error.json").write_text(json.dumps({"run_id": run_id, "status": "failed", "error": str(exc)}, indent=2))
        print(f"[deseq2] {run_id}: failed: {exc}", flush=True)


@app.post("/jobs/{run_id}/deseq2")
def start_deseq2(run_id: str):
    """Start DESeq2 for a run (background thread; R can take minutes).

    Only QC-passed samples are used; failed samples are named in
    deseq2/excluded_samples.json. Requires the run to have succeeded.
    """
    conn = _conn()
    try:
        row = rundb.get_run(conn, run_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    if row["status"] != "succeeded":
        raise HTTPException(status_code=409, detail=f"run is {row['status']}; DESeq2 needs a succeeded run")
    thread = threading.Thread(target=_run_deseq2_in_background, args=(run_id,), daemon=True)
    thread.start()
    return {"run_id": run_id, "status": "running"}


@app.get("/jobs/{run_id}/deseq2")
def get_deseq2(run_id: str):
    """DESeq2 summary for a run, or its status if not finished."""
    run_dir = PROJECT_ROOT / "runs" / run_id
    summary_path = run_dir / "deseq2" / "summary.json"
    if summary_path.exists():
        return json.loads(summary_path.read_text())
    error_path = run_dir / "deseq2" / "error.json"
    if error_path.exists():
        return json.loads(error_path.read_text())
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    return {"run_id": run_id, "status": "not_run"}


@app.get("/jobs/{run_id}/report", response_class=HTMLResponse)
def get_report(run_id: str):
    """One-page HTML report for a run: QC table + DESeq2 summary."""
    run_dir = PROJECT_ROOT / "runs" / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
    return build_report(run_dir).read_text()
