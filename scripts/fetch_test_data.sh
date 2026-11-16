#!/usr/bin/env bash
# Fetch the Week 1 test data (small yeast RNA-seq, ~27 MB total).
# Source: nf-core/test-datasets, branch `rnaseq`
#   https://github.com/nf-core/test-datasets/tree/rnaseq
# Verified file list 2026-11-10 via the GitHub API (repo tree).
# Idempotent: skips files that already exist and are non-empty.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="https://raw.githubusercontent.com/nf-core/test-datasets/rnaseq"

mkdir -p "$ROOT/data/test" "$ROOT/data/reference"

fetch() {
    local url="$1" dest="$2"
    if [ -s "$dest" ]; then
        echo "skip (exists): $dest"
        return 0
    fi
    echo "fetch: $url -> $dest"
    curl -fL --retry 2 -o "$dest" "$url"
}

# Paired-end 3v3 subset: WT SRR6357070-72, RAP1_IAA_30M SRR6357076-78.
# Each file is subsampled to 50,000 reads (~2.2 MB).
for srr in SRR6357070 SRR6357071 SRR6357072 SRR6357076 SRR6357077 SRR6357078; do
    fetch "$BASE/testdata/GSE110004/${srr}_1.fastq.gz" "$ROOT/data/test/${srr}_1.fastq.gz"
    fetch "$BASE/testdata/GSE110004/${srr}_2.fastq.gz" "$ROOT/data/test/${srr}_2.fastq.gz"
done

# Yeast reference (S. cerevisiae R64-1-1, subsampled).
fetch "$BASE/reference/transcriptome.fasta.gz" "$ROOT/data/reference/transcriptome.fasta.gz"
fetch "$BASE/reference/genes.gtf.gz" "$ROOT/data/reference/genes.gtf.gz"

echo "---"
echo "Downloaded files:"
ls -lh "$ROOT/data/test" "$ROOT/data/reference"
echo "---"
echo "Next: bash scripts/smoke_test.sh"
echo "Then: nextflow run main.nf -profile test,docker --outdir results/test"
