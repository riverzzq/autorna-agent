#!/usr/bin/env Rscript
# AutoRNA Agent — DESeq2 (Week 3, wired via POST /jobs/{run_id}/deseq2)
#
# STATUS: wired into the agent (agent/deseq2.py). First real run must
# still be spot-checked: tx2gene row count, counts, and the
# significant-gene count, before trusting any result table.
# The agent runs this only on QC-passed samples (agent/qc.py) and
# refuses when a group would be left with fewer than 2 replicates.
# This script fails loudly if inputs are missing; it never
# fabricates results.
#
# Inputs:
#   --quant_dir  directory containing <sample>/quant.sf per sample
#                (pipeline output: results/salmon/)
#   --samples    samplesheet CSV (sample,group,fastq_1,fastq_2,...)
#   --gtf        genes.gtf (for transcript -> gene mapping)
#   --contrast   e.g. "RAP1_IAA_30M_vs_WT"  (<groupB>_vs_<groupA>)
#   --outdir     output directory (default: results/deseq2)
#
# Outputs:
#   results.tsv       — DESeq2 results (gene, baseMean, log2FoldChange,
#                       lfcSE, stat, pvalue, padj), sorted by padj
#   volcano_data.tsv  — same table, for plotting
#   volcano.pdf       — base-R volcano plot (no extra deps beyond DESeq2/tximport)
#   session_info.txt  — R + package versions, for reproducibility
#
# Requires (install once): BiocManager::install(c("tximport", "DESeq2"))
# Usage:
#   Rscript scripts/deseq2.R --quant_dir results/salmon \
#     --samples data/samplesheet_test_3v3.csv \
#     --gtf data/reference/genes.gtf.gz \
#     --contrast RAP1_IAA_30M_vs_WT --outdir results/deseq2

args <- commandArgs(trailingOnly = TRUE)
get_arg <- function(name, default = NULL) {
  hit <- grep(paste0("^--", name, "="), args, value = TRUE)
  if (length(hit) == 0) return(default)
  sub(paste0("^--", name, "="), "", hit[1])
}

quant_dir <- get_arg("quant_dir")
samples_csv <- get_arg("samples")
gtf_file <- get_arg("gtf")
contrast <- get_arg("contrast")
outdir <- get_arg("outdir", "results/deseq2")

if (is.null(quant_dir) || is.null(samples_csv) || is.null(gtf_file) || is.null(contrast)) {
  stop("Missing required args. See header: --quant_dir, --samples, --gtf, --contrast are required.")
}
if (!grepl("_vs_", contrast, fixed = TRUE)) {
  stop("contrast must look like '<groupB>_vs_<groupA>', e.g. 'RAP1_IAA_30M_vs_WT'")
}
parts <- strsplit(contrast, "_vs_", fixed = TRUE)[[1]]
group_b <- parts[1]; group_a <- parts[2]

suppressPackageStartupMessages({
  library(tximport)
  library(DESeq2)
})

samples <- read.csv(samples_csv, stringsAsFactors = FALSE)
if (!all(c("sample", "group") %in% names(samples))) {
  stop("samplesheet must have 'sample' and 'group' columns")
}
if (!all(c(group_a, group_b) %in% unique(samples$group))) {
  stop(paste0("contrast groups '", group_a, "' / '", group_b,
              "' must both appear in the samplesheet groups: ",
              paste(unique(samples$group), collapse = ", ")))
}

# --- tx2gene from GTF (transcript_id -> gene_id) -------------------------
# NOTE: verify this parsing against YOUR gtf before trusting output.
# Yeast test GTF (Ensembl R64) uses gene_id / transcript_id attributes;
# human GRCh38 GTF uses the same attribute names, but always spot-check
# a few rows. If the regex finds no transcript_id, stop — do not guess.
gtf_lines <- readLines(gzfile(gtf_file), warn = FALSE)
gtf_lines <- gtf_lines[grepl("\ttranscript\t", gtf_lines)]
get_attr <- function(lines, attr) {
  m <- regmatches(lines, regexec(paste0(attr, ' "([^"]+)"'), lines))
  vapply(m, function(x) if (length(x) >= 2) x[2] else NA_character_, character(1))
}
tx2gene <- data.frame(
  TXNAME = get_attr(gtf_lines, "transcript_id"),
  GENEID = get_attr(gtf_lines, "gene_id"),
  stringsAsFactors = FALSE
)
tx2gene <- unique(tx2gene[!is.na(tx2gene$TXNAME) & !is.na(tx2gene$GENEID), ])
if (nrow(tx2gene) == 0) {
  stop("Could not parse transcript_id/gene_id from GTF — check the attribute format, do not proceed.")
}
message(paste0("tx2gene: ", nrow(tx2gene), " transcripts mapped to genes"))

# --- Import Salmon quant.sf files ----------------------------------------
quant_files <- file.path(quant_dir, samples$sample, "quant.sf")
names(quant_files) <- samples$sample
missing <- quant_files[!file.exists(quant_files)]
if (length(missing) > 0) {
  stop(paste0("Missing quant.sf files — run the pipeline first: ",
              paste(missing, collapse = ", ")))
}
txi <- tximport(quant_files, type = "salmon", tx2gene = tx2gene)

# --- DESeq2 (pseudobulk-style: one count per gene per sample) ------------
# NOTE: this is gene-level DE on Salmon gene-summed counts. It is NOT
# per-cell DE and it is NOT a substitute for checking QC gates first
# (docs/qc-gates.md). If any sample failed QC, exclude it and re-run.
coldata <- data.frame(
  row.names = samples$sample,
  group = factor(samples$group, levels = c(group_a, group_b))
)
dds <- DESeqDataSetFromTximport(txi, colData = coldata, design = ~ group)
dds <- DESeq(dds)
res <- results(dds, contrast = c("group", group_b, group_a))
res_df <- as.data.frame(res)
res_df$gene <- rownames(res_df)
res_df <- res_df[order(res_df$padj), ]

dir.create(outdir, showWarnings = FALSE, recursive = TRUE)
write.table(res_df, file.path(outdir, "results.tsv"),
            sep = "\t", quote = FALSE, row.names = FALSE)
write.table(res_df, file.path(outdir, "volcano_data.tsv"),
            sep = "\t", quote = FALSE, row.names = FALSE)

# --- Volcano plot (base R, no extra package) ------------------------------
pdf(file.path(outdir, "volcano.pdf"), width = 7, height = 6)
with(res_df, plot(log2FoldChange, -log10(pvalue),
                  pch = 20, cex = 0.6, col = "grey60",
                  xlab = "log2 fold change", ylab = "-log10 p-value",
                  main = contrast))
sig <- !is.na(res_df$padj) & res_df$padj < 0.05 & abs(res_df$log2FoldChange) > 1
with(res_df[sig, ], points(log2FoldChange, -log10(pvalue), pch = 20, cex = 0.6, col = "firebrick"))
abline(h = -log10(0.05), lty = 2, col = "grey40")
dev.off()

# --- Reproducibility -------------------------------------------------------
sink(file.path(outdir, "session_info.txt"))
print(sessionInfo())
sink()

message(paste0("Done. ", sum(sig, na.rm = TRUE), " genes at padj<0.05 & |log2FC|>1 (of ",
               nrow(res_df), " tested). Outputs in ", outdir))
