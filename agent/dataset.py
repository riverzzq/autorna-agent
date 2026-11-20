"""Dataset understanding — AutoRNA Agent, Step 1 (more agent-like).

Given a new dataset the agent first LOOKS at it before proposing
anything to run:

- Local FASTQ folder: pairs _1/_2 files, detects paired vs
  single-end, samples the first reads for read length, and infers
  sample groups from file names.
- SRA run IDs: looks up real metadata from the ENA API (organism,
  library layout, sample title). Nothing is invented: an ID the API
  does not know is reported as not found, and the GEO accession is
  NOT guessed — ENA returns a study accession when it has one.

What it does NOT do (yet — that is Step 2):
- It does not download FASTQs or references, and it does not run
  the pipeline. Human reference (GRCh38) is not wired yet, so a
  human proposal stops at "needs reference" with a clear message.
- It does not force a 2-factor design (e.g. cell line x treatment)
  into one contrast. When it sees more than two groups it says so
  and asks which contrast to run.
- Strandedness is NOT guessed: Salmon already runs with `-l A`
  (auto-detect) in modules/salmon.nf, so the samplesheet value is
  informational only and does not change the run.
"""
import gzip
import json
import re
import urllib.request
from pathlib import Path

ENA_API = "https://www.ebi.ac.uk/ena/portal/api/search"
CONTROL_WORDS = ("control", "ctrl", "untreated", "wt", "wildtype", "vehicle", "mock", "untreated")
TREATED_WORDS = ("treated", "treat", "dex", "drug", "knockdown", "kd", "ko", "overexpress")
# Organism -> (genome key used by parse_job_spec, wired?)
KNOWN_ORGANISMS = {
    "saccharomyces cerevisiae": ("saccharomyces_cerevisiae_R64", True),
    "homo sapiens": ("homo_sapiens_GRCh38", False),
}

FASTQ_SUFFIXES = (".fastq.gz", ".fq.gz", ".fastq", ".fq")
PAIR_MARKERS = (("_1", "_2"), ("_R1", "_R2"), (".1", ".2"))


def _strip_fastq_suffix(name):
    for suf in FASTQ_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def sample_read_lengths(path, n_records=3):
    """Read lengths of the first n_records reads (gzip-aware)."""
    opener = gzip.open if str(path).endswith(".gz") else open
    lengths = []
    try:
        with opener(path, "rt") as fh:
            for _ in range(n_records):
                header = fh.readline()
                if not header:
                    break
                seq = fh.readline().strip()
                fh.readline()
                fh.readline()
                if seq:
                    lengths.append(len(seq))
    except (OSError, EOFError):
        return []
    return lengths


def scan_fastq_folder(folder):
    """Pair FASTQ files in a folder. Returns samples + problems found."""
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"not a folder: {folder}")
    files = [p for p in folder.rglob("*") if p.is_file() and p.name.endswith(FASTQ_SUFFIXES)]
    by_base = {}
    for p in files:
        by_base.setdefault(_strip_fastq_suffix(p.name), p)
    samples = []
    problems = []
    seen = set()
    for base, path in sorted(by_base.items()):
        if base in seen:
            continue
        for m1, m2 in PAIR_MARKERS:
            if base.endswith(m1):
                stem = base[: -len(m1)]
                mate = stem + m2
                if mate in by_base:
                    seen.add(base)
                    seen.add(mate)
                    samples.append({
                        "sample": stem,
                        "layout": "paired",
                        "fastq_1": str(path),
                        "fastq_2": str(by_base[mate]),
                        "read_lengths_1": sample_read_lengths(path),
                        "size_1_bytes": path.stat().st_size,
                        "size_2_bytes": by_base[mate].stat().st_size,
                    })
                    break
        else:
            seen.add(base)
            samples.append({
                "sample": base,
                "layout": "single",
                "fastq_1": str(path),
                "fastq_2": None,
                "read_lengths_1": sample_read_lengths(path),
                "size_1_bytes": path.stat().st_size,
                "size_2_bytes": None,
            })
            problems.append(f"{path.name} has no mate file (single-end?)")
    return samples, problems


def group_from_name(name):
    """Group candidate from a sample/file name: strip replicate suffixes only.

    Only explicit replicate markers are stripped (dex_rep1 -> dex).
    Plain numeric IDs (SRR6357070) are left alone — stripping their
    digits would wrongly merge every sample into one group, so for
    those the profiler admits it cannot infer groups and asks.
    """
    candidate = re.sub(r"[_-]?(rep|replicate)\d*$", "", name, flags=re.IGNORECASE)
    candidate = re.sub(r"[_-]r\d+$", "", candidate, flags=re.IGNORECASE)
    return candidate.strip("_-") or name


def infer_groups(sample_names):
    """Group sample names by their stripped form. Returns {group: [samples]}."""
    groups = {}
    for name in sample_names:
        groups.setdefault(group_from_name(name), []).append(name)
    return groups


def classify_group_role(name):
    lowered = name.lower()
    if any(w in lowered for w in CONTROL_WORDS):
        return "control"
    if any(w in lowered for w in TREATED_WORDS):
        return "treated"
    return "unknown"


def propose_contrast(groups):
    """Propose a contrast, or None with a reason when it is ambiguous."""
    if len(groups) == 2:
        names = sorted(groups)
        roles = {n: classify_group_role(n) for n in names}
        treated = [n for n, r in roles.items() if r == "treated"]
        control = [n for n, r in roles.items() if r == "control"]
        if len(treated) == 1 and len(control) == 1:
            return f"{treated[0]}_vs_{control[0]}", None
        return None, (
            f"two groups found ({names[0]}, {names[1]}) but I cannot tell "
            "which is treated and which is control — please confirm"
        )
    if len(groups) > 2:
        return None, (
            f"{len(groups)} groups found ({', '.join(sorted(groups))}) — this looks "
            "like a multi-factor design, not one contrast. Please pick the contrast to run"
        )
    return None, "only one group found — need at least two groups for a contrast"


def fetch_sra_metadata(srr_ids):
    """Look up SRA run metadata from the ENA API. Returns {srr: row} + not-found list."""
    ids = [s.strip() for s in srr_ids if s and s.strip()]
    if not ids:
        return {}, []
    query = " OR ".join(f"run_accession={s}" for s in ids)
    fields = "run_accession,sample_accession,scientific_name,library_layout,library_strategy,sample_title,study_accession"
    url = (
        f"{ENA_API}?query={urllib.parse.quote(query)}"
        f"&result=read_run&fields={fields}&format=json"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        rows = json.loads(resp.read().decode())
    found = {row["run_accession"]: row for row in rows}
    return found, [s for s in ids if s not in found]


def profile_dataset(folder=None, srr_ids=None):
    """Profile a new dataset. Returns a proposal + the questions it raises."""
    samples = []
    problems = []
    metadata = {}
    not_found = []
    if folder:
        samples, problems = scan_fastq_folder(folder)
    if srr_ids:
        metadata, not_found = fetch_sra_metadata(srr_ids)
        for srr in not_found:
            problems.append(f"{srr}: not found in ENA — check the ID, nothing was invented for it")

    # Group inference: prefer ENA sample titles, fall back to file names.
    group_source = "file names"
    if metadata:
        group_source = "ENA sample titles"
        # "N61311_Alb_Dex" -> group "Alb_Dex" (drop the cell-line prefix,
        # keep the whole treatment). "N052611_untreated" -> "untreated".
        sample_groups = {}
        for srr, row in metadata.items():
            title = (row.get("sample_title") or "").strip()
            parts = title.split("_")
            sample_groups[srr] = "_".join(parts[1:]) if len(parts) > 1 else (title or srr)
    else:
        sample_groups = {s["sample"]: group_from_name(s["sample"]) for s in samples}

    groups = {}
    for sample, group in sample_groups.items():
        groups.setdefault(group, []).append(sample)

    # Organism + reference, from metadata when available.
    organisms = sorted({(row.get("scientific_name") or "").strip() for row in metadata.values() if row.get("scientific_name")})
    reference = {"organism": organisms[0] if organisms else None, "genome": None, "wired": False, "note": None}
    if len(organisms) == 1:
        key = organisms[0].lower()
        if key in KNOWN_ORGANISMS:
            genome, wired = KNOWN_ORGANISMS[key]
            reference.update({"genome": genome, "wired": wired})
            reference["note"] = None if wired else f"{organisms[0]} reference ({genome}) is not wired yet — Step 2 will add automatic reference download"
        else:
            reference["note"] = f"organism {organisms[0]} is not in the known list — reference must be resolved before running"
    elif len(organisms) > 1:
        reference["note"] = f"mixed organisms in metadata: {', '.join(organisms)} — cannot pick one reference"

    contrast, contrast_question = propose_contrast(groups)

    questions = list(problems)
    if contrast_question:
        questions.append(contrast_question)
    if reference["note"]:
        questions.append(reference["note"])
    if not samples and not metadata:
        questions.append("no FASTQ files and no SRA metadata — give a folder path or SRR IDs")

    layouts = sorted({s["layout"] for s in samples}) if samples else sorted({(row.get("library_layout") or "").lower() for row in metadata.values() if row.get("library_layout")})
    read_lengths = sorted({L for s in samples for L in s.get("read_lengths_1", [])})

    proposal_samples = []
    for s in samples:
        proposal_samples.append({
            "sample": s["sample"],
            "group": sample_groups.get(s["sample"], group_from_name(s["sample"])),
            "fastq_1": s["fastq_1"],
            "fastq_2": s["fastq_2"],
            "strandedness": "reverse",
        })
    for srr in sorted(metadata):
        if not any(p["sample"] == srr for p in proposal_samples):
            proposal_samples.append({
                "sample": srr,
                "group": sample_groups.get(srr, srr),
                "fastq_1": None,
                "fastq_2": None,
                "strandedness": "reverse",
            })

    return {
        "n_samples": len(proposal_samples),
        "layout": layouts[0] if len(layouts) == 1 else (layouts or None),
        "read_lengths_sampled": read_lengths,
        "organism": reference["organism"],
        "reference": reference,
        "groups": groups,
        "group_source": group_source,
        "contrast_proposal": contrast,
        "samples": proposal_samples,
        "metadata": metadata,
        "questions": questions,
    }
