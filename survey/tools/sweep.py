#!/usr/bin/env python3
"""Dual-lane VMOD survey sweep: build + load-smoke every worklist VMOD in
the lane containers built by survey/harness/build-images.sh.

Per (vmod, lane): docker run --rm --network none with the host-side repo
clone mounted read-only, driving /harness/build-and-load.sh. Results land in
survey/results/<lane>/<name>.json with the full log alongside. The sweep is
resumable: settled results are skipped unless --force, but harness artifacts
(timeouts, docker errors, pin mismatches, missing clones) are always retried
so transient trouble cannot bake into the matrix.

Each result is stamped with the lane image id and the repo commit that was
actually swept; when triage.json records a pin for the repo, a cache tree on
a different commit is refused (class pin-mismatch) instead of swept.

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

from classes import RETRY_CATEGORIES, category

SCHEMA = "vmod-survey-result/v2"
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
    # Attribute subclasses from the failing stage's output only: text before
    # the last ::stage:: marker belongs to stages that succeeded and must not
    # vote (a configure that merely prints "checking to see if VARNISHSRC
    # set... no" and then fails on something else is not a source-tree demand).
    tail = log_text.rsplit("::stage::", 1)[-1]
    if base == "configure-failed":
        if re.search(r"configure: error:.*(Need (VINYL|VARNISH)SRC|(VINYL|VARNISH)SRC must be set)", tail):
            # Builds against the daemon source tree, not the installed dev
            # package — can never work from packages as currently written.
            return "configure-failed-needs-source-tree"
        if re.search(r"Missing Varnish Cache development files|Missing Vinyl Cache development files|No package 'varnishapi'|varnishapi.*not found|Unable to find required (Varnish|Vinyl) build environment|version .* or higher is required|version below .* is required", tail):
            return "configure-failed-api-detect"
        if re.search(r"requires (the )?lib|(lib|header)\w* (is )?(not found|missing|required)|No package '(?!varnishapi)", tail):
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
    image_id = inspect.stdout.strip()
    recorded_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    # Keep a history of the images this lane's results were produced with;
    # report.py flags result sets that span more than one image.
    meta_path = RESULTS_DIR / lane / "LANE.json"
    runs = []
    if meta_path.exists():
        try:
            runs = json.loads(meta_path.read_text()).get("runs", [])
        except ValueError:
            runs = []
    if not runs or runs[-1].get("image_id") != image_id:
        runs = runs + [{"image_id": image_id, "recorded_at": recorded_at}]
    meta = {
        "schema": "vmod-survey-lane/v2",
        "lane": lane,
        "image": image,
        "image_id": image_id,
        "daemon_probe": (proc.stdout + proc.stderr).strip().splitlines(),
        "recorded_at": recorded_at,
        "runs": runs,
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def repo_head(repo):
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def sweep_one(name, lane, image, image_id, pin, timeout):
    out_json = RESULTS_DIR / lane / f"{name}.json"
    out_log = RESULTS_DIR / lane / f"{name}.log"
    repo = REPOS_DIR / name
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name)
    container = f"vmod-survey-{lane}-{slug}-{uuid.uuid4().hex[:6]}"
    started = time.time()

    def emit(result):
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(result, indent=2) + "\n")
        return result

    def base_result(cls, exit_code, head):
        return {
            "schema": SCHEMA,
            "name": name,
            "lane": lane,
            "class": cls,
            "category": category(cls),
            "exit_code": exit_code,
            "image_id": image_id,
            "head_commit": head,
            "pinned_commit": pin,
            "vmods": {},
            "duration_s": round(time.time() - started, 1),
        }

    if not (repo / ".git").exists():
        return emit(base_result("clone-missing", None, None))

    head = repo_head(repo)
    if pin and head and head != pin:
        # The cache tree is not the code triage recorded; sweeping it would
        # silently publish results for a different commit. Re-run triage
        # (which restores pins) or triage --repin to move the pin forward.
        return emit(base_result("pin-mismatch", None, head))

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

    result = base_result(cls, exit_code, head)
    result["last_stage"] = stages[-1] if stages else None
    result["vmods"] = vmods
    result["duration_s"] = round(time.time() - started, 1)
    out_log.parent.mkdir(parents=True, exist_ok=True)
    out_log.write_text(log_text)
    return emit(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worklist", type=Path, default=DATA_DIR / "worklist.json")
    parser.add_argument("--triage", type=Path, default=DATA_DIR / "triage.json",
                        help="triage snapshot whose head_commit pins the swept trees")
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

    pins = {}
    if args.triage.exists():
        triage = json.loads(args.triage.read_text())
        pins = {r["name"]: r.get("head_commit") for r in triage.get("results", [])}

    metas = {lane: lane_metadata(lane, image) for lane, image in lanes.items()}

    jobs = []
    for lane, image in lanes.items():
        image_id = metas[lane]["image_id"]
        for name in names:
            out_json = RESULTS_DIR / lane / f"{name}.json"
            if not args.force and out_json.exists():
                try:
                    prior_cls = json.loads(out_json.read_text()).get("class")
                except ValueError:
                    prior_cls = None
                # Honour settled results only; harness artifacts and missing
                # clones are retried so a docker hiccup or timeout cannot
                # survive into the report as a permanent failure.
                if prior_cls is not None and category(prior_cls) not in RETRY_CATEGORIES:
                    continue
            jobs.append((name, lane, image, image_id, pins.get(name)))
    print(f"{len(jobs)} runs to do ({len(names)} vmods x {len(lanes)} lanes, minus settled results)", file=sys.stderr)

    counts = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(sweep_one, *job, args.timeout) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            counts[result["class"]] = counts.get(result["class"], 0) + 1
            print(f"  {result['lane']:<9} {result['name']:<24} {result['class']} ({result['duration_s']}s)", file=sys.stderr)

    print(f"done: {counts}", file=sys.stderr)


if __name__ == "__main__":
    main()
