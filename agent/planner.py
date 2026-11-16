"""Job-spec planner — AutoRNA Agent, Week 1.

Two layers, kept separate on purpose:
1. `parse_job_spec()` — validates a STRUCTURED job spec (dict/JSON).
   This is implemented and tested by the API. It is the Week 1 planner.
2. `parse_natural_language()` — natural-language input
   ("run RNA-seq on these 6 samples, dex vs untreated").
   STUB in Week 1. It raises NotImplementedError. Do not present it
   as working. Week 2 wires it to LLM tool calling that outputs
   exactly the structured spec validated by (1) — the LLM never
   launches a pipeline directly, it only produces a spec that must
   pass validation first.
"""
from dataclasses import dataclass, field

# Genomes the pipeline knows how to handle. Only yeast is wired in
# Week 1 (the test data). Human is listed so the API can reject it
# with a clear message instead of failing halfway through a run.
SUPPORTED_GENOMES = {
    "saccharomyces_cerevisiae_R64": "yeast test data (Week 1, wired)",
    "homo_sapiens_GRCh38": "human (Week 2/3, NOT wired yet)",
}


@dataclass
class JobSpec:
    samples: list  # [{"sample": "SRR6357070", "group": "WT", "fastq_1": "...", "fastq_2": "..."}]
    contrast: str  # e.g. "RAP1_IAA_30M_vs_WT" — "<groupB>_vs_<groupA>"
    genome: str = "saccharomyces_cerevisiae_R64"
    strandedness: str = "reverse"
    outdir: str = "results"
    profile: str = "docker"  # "docker" | "singularity" | "standard"


def parse_job_spec(raw: dict) -> JobSpec:
    """Validate a structured job spec. Raises ValueError on bad input."""
    if not isinstance(raw, dict):
        raise ValueError("job spec must be a JSON object")
    samples = raw.get("samples")
    if not samples or not isinstance(samples, list):
        raise ValueError("job spec needs a non-empty 'samples' list")
    groups = set()
    for s in samples:
        if not isinstance(s, dict):
            raise ValueError("each sample must be an object")
        for key in ("sample", "group", "fastq_1", "fastq_2"):
            if not s.get(key):
                raise ValueError(f"sample {s.get('sample', '?')} is missing '{key}'")
        groups.add(s["group"])
    contrast = raw.get("contrast", "")
    if "_vs_" not in contrast:
        raise ValueError("contrast must look like '<groupB>_vs_<groupA>', e.g. 'RAP1_IAA_30M_vs_WT'")
    group_b, group_a = contrast.split("_vs_", 1)
    if group_b not in groups or group_a not in groups:
        raise ValueError(
            f"contrast groups '{group_a}' / '{group_b}' must both appear in samples; got groups={sorted(groups)}"
        )
    if group_a == group_b:
        raise ValueError("contrast groups must differ")
    genome = raw.get("genome", "saccharomyces_cerevisiae_R64")
    if genome not in SUPPORTED_GENOMES:
        raise ValueError(f"unsupported genome '{genome}'; supported: {sorted(SUPPORTED_GENOMES)}")
    if genome == "homo_sapiens_GRCh38":
        raise ValueError(
            "human genome is NOT wired yet (Week 2/3): no human transcriptome/index "
            "is configured and the airway samplesheet is still a template. "
            "Use saccharomyces_cerevisiae_R64 for the Week 1 test."
        )
    profile = raw.get("profile", "docker")
    if profile not in ("docker", "singularity", "standard"):
        raise ValueError("profile must be one of: docker, singularity, standard")
    return JobSpec(
        samples=samples,
        contrast=contrast,
        genome=genome,
        strandedness=raw.get("strandedness", "reverse"),
        outdir=raw.get("outdir", "results"),
        profile=profile,
    )


def parse_natural_language(text: str) -> JobSpec:
    """STUB (Week 1) — natural-language -> JobSpec via LLM tool calling.

    TODO (Week 2): call an LLM with a tool schema that returns exactly
    the JobSpec fields above, then pass the result through
    parse_job_spec() for validation. Until then this raises.
    """
    raise NotImplementedError(
        "parse_natural_language is a Week 1 stub (TODO, Week 2). "
        "Submit a structured job spec to POST /jobs instead. "
        "Do not claim LLM parsing is live."
    )
