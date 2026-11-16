/*
 * FASTQC module — AutoRNA Agent, Week 1
 * Container follows the nf-core module convention (Biocontainers).
 * When no docker/singularity profile is active, Nextflow runs the
 * locally installed `fastqc` instead; install it or use a profile.
 */
process FASTQC {
    tag "$meta.id"
    label 'process_low'

    container 'quay.io/biocontainers/fastqc:0.12.1--hdfd78af_0'

    publishDir "${params.outdir}/fastqc", mode: 'copy'

    input:
    tuple val(meta), path(reads)

    output:
    path("*_fastqc.zip"),  emit: zip
    path("*_fastqc.html"), emit: html
    path "versions.yml",   emit: versions

    script:
    """
    fastqc -q -t ${task.cpus} ${reads.join(' ')}

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        fastqc: \$(fastqc --version | sed 's/FastQC v//')
    END_VERSIONS
    """
}
