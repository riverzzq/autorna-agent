# QC gates + failure handling — AutoRNA Agent

## Principle

A run is not "done" because Nextflow exited 0. It is done when
(1) the pipeline finished, (2) every sample passed the QC gates
below, and (3) the run is logged in SQLite with its wall time,
resources, and QC outcome. A failed gate is a **failed run for that
sample**, logged as such — never a silent pass, never a faked number.

All thresholds below are **starting points, not validated cutoffs**.
They must be tuned after the first real runs and the reason for any
change recorded in the run log / README. Do not present them as
established standards.

## Gates (applied after MultiQC, Week 3 parser — see status)

| # | Gate | Source | Starting threshold | Fail action |
|---|------|--------|--------------------|-------------|
| 1 | Pipeline exit code | Nextflow | exit code 0 | Run = failed. Classify error (see below), log it. |
| 2 | FASTQ present + non-empty | samplesheet / fetch step | both mates exist, size > 0 | Do not launch. Fix the samplesheet / re-download. |
| 3 | FastQC per-base quality | `multiqc_report_data/multiqc_fastqc.txt` | median Phred >= 20 for most positions; flag, don't auto-fail, on adapter content alone | Flag in report; if a sample is far below the rest, exclude it and note why. |
| 4 | Salmon mapping rate | Salmon `quant.sf` / logs (`meta_info.json`: `percent_mapped`) | >= 50% (`params.min_mapping_rate`, default 0.5) | Sample = failed QC. Do not include it in DESeq2. Investigate: wrong reference? strandedness? contamination? |
| 5 | Salmon assigned fragments | `quant.sf` NumReads summed | > 0 and within ~10x of the other samples in the same run | Sample = failed QC if 0 or an extreme outlier. Check the FASTQ / index. |
| 6 | MultiQC general stats complete | `multiqc_report_data/` | one row per sample, no missing sample | Missing row = investigate that sample's logs before trusting the report. |
| 7 | DESeq2 input sanity (Week 3) | `scripts/deseq2.R` | quant.sf exists for every sample in the contrast; tx2gene parsed > 0 rows | Script stops loudly (it already does). Do not hand-edit a results table to fill gaps. |

### How to read the mapping rate (do not guess it)

Salmon writes `meta_info.json` next to each `quant.sf` with
`percent_mapped`. The Week 3 QC parser must read that file, not
infer a rate from file size. Until the parser exists, check it by
hand per sample:
`python3 -c "import json; print(json.load(open('results/salmon/<sample>/aux_info/meta_info.json'))['percent_mapped'])"`

## Failure classification (implemented in `agent/runner.py`)

`classify_error()` scans the tail of `nextflow.log` (kept at
`runs/<run_id>/nextflow.log`) for markers and returns one kind:

- `oom` — "out of memory", "killed", "exit code 137", "memory limit"
- `timeout` — "timeout", "exceeded time limit"
- `file_not_found` — "no such file", "checkIfExists"
- `container` — docker/singularity/image errors
- `unknown` — anything else (read the log; do not guess)

These are triage heuristics, not a diagnosis. The raw log is
always kept.

## Retry rules (Week 1: decision only — see below)

`should_retry(error_kind, attempt)` in `agent/runner.py`:

- OOM, first attempt only (`attempt == 0`) → retry is **recommended**,
  with more memory. Week 1 does **not** auto-retry; the API logs
  `retry_recommended=true` on the failed run row.
- Timeout → no auto-retry. Check whether the job actually needs
  more time or is stuck (e.g. wrong reference, infinite loop).
- File not found / container → no retry. Fix the input or the
  container setup and resubmit.
- Unknown → no retry. Read the log first.

### Week 3 TODO (auto-retry, to implement, not to fake)

1. On OOM retry: relaunch the failed sample(s) with the next
   memory tier (e.g. 8 GB → 16 GB), record `attempt` and the new
   memory in the SQLite row, and keep both attempts' logs.
2. On QC-gate failure (mapping rate): do NOT retry with more
   memory — that does not fix a wrong reference. Log the sample
   as `qc_pass=0` and exclude it from DESeq2.
3. Never retry more than once automatically. A second failure
   needs a human decision, recorded in the run log.

## What a "passed" run looks like (for the README / interview)

- Nextflow exit 0, MultiQC report exists at
  `results/multiqc/multiqc_report.html`
- One `quant.sf` per sample under `results/salmon/<sample>/`
- Every sample's `percent_mapped` >= the threshold in force,
  recorded in SQLite (`qc_pass=1`)
- DESeq2 `results.tsv` exists only if all contrast samples passed
  QC; otherwise the DESeq2 step is skipped and the reason is logged.
