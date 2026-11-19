"""QC-gate parser — AutoRNA Agent, Week 3 step 1.

Reads real numbers, never invents them:
- Salmon mapping rate: <sample>/aux_info/meta_info.json, key
  `percent_mapped` (NOT inferred from file size).
- Salmon assigned fragments: sum of NumReads in <sample>/quant.sf.
- MultiQC completeness: every sample id appears in
  multiqc_report_data/multiqc_general_stats.txt, when that file
  exists. A missing file is reported, not silently treated as a pass.

Gates applied (docs/qc-gates.md, starting thresholds):
- Gate 4: percent_mapped >= min_mapping_rate (default 0.5).
- Gate 5: total NumReads > 0 and within ~10x of the run median.
- Gate 6: MultiQC row present (warning if the file is missing,
  hard fail only when the file exists and the sample row is absent).

qc_pass is 1 only when every sample passes gates 4 and 5.
A sample that fails a gate is excluded from DESeq2 (Week 3 step 3).
"""
import json
import statistics
from pathlib import Path


def _read_percent_mapped(sample_dir: Path):
    """percent_mapped from Salmon meta_info.json, or None if unreadable."""
    meta_path = sample_dir / "aux_info" / "meta_info.json"
    if not meta_path.exists():
        return None, f"missing {meta_path.name}"
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return None, f"unreadable meta_info.json: {exc}"
    value = meta.get("percent_mapped")
    if value is None:
        return None, "meta_info.json has no percent_mapped"
    try:
        return float(value), None
    except (TypeError, ValueError):
        return None, f"percent_mapped is not a number: {value!r}"


def _read_num_reads(sample_dir: Path):
    """Total NumReads summed from quant.sf, or None if unreadable."""
    quant_path = sample_dir / "quant.sf"
    if not quant_path.exists():
        return None, "missing quant.sf"
    total = 0.0
    rows = 0
    try:
        with open(quant_path) as fh:
            header = fh.readline().rstrip("\n").split("\t")
            try:
                num_idx = header.index("NumReads")
            except ValueError:
                return None, "quant.sf has no NumReads column"
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) <= num_idx or not parts[num_idx]:
                    continue
                total += float(parts[num_idx])
                rows += 1
    except (OSError, ValueError) as exc:
        return None, f"unreadable quant.sf: {exc}"
    if rows == 0:
        return None, "quant.sf has no data rows"
    return total, None


def _multiqc_samples(results_dir: Path):
    """Sample ids listed in MultiQC general stats, or None if no file."""
    stats_path = results_dir / "multiqc" / "multiqc_report_data" / "multiqc_general_stats.txt"
    if not stats_path.exists():
        return None
    try:
        text = stats_path.read_text(errors="replace")
    except OSError:
        return None
    found = set()
    for line in text.splitlines()[1:]:
        if line.strip():
            found.add(line.split("\t")[0].strip())
    return found


def parse_run_qc(results_dir, samples, min_mapping_rate=0.5):
    """Parse QC for one run. Returns a dict; never raises on bad data.

    `samples` is a list of {"sample": ..., "group": ...} dicts.
    `results_dir` is runs/<run_id>/results (salmon/ + multiqc/ below it).
    """
    results_dir = Path(results_dir)
    salmon_dir = results_dir / "salmon"
    per_sample = []
    num_reads_list = []
    # First pass: read raw numbers so gate 5 can compare to the median.
    raw = {}
    for s in samples:
        sample = s["sample"]
        sample_dir = salmon_dir / sample
        pct, pct_err = _read_percent_mapped(sample_dir)
        reads, reads_err = _read_num_reads(sample_dir)
        raw[sample] = {
            "sample": sample,
            "group": s.get("group"),
            "percent_mapped": pct,
            "num_reads": reads,
            "errors": [e for e in (pct_err, reads_err) if e],
        }
        if reads is not None:
            num_reads_list.append(reads)
    median_reads = statistics.median(num_reads_list) if num_reads_list else None
    multiqc_found = _multiqc_samples(results_dir)

    for sample, entry in raw.items():
        gates = {}
        # Gate 4: mapping rate.
        if entry["percent_mapped"] is None:
            gates["mapping_rate"] = {"pass": False, "reason": entry["errors"][0] if entry["errors"] else "no percent_mapped"}
        else:
            gates["mapping_rate"] = {
                "pass": entry["percent_mapped"] / 100.0 >= min_mapping_rate,
                "threshold": min_mapping_rate,
                "value": entry["percent_mapped"],
            }
        # Gate 5: assigned fragments.
        if entry["num_reads"] is None:
            gates["num_reads"] = {"pass": False, "reason": "no NumReads"}
        elif entry["num_reads"] <= 0:
            gates["num_reads"] = {"pass": False, "reason": "NumReads is 0"}
        elif median_reads and (entry["num_reads"] < median_reads / 10 or entry["num_reads"] > median_reads * 10):
            gates["num_reads"] = {
                "pass": False,
                "reason": f"NumReads {entry['num_reads']:.0f} is >10x away from run median {median_reads:.0f}",
            }
        else:
            gates["num_reads"] = {"pass": True, "value": entry["num_reads"]}
        # Gate 6: MultiQC row present.
        if multiqc_found is None:
            gates["multiqc_row"] = {"pass": True, "warning": "multiqc_general_stats.txt not found; row not checked"}
        else:
            gates["multiqc_row"] = {
                "pass": sample in multiqc_found,
                "reason": None if sample in multiqc_found else "sample missing from MultiQC general stats",
            }
        entry["gates"] = gates
        # A sample passes only on gates 4 and 5; gate 6 is a hard fail
        # only when the file exists and the row is absent.
        entry["pass"] = bool(gates["mapping_rate"]["pass"] and gates["num_reads"]["pass"] and gates["multiqc_row"]["pass"])
        per_sample.append(entry)

    all_pass = bool(per_sample) and all(e["pass"] for e in per_sample)
    return {
        "min_mapping_rate": min_mapping_rate,
        "median_num_reads": median_reads,
        "multiqc_stats_found": multiqc_found is not None,
        "samples": per_sample,
        "qc_pass": 1 if all_pass else 0,
        "failed_samples": [e["sample"] for e in per_sample if not e["pass"]],
    }


def compute_qc_for_run(run_dir):
    """Parse QC for runs/<run_id> from its job_spec.json. Returns the QC dict.

    Raises FileNotFoundError / ValueError when the run has no spec or
    no results yet — the caller turns that into a not-ready response.
    """
    from .planner import parse_job_spec

    run_dir = Path(run_dir)
    spec_path = run_dir / "job_spec.json"
    if not spec_path.exists():
        raise FileNotFoundError(f"no job_spec.json in {run_dir}")
    spec_raw = json.loads(spec_path.read_text())
    job = parse_job_spec(spec_raw)
    results_dir = run_dir / "results"
    if not (results_dir / "salmon").exists():
        raise FileNotFoundError(f"no results/salmon in {run_dir}; run has not produced quant.sf yet")
    qc = parse_run_qc(results_dir, job.samples)
    qc["run_id"] = run_dir.name
    qc["contrast"] = job.contrast
    (run_dir / "qc.json").write_text(json.dumps(qc, indent=2))
    return qc
