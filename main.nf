#!/usr/bin/env nextflow
/*
 * AutoRNA Agent — Week 1 pipeline (Nextflow DSL2)
 *
 * FastQC -> Salmon quant -> MultiQC.
 *
 * This pipeline is the execution engine only. It does NOT invent a new
 * quantifier and it does NOT replace nf-core/rnaseq. The agent layer
 * (agent/) wraps this pipeline: it validates a job spec, launches it,
 * watches it, retries on known failures, checks QC gates, and logs
 * every run to SQLite. See README.md and docs/qc-gates.md.
 *
 * Samplesheet schema (this project, not nf-core's):
 *   sample,group,fastq_1,fastq_2,strandedness
 * Paired-end only in Week 1. fastq_2 must be present.
 *
 * Usage:
 *   nextflow run main.nf -profile test,docker --outdir results/test
 *   nextflow run main.nf --input data/samplesheet_test_3v3.csv \
 *       --transcriptome data/reference/transcriptome.fasta.gz \
 *       --gtf data/reference/genes.gtf.gz -profile docker
 */
nextflow.enable.dsl = 2

include { FASTQC    } from './modules/fastqc'
include { SALMON_INDEX; SALMON_QUANT } from './modules/salmon'
include { MULTIQC   } from './modules/multiqc'

workflow {
    main:
    // ---- Inputs ---------------------------------------------------------
    if (!params.input) {
        error "Missing required parameter: --input <samplesheet.csv>"
    }
    if (!params.transcriptome) {
        error "Missing required parameter: --transcriptome <transcriptome.fasta(.gz)>"
    }

    reads_ch = Channel
        .fromPath(params.input, checkIfExists: true)
        .splitCsv(header: true)
        .map { row ->
            def meta = [id: row.sample, group: row.group]
            if (!row.sample || !row.fastq_1) {
                error "Samplesheet row is missing 'sample' or 'fastq_1': ${row}"
            }
            if (!row.fastq_2) {
                error "Sample ${row.sample}: Week 1 is paired-end only, 'fastq_2' is missing."
            }
            def fq1 = file(row.fastq_1, checkIfExists: true)
            def fq2 = file(row.fastq_2, checkIfExists: true)
            tuple(meta, [fq1, fq2])
        }

    transcriptome_ch = Channel.fromPath(params.transcriptome, checkIfExists: true)

    // ---- Salmon index: build it, unless a pre-built index is given -----
    // NOTE: nf-core/test-datasets ships pre-built indexes (salmon.tar.gz
    // is Salmon 1.x pufferfish format; salmon_2.11.0.tar.gz is Salmon 2.x).
    // Building the index from the transcriptome avoids a version mismatch
    // and is the default here. Pass --salmon_index only if you have
    // verified it matches your Salmon version.
    if (params.salmon_index) {
        salmon_index_ch = Channel.fromPath(params.salmon_index, checkIfExists: true).first()
    } else {
        SALMON_INDEX(transcriptome_ch)
        salmon_index_ch = SALMON_INDEX.out.index.first()
    }

    // ---- Run ------------------------------------------------------------
    FASTQC(reads_ch)
    SALMON_QUANT(reads_ch, salmon_index_ch)

    // MultiQC scans the FastQC zips and the Salmon quant outputs.
    multiqc_inputs = FASTQC.out.zip
        .mix(SALMON_QUANT.out.quant_dir.map { meta, dir -> dir })
        .collect()
    MULTIQC(multiqc_inputs)

    onComplete:
    log.info "AutoRNA pipeline finished: ${workflow.success ? 'SUCCESS' : 'FAILED'}"
    log.info "Results: ${params.outdir}"
    log.info "MultiQC report: ${params.outdir}/multiqc/multiqc_report.html"
    log.info "Salmon quant.sf files: ${params.outdir}/salmon/<sample>/quant.sf"
    log.info "Next step (Week 2/3): run the agent QC gates in docs/qc-gates.md against the MultiQC data and Salmon logs — do not eyeball one sample and call it done."
}
