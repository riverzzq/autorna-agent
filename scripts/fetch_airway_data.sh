#!/usr/bin/env bash
# TEMPLATE — human airway 3v3 data fetch. DO NOT RUN BLINDLY.
#
# Steps before running:
#   1. Install sra-tools (prefetch, fasterq-dump) and pysradb.
#   2. Resolve the GEO accession for these SRRs — do NOT invent a GSE:
#        pysradb sra-to-gsm SRR1039508
#        pysradb gse-to-srr <GSE> --saveto airway_srr.tsv
#      Fill in the SRR list below from that output (untreated vs dex,
#      3-4 cell lines each). The SRRs named here are only the examples
#      from the prep file; the full list must come from pysradb.
#   3. Check disk space: full human FASTQ files are ~20-28M reads each.
#      6 samples x 2 files is several GB, plus a human Salmon index
#      build. Do not start this until the yeast test passes end to end.
#
# This script currently STOPS after the checklist on purpose.
# Remove the `exit 1` below only after steps 1-3 are done and the
# SRR list has been filled in from pysradb output.
set -euo pipefail

echo "STOP: resolve the GSE accession with pysradb first (see header)."
echo "Then fill in SRR_LIST below from pysradb output and remove the exit."
exit 1

# --- Fill in after pysradb resolution ---
# SRR_LIST="SRR1039508 SRR1039509 ..."
# mkdir -p data/airway
# for srr in $SRR_LIST; do
#     prefetch "$srr" --output-directory data/airway/sra
#     fasterq-dump "data/airway/sra/${srr}/${srr}.sra" \
#         --outdir data/airway --gzip --split-files --threads 4
# done
# ls -lh data/airway
