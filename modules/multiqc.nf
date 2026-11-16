/*
 * MULTIQC module — AutoRNA Agent, Week 1
 * Scans the staged FastQC zips and Salmon quant outputs in the
 * process work directory and writes one HTML report + data dir.
 * The agent QC gates (docs/qc-gates.md) are applied AFTER this,
 * by parsing multiqc_report_data/ and the Salmon logs — not by eyeballing
 * the HTML alone.
 */
process MULTIQC {
    tag "multiqc"
    label 'process_single'

    container 'quay.io/biocontainers/multiqc:1.19--pyhdfd78af_0'

    publishDir "${params.outdir}/multiqc", mode: 'copy'

    input:
    path(multiqc_files)

    output:
    path("multiqc_report.html"), emit: report
    path("multiqc_report_data"), emit: data
    path "versions.yml",         emit: versions

    script:
    """
    multiqc . -o . -n multiqc_report.html --force

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        multiqc: \$(multiqc --version | sed 's/multiqc, version //')
    END_VERSIONS
    """
}
