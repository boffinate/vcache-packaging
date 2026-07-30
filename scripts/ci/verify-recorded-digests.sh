#!/bin/sh
#
# The identity cross-check: does what THIS run built match what the registry
# already published as the evidence of record?
#
#   verify-recorded-digests.sh --cohort ID --target ID --packages DIR
#
# Three assertions, and every one of them is a stop rather than a note. A
# transaction verdict recorded against packages that are not the recorded
# packages is evidence about something the release does not contain, which is
# worse than no verdict at all -- it looks like a measurement.
#
#   1. the engine identity resolves to the expected cohort;
#   2. every VMOD's recorded package version and revision is what the run built;
#   3. every Debian artifact in the equivalence contract's scope byte-matches
#      the digest recorded in registry/targets/<cohort>/<target>.yml.
#
# EL9 is exempt from (3), and that exemption is measured rather than assumed:
# an RPM's header is signed over build-host and build-time material, so two
# builds of an identical payload produce different file digests. Wave 2's
# equivalence run (30520146411) confirmed it across all six EL9 packages. The
# EL9 half is covered by (1) and (2), which is what the Debian half's version
# and revision assertions cover too -- the digest comparison is the extra.
#
# Also excluded from (3), for the same reason Wave 2 excluded them: .buildinfo,
# .changes and _source.changes. They record the build environment and the upload
# rather than the package, they carry a timestamp and the resolved buildroot
# package list by construction, and requiring them to match would be requiring
# two runs on two runners to have been the same runner. The registry records
# them for dict and redis because the run that recorded those entries had them;
# that is a wider record, not a wider claim.
#
# Recorded values come from `release_tool.py recorded-evidence`, which is the
# one reader of the evidence shape. This script computes digests and compares;
# it does not know how a manifest is laid out and must not learn.

set -eu

cohort=
target=
packages=

while [ $# -gt 0 ]; do
	case $1 in
	--cohort) cohort=${2:?}; shift 2 ;;
	--target) target=${2:?}; shift 2 ;;
	--packages) packages=${2:?}; shift 2 ;;
	--identity) identity=${2:?}; shift 2 ;;
	*) printf 'E: unknown argument %s\n' "$1" >&2; exit 2 ;;
	esac
done
identity=${identity:-}

for required in cohort target packages; do
	eval "value=\$$required"
	[ -n "$value" ] || { printf 'E: --%s is required\n' "$required" >&2; exit 2; }
done

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../.." && pwd)

note() { printf '\n===== %s =====\n' "$*"; }
fail=0
bad() { printf 'DRIFT: %s\n' "$*" >&2; fail=1; }
ok() { printf 'OK: %s\n' "$*"; }

recorded=$(mktemp)
# --no-cachetag-cross-check: this job has no libvmod-cachetag checkout, and the
# configure.ac cross-check already ran in this same run's per-VMOD plan job.
python3 "$repo/tools/release_tool.py" --no-cachetag-cross-check recorded-evidence \
	--cohort "$cohort" --target "$target" --format json > "$recorded"

note "1 -- the engine identity resolves to $cohort"
# The same reader both sides of every engine comparison in this project use;
# AGENTS.md makes it the ONE reader of the lane pin files, so this cannot
# disagree with what the engine rows verified.
if [ -n "$identity" ] && [ -f "$identity" ]; then
	printf 'identity file: %s\n' "$identity"
else
	identity=$(mktemp)
	case $target in
	*debian*) family=deb ;;
	*el9*)    family=rpm ;;
	*) printf 'E: cannot infer the package family from target %s\n' "$target" >&2; exit 2 ;;
	esac
	sh "$repo/scripts/ci/engine-identity.sh" "$family" > "$identity"
fi
# sed, not `.`: engine-identity.sh emits values containing spaces.
resolved=$(sed -n 's/^cohort_id=//p' "$identity" | head -1)
printf 'resolved cohort: %s\nexpected cohort: %s\n' "$resolved" "$cohort"
if [ "$resolved" = "$cohort" ]; then
	ok "the engine this run built against is the recorded cohort"
else
	bad "the engine resolves to cohort '$resolved', not '$cohort'.
The run measured a different cohort from the one whose evidence would be
updated. Do NOT record anything from it."
fi

note "2 -- recorded package versions and revisions against what was built"
# The built filenames are the assertion. A Debian binary package filename is
# <name>_<version>-<revision>_<arch>.deb by construction, so "the recorded
# version and revision are what was built" and "a file with the recorded name
# exists" are the same statement, and the second is the one that can be
# checked without parsing anything.
status=0
python3 - "$recorded" "$packages" <<'PY' || status=$?
import json, pathlib, sys

recorded = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
packages = pathlib.Path(sys.argv[2])
built = {p.name for p in packages.rglob("*") if p.is_file()}

status = 0
for vmod_id, entry in sorted(recorded["vmods"].items()):
    package = entry["package"]
    print(
        f"{vmod_id}: recorded {package['binary_name']} "
        f"{package['upstream_version']} revision {package['revision']}"
    )
    if entry["evidence"] != "recorded":
        print(f"  SKIP: evidence is '{entry['evidence']}'; there is nothing recorded to match")
        continue
    if not entry["artifacts"]:
        print("  DRIFT: the entry records no artifacts at all", file=sys.stderr)
        status = 1
        continue
    # Every recorded NATIVE package must have been built again under exactly
    # the same name. A version or revision that moved renames the file, which
    # is why this catches it without parsing a version out of anything.
    natives = sorted(
        name
        for name in entry["artifacts"]
        if name.endswith((".deb", ".rpm")) and not name.endswith(".src.rpm")
    )
    for name in natives:
        if name in built:
            print(f"  OK: {name}")
        else:
            print(f"  DRIFT: {name} is recorded but this run built no such file", file=sys.stderr)
            status = 1
sys.exit(status)
PY
if [ "$status" -eq 0 ]; then
	ok "every recorded native package was rebuilt under the same name"
else
	bad "a recorded package version or revision is not what this run built"
fi

note "3 -- Debian artifact digests against the recorded evidence"
case $target in
*el9*)
	printf 'EL9 target: the whole-RPM digest comparison does not apply.\n'
	printf 'An RPM header is signed over build-host and build-time material, so two\n'
	printf 'builds of an identical payload differ. Measured across all six EL9 packages\n'
	printf 'in run 30520146411. Assertions 1 and 2 cover this target.\n'
	;;
*)
	# The equivalence contract's scope, and nothing else. Extensions rather
	# than a filename list, so a fourth VMOD needs no edit here.
	status=0
	python3 - "$recorded" "$packages" <<-'PY' || status=$?
		import hashlib, json, pathlib, sys

		IN_SCOPE = (".deb", ".dsc", ".debian.tar.xz", ".orig.tar.gz")
		EXCLUDED = (".buildinfo", ".changes")

		recorded = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
		packages = pathlib.Path(sys.argv[2])
		built = {}
		for path in packages.rglob("*"):
		    if path.is_file():
		        built.setdefault(path.name, path)

		def digest(path):
		    h = hashlib.sha256()
		    with open(path, "rb") as handle:
		        for chunk in iter(lambda: handle.read(1 << 20), b""):
		            h.update(chunk)
		    return h.hexdigest()

		status = 0
		compared = skipped = 0
		for vmod_id, entry in sorted(recorded["vmods"].items()):
		    if entry["evidence"] != "recorded":
		        continue
		    for name, want in sorted(entry["artifacts"].items()):
		        if name.endswith(EXCLUDED):
		            skipped += 1
		            continue
		        if not name.endswith(IN_SCOPE):
		            skipped += 1
		            continue
		        path = built.get(name)
		        if path is None:
		            print(f"DRIFT: {vmod_id}: {name} recorded but not built", file=sys.stderr)
		            status = 1
		            continue
		        got = digest(path)
		        compared += 1
		        if got == want:
		            print(f"OK: {vmod_id}: {name}")
		        else:
		            print(
		                f"DRIFT: {vmod_id}: {name}\n"
		                f"       built    {got}\n"
		                f"       recorded {want}",
		                file=sys.stderr,
		            )
		            status = 1
		print(f"\ncompared {compared} artifact(s); {skipped} outside the equivalence contract")
		if compared == 0:
		    print(
		        "DRIFT: nothing was compared. A cross-check that checks nothing passes\n"
		        "       vacuously, which is the failure this line exists to prevent.",
		        file=sys.stderr,
		    )
		    status = 1
		sys.exit(status)
	PY
	if [ "$status" -eq 0 ]; then
		ok "every in-scope Debian artifact byte-matches its recorded digest"
	else
		bad "a Debian artifact does not match the digest the registry records"
	fi
	;;
esac

rm -f "$recorded"

note "verdict"
if [ "$fail" -eq 0 ]; then
	printf 'IDENTITY CROSS-CHECK PASSED: this run built the recorded packages of\n'
	printf 'cohort %s on %s.\n' "$cohort" "$target"
	exit 0
fi
printf 'IDENTITY CROSS-CHECK FAILED.\n\n' >&2
printf 'Drift is a STOP, not a note. Whatever this run measured, it did not measure\n' >&2
printf 'the packages the registry describes, so nothing from it may be recorded as\n' >&2
printf 'evidence for cohort %s. Establish what moved -- an input, a pin, a package\n' "$cohort" >&2
printf 'revision -- before dispatching again. Do NOT update a recorded digest to\n' >&2
printf 'make this pass.\n' >&2
exit 1
