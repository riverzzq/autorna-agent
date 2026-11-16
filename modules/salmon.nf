/*
 * Salmon modules — AutoRNA Agent, Week 1
 *
 * SALMON_INDEX builds the index from the transcriptome FASTA.
 * SALMON_QUANT quantifies each sample with --validateMappings.
 *
 * Version note: nf-core/test-datasets ships pre-built indexes, but
 * salmon.tar.gz is Salmon 1.x (pufferfish) format and
 * salmon_2.11.0.tar.gz is Salmon 2.x format — they are NOT
 * interchangeable. Building the index here avoids that mismatch.
 * The container below pins Salmon 1.10.1; if you switch to Salmon 2.x,
 * update the container AND the index in the same change.
 */
process SALMON_INDEX {
    tag "salmon_index"
    label 'process_low'

    container 'quay.io/biocontainers/salmon:1.10.1--h7e5ed60_0'

    publishDir "${params.outdir}/salmon_index", mode: 'copy'

    input:
    path(transcriptome)

    output:
    path("salmon_index"), emit: index
    path "versions.yml",  emit: versions

    script:
    """
    salmon index -t $transcriptome -i salmon_index -k 31

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        salmon: \$(salmon --version | sed 's/salmon //')
    END_VERSIONS
    """
}

process SALMON_QUANT {
    tag "$meta.id"
    label 'process_medium'

    container 'quay.io/biocontainers/salmon:1.10.1--h7e5ed60_0'

    publishDir "${params.outdir}/salmon", mode: 'copy'

    input:
    tuple val(meta), path(reads)
    path(index)

    output:
    tuple val(meta), path("${meta.id}"), emit: quant_dir
    path "versions.yml",                  emit: versions

    script:
    """
    salmon quant \\
        -i $index \\
        -l A \\
        -1 ${reads[0]} \\
        -2 ${reads[1]} \\
        -p ${task.cpus} \\
        --validateMappings \\
        -o ${meta.id}

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        salmon: \$(salmon --version | sed 's/salmon //')
    END_VERSIONS
    """
}
