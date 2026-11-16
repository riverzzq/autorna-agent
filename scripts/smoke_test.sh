#!/usr/bin/env bash
# AutoRNA Agent — Week 1 smoke test.
# Checks tools, file structure, Python syntax, and shell syntax.
# It does NOT run the pipeline and does NOT download data.
# Run: bash scripts/smoke_test.sh
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
FAIL=0

check() { # check <label> <command...>
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then echo "OK   $label"; else echo "FAIL $label"; FAIL=1; fi
}
warn() { # warn <label> <command...>
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then echo "OK   $label"; else echo "WARN $label (needed on the run machine, not fatal here)"; fi
}

echo "== tools =="
warn "nextflow installed"    bash -c 'command -v nextflow >/dev/null'
warn "docker installed"      bash -c 'command -v docker >/dev/null'
warn "singularity/apptainer" bash -c 'command -v singularity >/dev/null || command -v apptainer >/dev/null'
check "python3 installed"    bash -c 'command -v python3 >/dev/null'
warn "Rscript installed"     bash -c 'command -v Rscript >/dev/null'
warn "curl installed"        bash -c 'command -v curl >/dev/null'

echo "== files =="
for f in main.nf nextflow.config README.md requirements.txt .gitignore \
         modules/fastqc.nf modules/salmon.nf modules/multiqc.nf \
         agent/app.py agent/planner.py agent/runner.py agent/db.py \
         scripts/deseq2.R scripts/fetch_test_data.sh \
         data/samplesheet_test_3v3.csv data/samplesheet_airway_template.csv \
         docs/qc-gates.md; do
    check "exists: $f" test -f "$f"
done

echo "== syntax =="
check "python: agent/app.py compiles"    python3 -m py_compile agent/app.py
check "python: agent/planner.py compiles" python3 -m py_compile agent/planner.py
check "python: agent/runner.py compiles"  python3 -m py_compile agent/runner.py
check "python: agent/db.py compiles"      python3 -m py_compile agent/db.py
check "bash: fetch_test_data.sh parses"   bash -n scripts/fetch_test_data.sh
check "bash: fetch_airway_data.sh parses" bash -n scripts/fetch_airway_data.sh
check "bash: smoke_test.sh parses"        bash -n scripts/smoke_test.sh

echo "== data (expected after fetch) =="
if [ -s data/test/SRR6357070_1.fastq.gz ] && [ -s data/reference/transcriptome.fasta.gz ]; then
    echo "OK   test data present (run: nextflow run main.nf -profile test,docker --outdir results/test)"
else
    echo "INFO test data not present yet — run: bash scripts/fetch_test_data.sh"
fi

echo "== nextflow syntax =="
echo "INFO Nextflow syntax is NOT validated by this script on machines"
echo "     without Nextflow installed. On your machine run:"
echo "       nextflow run main.nf -profile test,docker --outdir results/test"
echo "     and treat a config/workflow parse error there as a real failure."

if [ "$FAIL" -ne 0 ]; then echo "SMOKE TEST: FAILURES ABOVE"; exit 1; fi
echo "SMOKE TEST: PASS (structure + python + shell syntax)"
