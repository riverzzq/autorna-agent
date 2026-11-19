"""DESeq2 wiring — AutoRNA Agent, Week 3 step 3.

Runs scripts/deseq2.R on a run's REAL Salmon quant.sf files, but
only for samples that passed the QC gates (agent/qc.py). A sample
that failed QC is excluded and named in excluded_samples.json —
never silently included, never silently dropped.

Pre-checks before R starts (fail loudly, do not guess):
- the run must have succeeded and have a QC result (computed on the
  fly if qc.json is missing);
- every contrast group must still have >= 2 QC-passed samples
  (DESeq2 without replicates cannot estimate dispersion properly);
- the GTF must yield at least one transcript_id -> gene_id pair
  (counted here in Python first, so a bad GTF is caught before R
  installs/loads anything).

After R finishes, results.tsv is parsed here into summary.json:
gene count, significant-gene count (padj<0.05 & |log2FC|>1) and
the top 5 genes by padj. The R script itself also writes
results.tsv, volcano_data.tsv, volcano.pdf and session_info.txt.

R and the Bioconductor packages (tximport, DESeq2) are NOT installed
by this code — they must exist on the machine (see README).
"""
import csv
import gzip
import json
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIN_REPLICATES_PER_GROUP = 2


def count_tx2gene(gtf_path):
    """Count transcript_id -> gene_id pairs parseable from a GTF (.gz or plain).

    Mirrors the R script's rule: a line must be a transcript feature
    and carry both transcript_id and gene_id attributes.
    """
    opener = gzip.open if str(gtf_path).endswith(".gz") else open
    pairs = set()
    with opener(gtf_path, "rt") as fh:
        for line in fh:
            if "\ttranscript\t" not in line:
                continue
            attrs = line.rsplit("\t", 1)[-1]
            tx = _attr(attrs, "transcript_id")
            gene = _attr(attrs, "gene_id")
            if tx and gene:
                pairs.add((tx, gene))
    return len(pairs)


def _attr(attrs, name):
    marker = f'{name} "'
    start = attrs.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = attrs.find('"', start)
    return attrs[start:end] if end > start else None


def run_deseq2(run_dir, gtf_path=None):
    """Run DESeq2 for runs/<run_id>. Returns a summary dict.

    Raises FileNotFoundError / ValueError for missing inputs or
    failed pre-checks, RuntimeError when R itself fails. The full
    R output is kept in runs/<run_id>/deseq2/deseq2.log.
    """
    from .planner import parse_job_spec
    from .qc import compute_qc_for_run

    run_dir = Path(run_dir)
    spec_raw = json.loads((run_dir / "job_spec.json").read_text())
    job = parse_job_spec(spec_raw)
    qc_path = run_dir / "qc.json"
    qc = json.loads(qc_path.read_text()) if qc_path.exists() else compute_qc_for_run(run_dir)
    if qc["qc_pass"] is None:
        raise ValueError("no QC result for this run; run GET /qc first")

    failed = set(qc.get("failed_samples", []))
    passed = [s for s in job.samples if s["sample"] not in failed]
    groups = {}
    for s in passed:
        groups.setdefault(s["group"], []).append(s["sample"])
    for group, members in groups.items():
        if len(members) < MIN_REPLICATES_PER_GROUP:
            raise ValueError(
                f"group {group} has only {len(members)} QC-passed sample(s) "
                f"({members}); DESeq2 needs at least {MIN_REPLICATES_PER_GROUP} per group"
            )
    if len(groups) < 2:
        raise ValueError("fewer than 2 groups left after QC filtering; cannot run a contrast")

    gtf_path = Path(gtf_path or (PROJECT_ROOT / "data" / "reference" / "genes.gtf.gz"))
    if not gtf_path.exists():
        raise FileNotFoundError(f"GTF not found: {gtf_path} (download it with scripts/fetch_test_data.sh)")
    tx2gene_count = count_tx2gene(gtf_path)
    if tx2gene_count == 0:
        raise ValueError(f"no transcript_id/gene_id pairs parsed from {gtf_path}; check the GTF format")

    outdir = run_dir / "deseq2"
    outdir.mkdir(parents=True, exist_ok=True)
    filtered = run_dir / "samplesheet_qc_passed.csv"
    with open(filtered, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["sample", "group", "fastq_1", "fastq_2", "strandedness"])
        writer.writeheader()
        for s in passed:
            writer.writerow({k: s.get(k, "") for k in writer.fieldnames})
    (outdir / "excluded_samples.json").write_text(
        json.dumps({"excluded": sorted(failed), "reason": "failed QC gates (see runs/<run_id>/qc.json)"}, indent=2)
    )

    log_path = outdir / "deseq2.log"
    cmd = [
        "Rscript", str(PROJECT_ROOT / "scripts" / "deseq2.R"),
        f"--quant_dir={run_dir / 'results' / 'salmon'}",
        f"--samples={filtered}",
        f"--gtf={gtf_path}",
        f"--contrast={job.contrast}",
        f"--outdir={outdir}",
    ]
    started = time.time()
    with open(log_path, "w") as log_f:
        log_f.write(f"# AutoRNA DESeq2 run {run_dir.name}\n# command: {' '.join(cmd)}\n\n")
        log_f.flush()
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), stdout=log_f, stderr=subprocess.STDOUT, timeout=3600)
    wall = round(time.time() - started, 1)
    if proc.returncode != 0:
        raise RuntimeError(f"Rscript exited {proc.returncode}; see {log_path}")
    summary = parse_deseq2_results(outdir / "results.tsv")
    summary.update({
        "run_id": run_dir.name,
        "contrast": job.contrast,
        "tx2gene_pairs": tx2gene_count,
        "samples_used": [s["sample"] for s in passed],
        "samples_excluded": sorted(failed),
        "wall_time_s": wall,
        "log": str(log_path),
    })
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def parse_deseq2_results(results_tsv):
    """Summarise a DESeq2 results.tsv: gene count, significant count, top 5 by padj."""
    rows = []
    with open(results_tsv) as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            try:
                padj = float(row["padj"]) if row.get("padj") not in (None, "", "NA") else None
            except ValueError:
                padj = None
            try:
                lfc = float(row["log2FoldChange"]) if row.get("log2FoldChange") not in (None, "", "NA") else None
            except ValueError:
                lfc = None
            rows.append({"gene": row.get("gene"), "log2FoldChange": lfc, "padj": padj})
    sig = [r for r in rows if r["padj"] is not None and r["padj"] < 0.05 and r["log2FoldChange"] is not None and abs(r["log2FoldChange"]) > 1]
    ranked = sorted([r for r in rows if r["padj"] is not None], key=lambda r: r["padj"])
    return {
        "n_genes": len(rows),
        "n_significant": len(sig),
        "top_genes": ranked[:5],
    }
