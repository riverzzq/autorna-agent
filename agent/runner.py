"""Nextflow launcher + failure logging — AutoRNA Agent, Week 1.

The runner builds the Nextflow command from a validated JobSpec,
writes a samplesheet for that run, launches Nextflow as a subprocess,
and records the outcome in SQLite via agent.db. Failures are logged
with a classified error kind; they are never reported as success.

Retry policy (Week 1: decision function only, auto-retry is Week 3):
- OOM (exit / log mentions memory, killed, OutOfMemory) -> retry once
  with more memory is RECOMMENDED, but the runner does NOT auto-retry
  yet. `should_retry()` returns the decision; `app.py` logs it.
- Anything else -> no retry; fix the input and resubmit.
"""
import json
import subprocess
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Substrings (lowercased) used to classify a failed Nextflow log.
# These are heuristics for triage, not a diagnosis. The raw log is
# always kept at runs/<run_id>/nextflow.log.
ERROR_KINDS = {
    "oom": ["out of memory", "outofmemory", "killed", "memory limit", "exit code 137"],
    "timeout": ["timeout", "timed out", "exceeded time limit"],
    "file_not_found": ["no such file", "file not found", "checkifexists"],
    "container": ["docker", "singularity", "container", "image not found", "pull access denied"],
}


def classify_error(log_text: str) -> str:
    low = (log_text or "").lower()
    for kind, markers in ERROR_KINDS.items():
        if any(m in low for m in markers):
            return kind
    return "unknown"


def should_retry(error_kind: str, attempt: int) -> bool:
    """Week 1 decision only. Auto-retry execution is a Week 3 TODO."""
    return error_kind == "oom" and attempt == 0


def write_samplesheet(spec, run_dir: Path) -> Path:
    """Write the run's samplesheet from the validated JobSpec."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "samplesheet.csv"
    lines = ["sample,group,fastq_1,fastq_2,strandedness"]
    for s in spec.samples:
        lines.append(
            f"{s['sample']},{s['group']},{s['fastq_1']},{s['fastq_2']},{spec.strandedness}"
        )
    path.write_text("\n".join(lines) + "\n")
    return path


def build_nextflow_command(spec, samplesheet: Path, run_dir: Path) -> list:
    """Build the Nextflow command. No shell=True, no string interpolation."""
    transcriptome = PROJECT_ROOT / "data" / "reference" / "transcriptome.fasta.gz"
    cmd = [
        "nextflow", "run", "main.nf",
        "--input", str(samplesheet),
        "--transcriptome", str(transcriptome),
        "--outdir", str(run_dir / "results"),
        "-profile", spec.profile,
        "-work-dir", str(run_dir / "work"),
    ]
    return cmd


def launch_run(spec, run_id: str | None = None) -> dict:
    """Launch Nextflow synchronously and return a result dict.

    Returns {"run_id", "status": "succeeded"|"failed", "wall_time_s",
    "error_kind", "error", "log_path"}. The caller (app.py) is
    responsible for writing this into the SQLite run log.
    Use launch_run_async in app.py for the API so HTTP does not block.
    """
    run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
    run_dir = PROJECT_ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    samplesheet = write_samplesheet(spec, run_dir)
    cmd = build_nextflow_command(spec, samplesheet, run_dir)
    log_path = run_dir / "nextflow.log"
    started = time.time()
    with open(log_path, "w") as log_f:
        log_f.write(f"# AutoRNA run {run_id}\n# command: {' '.join(cmd)}\n\n")
        log_f.flush()
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), stdout=log_f, stderr=subprocess.STDOUT)
    wall = time.time() - started
    log_text = log_path.read_text(errors="replace")[-20000:]
    if proc.returncode == 0:
        return {
            "run_id": run_id, "status": "succeeded", "wall_time_s": round(wall, 1),
            "error_kind": None, "error": None, "log_path": str(log_path),
        }
    kind = classify_error(log_text)
    return {
        "run_id": run_id, "status": "failed", "wall_time_s": round(wall, 1),
        "error_kind": kind, "error": f"nextflow exit code {proc.returncode}; kind={kind}; see {log_path}",
        "log_path": str(log_path),
    }


def samplesheet_from_json(samples: list) -> str:
    """Helper for tests / debugging: render a samplesheet string."""
    lines = ["sample,group,fastq_1,fastq_2,strandedness"]
    for s in samples:
        lines.append(f"{s['sample']},{s['group']},{s['fastq_1']},{s['fastq_2']},reverse")
    return "\n".join(lines) + "\n"
