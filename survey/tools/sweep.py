#!/usr/bin/env python3
"""Dual-lane VMOD survey sweep: build + load-smoke every worklist VMOD in
the lane containers built by survey/harness/build-images.sh.

Per (vmod, lane): docker run --rm --network none with the host-side repo
clone mounted read-only, driving /harness/build-and-load.sh. Results land in
survey/results/<lane>/<name>.json with the full log alongside; existing
results are skipped unless --force, so the sweep is resumable.

Python 3 standard library only, per the repository tooling rule.
"""

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

SCHEMA = "vmod-survey-result/v1"
SURVEY_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = SURVEY_DIR / "data"
REPOS_DIR = SURVEY_DIR / "cache" / "repos"
RESULTS_DIR = SURVEY_DIR / "results"

LANES = {
    "varnish9": "vmod-survey-varnish9",
    "vinyl9": "vmod-survey-vinyl9",
}

EXIT_STAGE = {
    10: "copy-failed",
    11: "bootstrap-failed",
    12: "configure-failed",
    13: "build-failed",
    14: "no-vmod-built",
    15: "load-failed",
}


def classify(exit_code, log_text):
    """Map the run outcome to the failure classes in the survey plan."""
    if exit_code == 0:
        return "pass"
    base = EXIT_STAGE.get(exit_code)
    if base == "configure-failed":
        if re.search(r"VINYLSRC|VARNISHSRC", log_text):
            # Builds against the daemon source tree, not the installed dev
            # package — can never work from packages as currently written.
            return "configure-failed-needs-source-tree"
        if re.search(r"Missing Varnish Cache development files|Missing Vinyl Cache development files|No package 'varnishapi'|varnishapi.*not found|version .* or higher is required|version below .* is required", log_text):
            return "configure-failed-api-detect"
        if re.search(r"requires (the )?lib|(lib|header)\w* (is )?(not found|missing|required)|No package '(?!varnishapi)", log_text):
            return "configure-failed-missing-dep"
        return "configure-failed-other"
    if base == "build-failed":
        if "undefined reference" in log_text or "undefined symbol" in log_text:
            return "link-failed"
        return "compile-failed"
    if base:
        return base
    return f"harness-error(exit={exit_code})"


def lane_metadata(lane, image):
    probe = (
        "echo image=$LANE; $DAEMON -V 2>&1 | head -n 2; "
        "echo varnishapi=$(pkg-config --modversion varnishapi 2>/dev/null || echo none)"
    )
    proc = subprocess.run(
        ["docker", "run", "--rm", image, "sh", "-c", probe],
        capture_output=True, text=True, timeout=120,
    )
    inspect = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True, text=True, timeout=60,
    )
    meta = {
        "schema": "vmod-survey-lane/v1",
        "lane": lane,
        "image": image,
        "image_id": inspect.stdout.strip(),
        "daemon_probe": (proc.stdout + proc.stderr).strip().splitlines(),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (RESULTS_DIR / lane).mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / lane / "LANE.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def sweep_one(name, lane, image, timeout):
    out_json = RESULTS_DIR / lane / f"{name}.json"
    out_log = RESULTS_DIR / lane / f"{name}.log"
    repo = REPOS_DIR / name
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name)
    container = f"vmod-survey-{lane}-{slug}-{uuid.uuid4().hex[:6]}"
    started = time.time()

    if not (repo / ".git").exists():
        result = {"schema": SCHEMA, "name": name, "lane": lane, "class": "clone-missing", "exit_code": None, "vmods": {}, "duration_s": 0}
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(result, indent=2) + "\n")
        return result

    cmd = [
        "docker", "run", "--rm", "--name", container,
        "--network", "none",
        "-v", f"{repo}:/src:ro",
        image, "/harness/build-and-load.sh", name,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        exit_code = proc.returncode
        log_text = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as err:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=60)
        exit_code = -1
        log_text = ((err.stdout or b"").decode(errors="replace") if isinstance(err.stdout, bytes) else (err.stdout or "")) + "\n::timeout::"

    vmods = dict(re.findall(r"^::vmod::(\S+?)::(pass|fail)$", log_text, re.MULTILINE))
    stages = re.findall(r"^::stage::(\S+)$", log_text, re.MULTILINE)
    cls = "timeout" if exit_code == -1 else classify(exit_code, log_text)

    result = {
        "schema": SCHEMA,
        "name": name,
        "lane": lane,
        "class": cls,
        "exit_code": exit_code,
        "last_stage": stages[-1] if stages else None,
        "vmods": vmods,
        "duration_s": round(time.time() - started, 1),
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_log.write_text(log_text)
    out_json.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worklist", type=Path, default=DATA_DIR / "worklist.json")
    parser.add_argument("--lanes", default="varnish9,vinyl9")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--only", nargs="*", help="sweep only these vmod names")
    parser.add_argument("--force", action="store_true", help="re-run even when a result exists")
    args = parser.parse_args()

    lanes = {}
    for lane in args.lanes.split(","):
        if lane not in LANES:
            parser.error(f"unknown lane {lane}")
        lanes[lane] = LANES[lane]

    worklist = json.loads(args.worklist.read_text())
    # Entries whose status marks them as included in the daemon distribution
    # are not independent VMODs; they are covered by the daemon build itself.
    names = [
        entry["name"]
        for entry in worklist["entries"]
        if entry["clone_url"] and not (entry.get("status") or "").startswith("included")
    ]
    if args.only:
        names = [name for name in names if name in set(args.only)]

    for lane, image in lanes.items():
        lane_metadata(lane, image)

    jobs = []
    for lane, image in lanes.items():
        for name in names:
            if not args.force and (RESULTS_DIR / lane / f"{name}.json").exists():
                continue
            jobs.append((name, lane, image))
    print(f"{len(jobs)} runs to do ({len(names)} vmods x {len(lanes)} lanes, minus existing results)", file=sys.stderr)

    counts = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(sweep_one, name, lane, image, args.timeout) for name, lane, image in jobs]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            counts[result["class"]] = counts.get(result["class"], 0) + 1
            print(f"  {result['lane']:<9} {result['name']:<24} {result['class']} ({result['duration_s']}s)", file=sys.stderr)

    print(f"done: {counts}", file=sys.stderr)


if __name__ == "__main__":
    main()
