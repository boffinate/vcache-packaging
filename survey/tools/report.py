#!/usr/bin/env python3
"""Render the survey result matrix from worklist + triage + sweep results.

Writes survey/results/REPORT.md: lane identities, cross-lane verdict counts,
the divergence set (varnish9 pass, vinyl9 fail), and the full per-VMOD matrix.

Python 3 standard library only, per the repository tooling rule.
"""

import argparse
import json
import time
from pathlib import Path

SURVEY_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = SURVEY_DIR / "data"
RESULTS_DIR = SURVEY_DIR / "results"
LANES = ["varnish9", "vinyl9"]


def load_results(lane):
    results = {}
    lane_dir = RESULTS_DIR / lane
    if not lane_dir.is_dir():
        return {}, None
    for path in lane_dir.glob("*.json"):
        if path.name == "LANE.json":
            continue
        doc = json.loads(path.read_text())
        results[doc["name"]] = doc
    meta_path = lane_dir / "LANE.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
    return results, meta


def verdict(varnish_class, vinyl_class, status=""):
    if (status or "").startswith("included"):
        return "bundled"
    if varnish_class is None or vinyl_class is None:
        return "pending"
    v_pass = varnish_class == "pass"
    y_pass = vinyl_class == "pass"
    if v_pass and y_pass:
        return "green"
    if v_pass and not y_pass:
        return "DIVERGENT"
    if not v_pass and y_pass:
        return "anomaly"
    return "fails-both"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=RESULTS_DIR / "REPORT.md")
    args = parser.parse_args()

    worklist = json.loads((DATA_DIR / "worklist.json").read_text())
    triage = {r["name"]: r for r in json.loads((DATA_DIR / "triage.json").read_text())["results"]}
    lane_results, lane_meta = {}, {}
    for lane in LANES:
        lane_results[lane], lane_meta[lane] = load_results(lane)

    rows = []
    for entry in worklist["entries"]:
        name = entry["name"]
        tri = triage.get(name, {})
        classes = {lane: lane_results[lane].get(name, {}).get("class") for lane in LANES}
        rows.append({
            "name": name,
            "inactive": entry["inactive"],
            "abi": tri.get("abi") or "-",
            "head_year": (tri.get("head_date") or "-")[:4],
            "varnish9": classes["varnish9"] or "-",
            "vinyl9": classes["vinyl9"] or "-",
            "verdict": verdict(classes["varnish9"], classes["vinyl9"], entry.get("status")),
        })

    counts = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1

    lines = []
    lines.append("# VMOD survey report")
    lines.append("")
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M %z')} by survey/tools/report.py")
    lines.append("")
    lines.append(f"Worklist: {worklist['counts']['total']} VMODs from homepage commit `{worklist['homepage_commit']}` plus manual additions.")
    lines.append("")
    lines.append("## Lane identities")
    lines.append("")
    for lane in LANES:
        meta = lane_meta[lane]
        if meta:
            probe = "; ".join(meta.get("daemon_probe", []))
            lines.append(f"- **{lane}** — image `{meta['image']}` (`{meta.get('image_id', '')[:19]}`): {probe}")
        else:
            lines.append(f"- **{lane}** — no results yet")
    lines.append("")
    lines.append("A pass means: bootstrapped, configured, compiled, and every built module .so accepted by the lane daemon's VCL compiler. It is a survey signal, not a support claim; no test suites were run.")
    lines.append("")
    lines.append("## Verdict counts")
    lines.append("")
    for key in ("green", "DIVERGENT", "fails-both", "anomaly", "bundled", "pending"):
        if key in counts:
            lines.append(f"- {key}: {counts[key]}")
    lines.append("")

    divergent = [row for row in rows if row["verdict"] == "DIVERGENT"]
    if divergent:
        lines.append("## Divergence set (passes Varnish 9, fails Vinyl 9)")
        lines.append("")
        lines.append("Each of these is a Vinyl fork divergence to document or a candidate port; see the per-VMOD logs in the lane directories.")
        lines.append("")
        lines.append("| vmod | $ABI | vinyl9 failure |")
        lines.append("| --- | --- | --- |")
        for row in divergent:
            lines.append(f"| {row['name']} | {row['abi']} | {row['vinyl9']} |")
        lines.append("")

    lines.append("## Full matrix")
    lines.append("")
    lines.append("| vmod | listed | last commit | $ABI | varnish9 | vinyl9 | verdict |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in rows:
        listed = "stale" if row["inactive"] else "active"
        lines.append(f"| {row['name']} | {listed} | {row['head_year']} | {row['abi']} | {row['varnish9']} | {row['vinyl9']} | {row['verdict']} |")
    lines.append("")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines))
    print(f"wrote {args.output}: {counts}")


if __name__ == "__main__":
    main()
