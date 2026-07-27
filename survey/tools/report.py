#!/usr/bin/env python3
"""Render the survey result matrix from worklist + triage + sweep results.

Writes survey/results/REPORT.md: lane identities, cross-lane verdict counts,
the divergence set (varnish9 pass, vinyl9 fail), and the full per-VMOD matrix.

Verdicts are computed from the class categories in classes.py, so harness
artifacts (timeouts, docker errors, pin mismatches) can never masquerade as
DIVERGENT or fails-both: they land in an "incomplete" section instead, and
the tool exits non-zero — as it also does when a lane's results span more
than one image — so an unattended rerun fails loudly rather than publishing.

Python 3 standard library only, per the repository tooling rule.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from classes import category

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
    cats = (category(varnish_class), category(vinyl_class))
    if "artifact" in cats:
        return "incomplete"
    if "dead" in cats:
        return "dead"
    if "unbuildable" in cats:
        return "needs-source-tree"
    v, y = cats
    if v == "pass" and y == "pass":
        return "green"
    if v == "pass":
        # DIVERGENT requires a genuine failure on the vinyl lane; a pass
        # against a blocked dependency is a harness question, not divergence.
        return "DIVERGENT" if y == "fail" else "incomplete"
    if y == "pass":
        return "anomaly" if v == "fail" else "incomplete"
    if v == "blocked" and y == "blocked":
        return "blocked-deps"
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
        vd = verdict(classes["varnish9"], classes["vinyl9"], entry.get("status"))
        # Cross-check swept commits against the triage pin (older v1 results
        # carry no head_commit and are exempt): results for the wrong commit,
        # or lanes that swept different commits, are not publishable.
        heads = {lane: lane_results[lane].get(name, {}).get("head_commit") for lane in LANES}
        pin = tri.get("head_commit")
        reason = None
        if pin and any(h and h != pin for h in heads.values()):
            reason = "swept commit differs from triage pin"
        elif len({h for h in heads.values() if h}) > 1:
            reason = "lanes swept different commits"
        if reason and vd not in ("bundled", "pending"):
            vd = "incomplete"
        rows.append({
            "name": name,
            "inactive": entry["inactive"],
            "abi": tri.get("abi") or "-",
            "head_year": (tri.get("head_date") or "-")[:4],
            "varnish9": classes["varnish9"] or "-",
            "vinyl9": classes["vinyl9"] or "-",
            "verdict": vd,
            "reason": reason,
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
    # Results stamped with an image id must all come from one image per lane
    # (v1 results carry none and are exempt); a resumed sweep that spanned an
    # image rebuild mixes daemon builds and is not publishable.
    lane_images = {
        lane: {doc.get("image_id") for doc in lane_results[lane].values() if doc.get("image_id")}
        for lane in LANES
    }
    lines.append("## Lane identities")
    lines.append("")
    for lane in LANES:
        meta = lane_meta[lane]
        if meta:
            probe = "; ".join(meta.get("daemon_probe", []))
            mixed = f" — **MIXED: results span {len(lane_images[lane])} images, resweep with --force**" if len(lane_images[lane]) > 1 else ""
            lines.append(f"- **{lane}** — image `{meta['image']}` (`{meta.get('image_id', '')[:19]}`): {probe}{mixed}")
        else:
            lines.append(f"- **{lane}** — no results yet")
    lines.append("")
    lines.append("A pass means: bootstrapped, configured, compiled, and every built module .so accepted by the lane daemon's VCL compiler. It is a survey signal, not a support claim; no test suites were run.")
    lines.append("")
    lines.append("## Verdict counts")
    lines.append("")
    for key in ("green", "DIVERGENT", "anomaly", "fails-both", "needs-source-tree", "blocked-deps", "dead", "bundled", "incomplete", "pending"):
        if key in counts:
            lines.append(f"- {key}: {counts[key]}")
    lines.append("")

    incomplete = [row for row in rows if row["verdict"] == "incomplete"]
    if incomplete:
        lines.append("## Incomplete (harness artifacts — resweep before publishing)")
        lines.append("")
        lines.append("These rows record harness trouble, not VMOD results; the matrix is not publishable while they exist. `tools/sweep.py` retries them automatically on its next invocation.")
        lines.append("")
        lines.append("| vmod | varnish9 | vinyl9 | reason |")
        lines.append("| --- | --- | --- | --- |")
        for row in incomplete:
            lines.append(f"| {row['name']} | {row['varnish9']} | {row['vinyl9']} | {row['reason'] or 'harness artifact'} |")
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

    problems = []
    if incomplete:
        problems.append(f"{len(incomplete)} incomplete rows")
    for lane, ids in lane_images.items():
        if len(ids) > 1:
            problems.append(f"lane {lane} results span {len(ids)} images")
    if problems:
        print(f"NOT PUBLISHABLE: {'; '.join(problems)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
