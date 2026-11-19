"""Job-spec planner — AutoRNA Agent, Week 2 (rule-based + OpenAI LLM).

Two layers, kept separate on purpose:
1. `parse_job_spec()` — validates a STRUCTURED job spec (dict/JSON).
   This is implemented and tested by the API. It is the Week 1 planner.
2. `parse_natural_language()` — natural-language input
   ("run RNA-seq on these 6 samples, dex vs untreated").
   Week 2, step 1: a RULE-BASED parser. It only recognises the yeast
   3v3 test case below, by keyword, and always passes the result
   through parse_job_spec() for validation. Anything else raises a
   clear ValueError — it never guesses a spec.
   Week 2, step 2: an OpenAI LLM planner. It calls the LLM with a
   tool schema that outputs exactly the structured spec validated by
   (1) — the LLM never launches a pipeline directly, it only produces
   a spec that must pass validation first. The rule-based parser stays
   as the fallback when the LLM is unavailable or its output fails
   validation. The LLM needs OPENAI_API_KEY in the environment; the
   key is never committed, never logged, and never sent in chat.
"""
import json
import os
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


def _yeast_test_spec() -> dict:
    """The one case the rule-based parser knows: yeast 3v3 test data."""
    samples = []
    for sample, group in [
        ("SRR6357070", "WT"), ("SRR6357071", "WT"), ("SRR6357072", "WT"),
        ("SRR6357076", "RAP1_IAA_30M"), ("SRR6357077", "RAP1_IAA_30M"),
        ("SRR6357078", "RAP1_IAA_30M"),
    ]:
        samples.append({
            "sample": sample,
            "group": group,
            "fastq_1": f"data/test/{sample}_1.fastq.gz",
            "fastq_2": f"data/test/{sample}_2.fastq.gz",
        })
    return {
        "samples": samples,
        "contrast": "RAP1_IAA_30M_vs_WT",
        "genome": "saccharomyces_cerevisiae_R64",
        "strandedness": "reverse",
        "profile": "docker",
    }


def parse_natural_language_rule_based(text: str) -> JobSpec:
    """Rule-based parser (Week 2, step 1). No LLM, no API key, no network.

    Recognises only the yeast 3v3 test case, by keyword (Chinese or
    English, case-insensitive). Anything else raises ValueError with a
    clear message — it never guesses a spec.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("natural-language input must be a non-empty string")
    low = text.strip().lower()
    yeast_words = ("yeast", "酵母", "srr6357", "rap1")
    test_words = ("test", "测试", "3v3", "3 vs 3", "run", "跑")
    if any(w in low for w in yeast_words) and any(w in low for w in test_words):
        return parse_job_spec(_yeast_test_spec())
    if "yeast" in low or "酵母" in low:
        return parse_job_spec(_yeast_test_spec())
    raise ValueError(
        "rule-based parser only understands the yeast 3v3 test case, e.g. "
        "'run the yeast test' / '跑酵母测试'. "
        "For anything else, submit a structured job spec to POST /jobs, "
        "or wait for the LLM planner (Week 2, step 2)."
    )


def parse_natural_language(text: str) -> JobSpec:
    """Natural-language -> JobSpec. Returns (spec, source) via with_source."""
    spec, _source = parse_natural_language_with_source(text)
    return spec


def parse_natural_language_with_source(text: str):
    """Natural-language -> (JobSpec, source), source is 'llm' or 'rule'.

    If OPENAI_API_KEY is set, try the LLM first. If the LLM call fails,
    returns no tool call, or its output fails parse_job_spec()
    validation, fall back to the rule-based parser. If no key is set,
    use the rule-based parser directly. The LLM never launches a
    pipeline — its output must pass parse_job_spec() first.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("natural-language input must be a non-empty string")
    if os.environ.get("OPENAI_API_KEY"):
        try:
            return parse_natural_language_llm(text), "llm"
        except Exception as exc:  # LLM failed — log why, then fall back
            # Log the reason (never the API key) so a silent fallback
            # can be diagnosed from the uvicorn terminal.
            print(f"[planner] LLM failed, falling back to rule: {exc}", flush=True)
            try:
                return parse_natural_language_rule_based(text), "rule"
            except ValueError:
                raise ValueError(
                    f"LLM planner failed ({exc}); rule-based fallback also "
                    "does not understand this input. Try 'run the yeast test' "
                    "or submit a structured job spec to POST /jobs."
                ) from exc
    return parse_natural_language_rule_based(text), "rule"


def _http_client_without_brotli():
    """httpx client that never asks for brotli/zstd encoding.

    See the comment at the OpenAI() call site: newer httpx passes
    output_buffer_limit= to the decoder, which older brotli builds
    reject, breaking even successful API responses at decode time.
    """
    import httpx

    return httpx.Client(headers={"Accept-Encoding": "gzip, deflate"}, timeout=60.0)


def parse_natural_language_llm(text: str) -> JobSpec:
    """OpenAI LLM planner (Week 2, step 2). Needs OPENAI_API_KEY set.

    Calls gpt-4o-mini (override with OPENAI_MODEL) with one tool,
    create_job_spec, and validates the tool-call arguments through
    parse_job_spec(). Raises ValueError/RuntimeError on any failure —
    the caller falls back to the rule-based parser.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "openai package is not installed; run: pip install -r requirements.txt"
        ) from exc
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    tool = {
        "type": "function",
        "function": {
            "name": "create_job_spec",
            "description": "Create a validated RNA-seq job spec for AutoRNA.",
            "parameters": {
                "type": "object",
                "properties": {
                    "samples": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sample": {"type": "string"},
                                "group": {"type": "string"},
                                "fastq_1": {"type": "string"},
                                "fastq_2": {"type": "string"},
                            },
                            "required": ["sample", "group", "fastq_1", "fastq_2"],
                        },
                    },
                    "contrast": {"type": "string", "description": "e.g. RAP1_IAA_30M_vs_WT"},
                    "genome": {"type": "string", "description": "saccharomyces_cerevisiae_R64 unless the user names another supported genome"},
                    "strandedness": {"type": "string", "enum": ["reverse", "forward", "unstranded"]},
                    "profile": {"type": "string", "enum": ["docker", "singularity", "standard"]},
                },
                "required": ["samples", "contrast"],
            },
        },
    }
    system = (
        "You are the planner for AutoRNA, an RNA-seq pipeline. "
        "Translate the user's request into exactly one create_job_spec tool call. "
        "Known test data (use this unless the user gives explicit sample IDs and fastq paths): "
        "yeast, Saccharomyces cerevisiae R64, WT samples SRR6357070, SRR6357071, SRR6357072, "
        "RAP1_IAA_30M samples SRR6357076, SRR6357077, SRR6357078, "
        "fastq paths data/test/<sample>_1.fastq.gz and data/test/<sample>_2.fastq.gz, "
        "contrast RAP1_IAA_30M_vs_WT, genome saccharomyces_cerevisiae_R64, "
        "strandedness reverse, profile docker. "
        "If the user asks for anything else (another organism, other samples, human data), "
        "still return your best spec via the tool — validation will reject what is not wired yet. "
        "Never invent fastq paths for samples the user did not name and that are not in the known test data."
    )
    client = OpenAI(
        api_key=api_key,
        # Workaround for a httpx / brotli version mismatch (Nov 2026):
        # newer httpx calls the brotli/zstd decoder with
        # output_buffer_limit=, which older brotli builds do not accept,
        # so decoding a 200 OK response fails with
        # "Decompressor.decompress() got an unexpected keyword argument
        # 'output_buffer_limit'" and the planner falls back to rule.
        # Requesting only gzip/deflate means the broken decoder is
        # never selected; gzip is fine for JSON. Also pin brotli>=1.2.0
        # in requirements.txt.
        http_client=_http_client_without_brotli(),
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": text.strip()},
        ],
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": "create_job_spec"}},
    )
    calls = (resp.choices[0].message.tool_calls or []) if resp.choices else []
    if not calls:
        raise ValueError("LLM returned no tool call")
    raw = json.loads(calls[0].function.arguments)
    if not isinstance(raw, dict):
        raise ValueError("LLM tool call did not return a JSON object")
    raw.setdefault("genome", "saccharomyces_cerevisiae_R64")
    raw.setdefault("strandedness", "reverse")
    raw.setdefault("profile", "docker")
    return parse_job_spec(raw)
