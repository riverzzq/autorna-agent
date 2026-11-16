# AutoRNA Agent

**Problem, in one paragraph (for interviews):** RNA-seq pipelines
are brittle and need babysitting. A run fails halfway — out of
memory, a missing file, a bad sample — and nobody notices until
hours later, or the failure is quietly ignored and a results table
is produced from incomplete data. AutoRNA Agent is a small
agent-driven wrapper around a standard RNA-seq workflow: you submit
a job, it validates the spec, launches the pipeline, watches it,
logs every run (including failures) to a database, checks QC gates,
and produces a report. It does not invent a new quantifier and it
does not replace nf-core/rnaseq — it is the orchestration,
monitoring, and QC layer on top of established tools (FastQC,
Salmon, MultiQC, DESeq2).

**Scope, honestly:** this is a solo portfolio project for a job
search, not a research contribution and not a product. Its value is
as evidence: Nextflow DSL2, containers, an agent that launches and
monitors real work, SQLite run logging, QC gates with failure
handling, and a report a non-specialist can read. If you are
evaluating it as novel science, it is not that — and it does not
claim to be.

## Architecture

```
Natural-language request (Week 2 TODO — LLM planner is a stub)
        |
        v
Structured job spec (JSON) ──► planner.py validates it
        │   samples, contrast "<B>_vs_<A>", genome, profile
        v
FastAPI (agent/app.py) ──► creates run row in SQLite (status=queued)
        │   POST /jobs, GET /jobs/{id}, POST /jobs/{id}/launch
        v
runner.py ──► writes runs/<id>/samplesheet.csv
        │   builds `nextflow run main.nf ...` (no shell=True)
        v
Nextflow DSL2 (main.nf)
        ├── FASTQC  (modules/fastqc.nf)
        ├── SALMON_INDEX (modules/salmon.nf; built from transcriptome)
        ├── SALMON_QUANT (modules/salmon.nf; --validateMappings)
        └── MULTIQC (modules/multiqc.nf)
        │   outputs: results/fastqc/, results/salmon/<sample>/quant.sf,
        │            results/multiqc/multiqc_report.html
        v
QC gates (docs/qc-gates.md; parser = Week 3 TODO)
        │   mapping rate, FastQC, completeness — logged, never faked
        v
DESeq2 (scripts/deseq2.R — stub, wire after first real run)
        └── results/deseq2/results.tsv + volcano.pdf
Every run, success or failure, is a row in agent/runs.db
(id, samples, contrast, status, wall_time_s, resources, qc_pass, error).
```

## What is done (Week 1) vs TODO (Weeks 2–3)

Done in this scaffold:
- Nextflow DSL2 pipeline: FastQC → Salmon quant → MultiQC, with
  docker / singularity / standard profiles and a `test` profile.
- Test samplesheet: clean paired-end 3v3 yeast subset from
  nf-core/test-datasets (WT vs RAP1_IAA_30M, 50k reads/file).
  See `data/README.md` for provenance.
- Data-fetch script for the test data (~27 MB).
- Agent skeleton: FastAPI app, spec validation, Nextflow launcher,
  SQLite run log, failure classification + retry decision function.
- DESeq2 R script (stub — logic written, not yet run on real quant.sf).
- QC-gates design doc with thresholds marked as starting points.
- Smoke test for tools / structure / Python + shell syntax.
- **First real run completed 2026-11-16** on a MacBook Air,
  Nextflow 26.10.6 + Docker: SALMON_INDEX 1/1, FASTQC 6/6,
  SALMON_QUANT 6/6, MULTIQC 1/1, `AutoRNA pipeline finished:
  SUCCESS`, 6 `quant.sf` files, report at
  `results/test/multiqc/multiqc_report.html`. No mapping-rate
  or runtime numbers are claimed here — check the report /
  Salmon `meta_info.json` per sample before quoting any.

TODO — Week 2:
- LLM planner: natural-language → structured spec via tool calling,
  validated by `parse_job_spec()` before anything launches.
  (Currently raises `NotImplementedError` — do not claim it works.)
- Human genome wiring: human transcriptome/index + the airway
  3v3 dataset (template in `data/`, GSE must be resolved first).

TODO — Week 3:
- QC-gate parser: read `percent_mapped` from Salmon
  `meta_info.json` + MultiQC data, set `qc_pass` in SQLite.
- Auto-retry once on OOM with more memory, logged with attempt #.
- HTML report + README demo (commands + one real run's outputs).

Explicitly NOT in scope: an ML runtime predictor, a large TCGA
run, cloud (AWS Batch) before the local run is stable, and any
PSC data (this project uses public data and needs no PSC approval).

## Setup

Prerequisites (install on your machine — none are installed here):
- Nextflow >= 23.04 (needs Java): https://www.nextflow.io/docs/latest/install.html
- Docker (or Singularity/Apptainer on HPC)
- Python >= 3.10, R + Bioconductor `tximport` + `DESeq2` (for the DE step only)

```bash
git clone https://github.com/riverzzq/autorna-agent.git
cd autorna-agent
pip install -r requirements.txt
bash scripts/smoke_test.sh   # checks tools, structure, syntax
```

## Run the test (Week 1, step by step)

```bash
# 1. Fetch the small yeast test data (~27 MB) into data/ (gitignored)
bash scripts/fetch_test_data.sh

# 2. Run the pipeline with Docker (or -profile test,singularity on HPC)
nextflow run main.nf -profile test,docker --outdir results/test

# 3. Check the outputs — do not trust a run without these
ls results/test/salmon/*/quant.sf
open results/test/multiqc/multiqc_report.html   # or xdg-open on Linux

# 4. Start the agent API (separate terminal)
uvicorn agent.app:app --reload --port 8000
curl http://localhost:8000/health
# Submit a structured job spec (example in agent/app.py docstring area;
# see also runs/<id>/job_spec.json after creation):
curl -X POST http://localhost:8000/jobs -H 'Content-Type: application/json' -d @- <<'JSON'
{
  "samples": [
    {"sample": "SRR6357070", "group": "WT", "fastq_1": "data/test/SRR6357070_1.fastq.gz", "fastq_2": "data/test/SRR6357070_2.fastq.gz"},
    {"sample": "SRR6357076", "group": "RAP1_IAA_30M", "fastq_1": "data/test/SRR6357076_1.fastq.gz", "fastq_2": "data/test/SRR6357076_2.fastq.gz"}
  ],
  "contrast": "RAP1_IAA_30M_vs_WT",
  "genome": "saccharomyces_cerevisiae_R64",
  "profile": "docker"
}
JSON
# Then: curl -X POST http://localhost:8000/jobs/<run_id>/launch
# And:  curl http://localhost:8000/jobs/<run_id>
```

Notes:
- The `test` profile already sets `--input`,
  `--transcriptome`, `--gtf`, and `--outdir`; passing
  `--outdir results/test` again is harmless.
- On macOS / Windows Docker Desktop, if the `-u $(id -u):$(id -g)`
  run option in `nextflow.config` causes permission errors, remove
  that line and rerun.
- On HPC (Slurm/Singularity), use `-profile test,singularity` and
  set `NXF_SINGULARITY_CACHEDIR` to a writable directory if needed.
  Do not run the Docker profile on a shared HPC login node.

## DESeq2 (after the first real run)

```bash
Rscript scripts/deseq2.R --quant_dir results/test/salmon \
  --samples data/samplesheet_test_3v3.csv \
  --gtf data/reference/genes.gtf.gz \
  --contrast RAP1_IAA_30M_vs_WT --outdir results/test/deseq2
```

The script stops loudly if any `quant.sf` is missing or if the
tx2gene mapping parses to zero rows. Spot-check the tx2gene mapping
and the QC gates (docs/qc-gates.md) before using any results table.

## Real data (next step, not Week 1)

The human airway dexamethasone 3v3 dataset is a template only:
`data/samplesheet_airway_template.csv`. Before using it, resolve
the GEO accession with `pysradb` from the SRR IDs — do not invent
a GSE number — and use `scripts/fetch_airway_data.sh` only after
that resolution. The human Salmon index build is much heavier
than the yeast test; do not start it until the yeast test passes.

## Repo hygiene

- Do NOT commit `results/`, `work/`, `runs/`, `data/test/`,
  `data/reference/`, `data/airway/`, or `agent/runs.db` — see
  `.gitignore`. Data is fetched by script, not stored in git.
- First real run completed 2026-11-16 (see above), so pushing
  to GitHub is now OK. Keep the stub warnings honest: the LLM
  planner and DESeq2 are still TODO until they have run for real.
- No QC numbers, runtimes, or results are claimed anywhere in
  this repo until a real run produces them.
