# Data — provenance

No FASTQ or reference files are committed to this repo. They are
downloaded by `scripts/fetch_test_data.sh` into `data/` (gitignored).

## Test data (Week 1) — verified 2026-11-10

Source: `nf-core/test-datasets`, branch `rnaseq`
(`https://github.com/nf-core/test-datasets/tree/rnaseq`).

- Organism: *Saccharomyces cerevisiae*, 101 bp paired-end,
  strand-specific, subsampled to 50,000 reads per file.
- Origin paper: Wu et al., Mol Cell 2018 (Rap1-AID degron).
  GEO series for the full study is GSE110004 (per the repo README).
- Files used here (12 files, ~2.2 MB each, ~27 MB total):
  `testdata/GSE110004/SRR6357070_1.fastq.gz` … `SRR6357081_2.fastq.gz`
  This scaffold uses a clean paired-end 3v3 subset:
  WT = SRR6357070, SRR6357071, SRR6357072;
  RAP1_IAA_30M = SRR6357076, SRR6357077, SRR6357078.
  See `samplesheet_test_3v3.csv`.
- Reference files (same repo, `reference/`):
  `transcriptome.fasta.gz`, `genes.gtf.gz`
  (S. cerevisiae R64-1-1 Ensembl release, subsampled).
  A pre-built Salmon index also exists in that repo
  (`reference/salmon.tar.gz`, Salmon 1.x format;
  `reference/salmon_2.11.0.tar.gz`, Salmon 2.x format).
  This pipeline builds its own index by default to avoid a
  Salmon version mismatch — see `main.nf`.
- The upstream repo's own samplesheet (`samplesheet/samplesheet.csv`)
  uses a different schema (`group,replicate,fastq_1,fastq_2,...`) and
  mixes paired- and single-end rows. This project's samplesheet
  schema is `sample,group,fastq_1,fastq_2,strandedness`, paired-end
  only in Week 1. Do not copy the upstream samplesheet verbatim.

## Real data (Week 2/3) — NOT yet verified for this project

Human airway smooth muscle, dexamethasone vs untreated.
See `samplesheet_airway_template.csv` — it is a template with
placeholder paths. Resolve the GSE accession with `pysradb`
before downloading anything; do not invent a GSE number.
Full-size human FASTQ files are ~20–28M reads per sample and the
human Salmon index build is much heavier than the yeast test —
do not start it until the yeast test passes end to end.
