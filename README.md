# AutoRNA Agent

**Problem:** RNA-seq pipelines
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
Natural-language request (LLM planner done: OpenAI gpt-4o-mini + rule fallback, response shows "parser": "llm" or "rule")
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
QC gates (agent/qc.py, GET /jobs/{id}/qc; parser done, verified on real data)
        │   mapping rate, FastQC, completeness — logged, never faked
        v
DESeq2 (scripts/deseq2.R + agent/deseq2.py, POST /jobs/{id}/deseq2; verified on real data)
        └── results/deseq2/results.tsv + volcano.pdf
Every run, success or failure, is a row in agent/runs.db
(id, samples, contrast, status, wall_time_s, resources, qc_pass, error).
```

## What is done

Done:
- Nextflow DSL2 pipeline: FastQC → Salmon quant → MultiQC, with
  docker / singularity / standard profiles and a `test` profile.
- Test samplesheet: clean paired-end 3v3 yeast subset from
  nf-core/test-datasets (WT vs RAP1_IAA_30M, 50k reads/file).
  See `data/README.md` for provenance.
- Data-fetch script for the test data (~27 MB).
- Agent skeleton: FastAPI app, spec validation, Nextflow launcher,
  SQLite run log, failure classification + retry decision function.
- DESeq2 R script (scripts/deseq2.R + agent/deseq2.py): wired to the
  agent and verified on real data 2026-11-18 — run-9c783ca0, 124 genes,
  124 tx2gene pairs, 1 significant, top 5 all downregulated.
- QC-gates design doc with thresholds marked as starting points.
- Smoke test for tools / structure / Python + shell syntax.
- **First real run completed 2026-11-16** on a MacBook Air,
  Nextflow 26.10.6 + Docker: SALMON_INDEX 1/1, FASTQC 6/6,
  SALMON_QUANT 6/6, MULTIQC 1/1, `AutoRNA pipeline finished:
  SUCCESS`, 6 `quant.sf` files, report at
  `results/test/multiqc/multiqc_report.html`. No mapping-rate
  or runtime numbers are claimed here — check the report /
  Salmon `meta_info.json` per sample before quoting any.
- **Agent end-to-end (Week 2, done for rule-based, LLM added):**
  FastAPI `/health` returns ok; POST /jobs creates a queued run in
  SQLite and POST /jobs/{id}/launch runs Nextflow in the background.
  Natural-language entry POST /jobs/natural-language accepts e.g.
  `{"text": "run the yeast test"}` / `{"text": "跑酵母测试"}`.
  With OPENAI_API_KEY set it tries the OpenAI LLM first (gpt-4o-mini,
  tool calling, output validated by parse_job_spec()); without a key,
  or if the LLM fails, it falls back to the rule-based parser, which
  only knows the yeast 3v3 case. The response includes
  `"parser": "llm"` or `"rule"` so you can see which one parsed it.
  Verified: run-ee0f4604 (structured, 42.0 s, succeeded) and
  run-e90e6b98 (rule-based natural language, 40.1 s, succeeded).
  To enable the LLM: create a key at platform.openai.com, then in the
  terminal that runs uvicorn, `export OPENAI_API_KEY='sk-...'`
  (never paste the key in chat, never commit it; .env is git-ignored).
  Model override: OPENAI_MODEL, default gpt-4o-mini.

Week 3 — done:
- [done, step 1] QC-gate parser: agent/qc.py reads percent_mapped
  from Salmon meta_info.json + NumReads summed from quant.sf +
  MultiQC general-stats rows; GET /jobs/{id}/qc returns the real
  numbers and records qc_pass (1/0) in SQLite; a successful launch
  also parses QC automatically. Tested locally: low-mapping sample
  fails alone, all-~80% passes, missing quant.sf and zero reads fail.
- [done, step 2] Auto-retry once on OOM: when a run fails with
  error_kind=oom on attempt 0, the agent relaunches it once with
  retry.config bumping every memory tier (single 2->4 GB, low 4->8
  GB, medium 8->16 GB) via Nextflow `-c`; both attempts' logs are
  kept and the attempt is recorded in the run's resources JSON.
  Any other failure kind, or a second OOM, stops and needs a
  human decision. A manual relaunch resets the attempt counter.
- [done, step 3] DESeq2 (agent/deseq2.py, POST /jobs/{id}/deseq2):
  runs scripts/deseq2.R on the run's real quant.sf files, only for
  QC-passed samples; failed samples are named in
  deseq2/excluded_samples.json. Pre-checks before R starts: GTF
  yields >= 1 transcript_id/gene_id pair, and each contrast group
  keeps >= 2 QC-passed replicates. Results parsed into
  deseq2/summary.json (gene count, significant count, top 5 by
  padj). Tested locally with a stub Rscript: all-pass, one-sample
  excluded, too-few-replicates refused, bad GTF refused.
  First real R run done 2026-11-18 on run-9c783ca0: 124 genes, 124
  tx2gene pairs, 1 significant (padj < 0.05 and |log2FC| > 1), top 5
  by padj YAR009C, YAR010C, YAL038W, YAL003W, YAL037C-B, all
  downregulated. Only 124 genes because the subsampled test reference
  contains only 124 transcripts.
- [done, step 4] Per-run HTML report (agent/report.py,
  GET /jobs/{id}/report): run status, QC table, DESeq2 summary
  when present, excluded samples, links to the MultiQC report
  and results.tsv. All numbers come from qc.json /
  deseq2/summary.json on disk; nothing is invented.
- Human genome wiring: human transcriptome/index + the airway
  3v3 dataset (template in `data/`, GSE must be resolved first).

Explicitly NOT in scope: an ML runtime predictor, a large TCGA
run, cloud (AWS Batch) before the local run is stable, and any
PSC data (this project uses public data and needs no PSC approval).

## Web UI (no terminal needed)

Start the server as usual (`uvicorn agent.app:app --reload --port 8000`),
then open in a browser:

- **http://localhost:8000/ui** — the AutoRNA page: type a request in
  words, create a run, launch it, check QC and DESeq2, and open the
  report, all by clicking. No terminal, no curl. It also has an
  "Understand a new dataset" section: give a FASTQ folder path or
  SRA run IDs, see the profile (layout, read length, organism,
  inferred groups) and the questions it raises, fix the groups in
  the table, and create a run from the proposal.
- **http://localhost:8000/docs** — the auto-generated API docs: every
  endpoint with a "Try it out" button.

The UI is a single file, `agent/static/index.html` (plain HTML/JS,
no build step, no new dependency); it calls the same API the
terminal commands use, so the page and the terminal always agree.

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

## Dataset understanding (Step 1 — more agent-like)

`agent/dataset.py`, `POST /datasets/profile`, and the "Understand a
new dataset" section of the UI. Give a local FASTQ folder and/or SRA
run IDs; the agent profiles first and asks instead of guessing:

- local folder: pairs _1/_2 files, detects paired vs single-end,
  samples read length, infers groups from file names (dex_rep1 ->
  dex; plain numeric IDs like SRR6357070 are left alone, not
  merged);
- SRA IDs: real metadata from the ENA API (organism, layout, sample
  title). Verified 2026-11-18: the 5 airway SRRs are all Homo
  sapiens, PAIRED, groups untreated / Dex / Alb / Alb_Dex — a
  2-factor design, so no single contrast is proposed and the agent
  asks which contrast to run. An unknown ID is reported as not
  found; the GEO accession is never invented.
- strandedness is not guessed: Salmon already runs with `-l A`
  (auto-detect), so the samplesheet value does not change the run.
- human reference (GRCh38) is NOT wired yet: a human proposal stops
  with a clear message. Automatic reference download is Step 2.

R is NOT installed by this project. Check first:

```bash
Rscript --version
Rscript -e 'library(tximport); library(DESeq2); message("ok")'
```

If either fails, install R (https://cran.r-project.org, macOS: the
.pkg installer), then in R:

```r
if (!requireNamespace("BiocManager", quietly = TRUE)) install.packages("BiocManager")
BiocManager::install(c("tximport", "DESeq2"))
```

Then trigger DESeq2 for a run (background thread; R can take minutes):

```bash
curl -X POST http://localhost:8000/jobs/run-9c783ca0/deseq2
curl http://localhost:8000/jobs/run-9c783ca0/deseq2   # summary when done
curl http://localhost:8000/jobs/run-9c783ca0/report  # one-page HTML report
```

Only QC-passed samples are used; failed samples are named in
deseq2/excluded_samples.json. The first real R run was spot-checked 2026-11-18 (run-9c783ca0:
tx2gene 124 pairs, 124 genes, 1 significant).

Manual equivalent (same script, same checks):

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
  to GitHub is now OK. The LLM planner and DESeq2 have both run for
  real (run-9c783ca0, 2026-11-18) — keep every number in this README
  tied to a real run.
- No QC numbers, runtimes, or results are claimed anywhere in
  this repo until a real run produces them.
