#!/bin/sh
#
# Debian 13 (trixie) lane: build the synthetic mismatched Vinyl candidate
# package fixtures required by the "Upgrade transaction safety" section of the
# binary packaging and distribution plan.
#
#   "Use the cryptographically pinned previous supported cohort as the normal
#    mismatch fixture rather than building a second Vinyl on every run. Before
#    a previous cohort exists, create one synthetic mismatched package fixture
#    per release line and retain its source and digest."
#
# There is no previous supported cohort yet -- the Debian 13 lane produced the
# first one today -- so this script mints the synthetic fixture for the 9.0.0
# snapshot release line.
#
# Two variants, both versioned ABOVE the baseline cohort so a package manager
# treats them as upgrade candidates:
#
#   mismatch   different vinyld-abi-<hash>. Simulates an incompatible Vinyl
#              security upgrade: the exact-ABI dependency of an installed VMOD
#              can no longer be satisfied. This is the fixture the transaction
#              matrix is really about.
#
#   sameabi    the SAME vinyld-abi-<hash> as the baseline, different version,
#              different payload and a DIFFERENT vinyld-cohort-<id>. This is
#              the plan's stated known limitation -- "an exact vinyld-abi-<hash>
#              dependency alone cannot distinguish two Vinyl packages that
#              advertise the same baked-in ABI string but contain different
#              downstream patches" -- turned into something testable, and since
#              2026-07-25 it is also the regression test for the cohort-
#              qualified provide that closes it.
#
# Both are produced by a scripted metadata-level transformation of the retained
# baseline cohort debs rather than by a second Vinyl compile. The justification
# is in container/make-mismatch.sh; in short, the payload stays byte-identical
# to the audited baseline so that any resolver behaviour difference is
# attributable to the metadata change alone, and the result is still a real,
# installable deb that every transaction scenario installs for real.
#
# Everything runs inside the pinned debian:trixie container. No host package is
# installed and the host only reads and writes dist/debian-13/.
#
# Usage:
#   recipes/debian-13/mismatch-fixture.sh [mismatch|sameabi ...]     (default: both)
#
# Output: dist/debian-13/mismatch/, including SHA256SUMS.
#
# Environment, all defaulted so the cachetag invocation is unchanged:
#   TXN_OUT_DIR      the directory holding the baseline cohort debs and their
#                    SHA256SUMS, and where mismatch/ is written. Defaults to
#                    dist/debian-13; the reusable workflow points it at a staging
#                    directory, because a generated VMOD's package lives in
#                    lane/out and the engine's in lane/engine.
#   DEB_HOST_ARCH    read from $TXN_OUT_DIR/work/target.txt when unset
#   VMOD_PACKAGE     the VMOD binary package name whose deb is part of the
#                    baseline cohort (libvmod-cachetag)
#   VMOD_VERSION     its Debian version (pins.env's CACHETAG_DEBIAN_VERSION)
#
# The fixture variants themselves are ENGINE packages and carry no VMOD name:
# container/make-mismatch.sh repacks vinyl-cache and vinyl-cache-dev only. The
# VMOD is named here solely so its deb is digest-verified along with them before
# anything is derived from the directory.

set -eu

###############################################################################
# BASELINE COHORT IDENTITY
#
# These must match what recipes/debian-13/build.sh produced and what
# dist/debian-13/SHA256SUMS records. The script asserts the baseline debs exist
# and that their digests still match SHA256SUMS before transforming them: a
# fixture derived from an unrecorded input would be worthless as evidence.
###############################################################################

# Read from the single definition of the lane's pinned inputs rather than
# mirrored here. Until 2026-07-26 these were three hand-written copies, and the
# cohort mint moved COHORT_ID in pins.env while this file kept saying
# 'unassigned-local-process-proof' -- a divergence that turns every assertion
# below into a false negative, because the fixture would be derived from a
# baseline it no longer describes.
. "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/pins.env"

BASE_ABI=$VINYL_STRICT_ABI
BASE_VERSION=$VINYL_PACKAGE_VERSION
# It is what the baseline runtime advertises as vinyld-cohort-<id>, and the
# transformation below asserts it is present before rewriting it.
BASE_COHORT=$COHORT_ID

VMOD_PACKAGE=${VMOD_PACKAGE:-libvmod-cachetag}
VMOD_VERSION=${VMOD_VERSION:-$CACHETAG_DEBIAN_VERSION}

###############################################################################
# SYNTHETIC FIXTURE IDENTITY
#
# The commit-like tokens are deliberately impossible-looking (all-f, all-e) and
# the strict ABI hash of the mismatch variant is 40 f's, which no Git object id
# will ever be in practice. Combined with the SYNTHETIC FIXTURE banner in the
# package Description and the SYNTHETIC-FIXTURE.txt marker file inside each
# package, a fixture cannot be mistaken for a real build on an installed
# system.
#
# Version ordering matters and is asserted below. The candidate versions come
# from pins.env's derivation, which keys them off the active track's baseline
# (a later snapshot of the same version on trunk, a pre-release snapshot of
# the next patch release on the release track), so on either track:
#     baseline < mismatch < sameabi
#
# Because sameabi sorts above mismatch, the two variants are never placed in
# the same apt repository: transactions.sh publishes exactly one candidate per
# scenario.
###############################################################################

MISMATCH_ABI=ffffffffffffffffffffffffffffffffffffffff
MISMATCH_COHORT=mismatch-fixture-ffffffffffff

SAMEABI_ABI=$BASE_ABI
# Deliberately NOT the baseline cohort. The whole point of this variant is a
# package that copies the baked-in ABI string while coming from somewhere else:
# a distro backport, a vendor respin, a rebuild with a different patch series.
# Before the cohort-qualified provide existed, apt upgraded it cleanly through
# every path tested (scenarios s12 to s14). With it, the same package is an
# unsatisfiable upgrade, which is the intended direction.
SAMEABI_COHORT=sameabi-fixture-eeeeeeeeeeee

# Fixed timestamp for the fixture build. The plan asks for the fixture's digest
# to be retained; a digest is only worth retaining if regenerating the fixture
# reproduces it. This is the Vinyl commit epoch the Debian 13 lane already uses
# as SOURCE_DATE_EPOCH, so the fixture is dated to the cohort it derives from
# rather than to whenever someone happened to run this script.
FIXTURE_SOURCE_DATE_EPOCH=${FIXTURE_SOURCE_DATE_EPOCH:-1779265093}

IMAGE_REF=${IMAGE_REF:-debian:trixie}
IMAGE_DIGEST=${IMAGE_DIGEST:-sha256:fac46bff2e02f51425b6e33b0e1169f55dfb053d83511ca28aa50c09fd5ed7a4}
IMAGE="$IMAGE_REF@$IMAGE_DIGEST"

recipe_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$recipe_dir/../.." && pwd)

out_dir=${TXN_OUT_DIR:-$repo_dir/dist/debian-13}
log_dir=$out_dir/logs
mismatch_dir=$out_dir/mismatch

note() { printf '\n===== %s =====\n' "$*"; }
die() { printf 'E: %s\n' "$*" >&2; exit 1; }

[ -d "$out_dir" ] || die "no baseline cohort in $out_dir; run recipes/debian-13/build.sh first"
[ -f "$out_dir/SHA256SUMS" ] || die "no $out_dir/SHA256SUMS; the baseline cohort is unusable as a fixture source"

DEB_HOST_ARCH=${DEB_HOST_ARCH:-$(sed -n 1p "$out_dir/work/target.txt" 2>/dev/null || true)}
[ -n "$DEB_HOST_ARCH" ] || die "no DEB_HOST_ARCH, and none in $out_dir/work/target.txt"

# Both directories are created here, by the host, before any container runs.
# container/make-mismatch.sh would create dist/debian-13/mismatch/ itself, as
# root, and on a Linux runner the host user then cannot add PROVENANCE to it --
# which is exactly how this failed on its first CI run
# (nightly-transactions.yml run 30192509993: "cannot create .../PROVENANCE:
# Permission denied", after every fixture had already been built correctly).
# It never showed up locally because Docker Desktop maps bind-mount ownership
# to the calling user.
mkdir -p "$log_dir" "$mismatch_dir"

###############################################################################
# Verify the fixture source before deriving anything from it.
###############################################################################

note "verifying the baseline cohort debs against $out_dir/SHA256SUMS"
for _pkg in vinyl-cache vinyl-cache-dev "$VMOD_PACKAGE"; do
	case $_pkg in
	"$VMOD_PACKAGE") _v=$VMOD_VERSION ;;
	*)               _v=$BASE_VERSION ;;
	esac
	_deb=${_pkg}_${_v}_${DEB_HOST_ARCH}.deb
	[ -f "$out_dir/$_deb" ] || die "baseline deb missing: $out_dir/$_deb"
	_want=$(awk -v f="$_deb" '$2 == f { print $1 }' "$out_dir/SHA256SUMS")
	[ -n "$_want" ] || die "$_deb is not recorded in dist/debian-13/SHA256SUMS"
	_got=$(shasum -a 256 "$out_dir/$_deb" 2>/dev/null | awk '{print $1}')
	[ -n "$_got" ] || _got=$(sha256sum "$out_dir/$_deb" | awk '{print $1}')
	[ "$_got" = "$_want" ] || die "$_deb digest $_got != recorded $_want"
	printf 'OK: %s  %s\n' "$_want" "$_deb"
done

###############################################################################
# Assert the synthetic versions really do sort above the baseline. A fixture
# that sorted below it would silently turn every upgrade scenario into a no-op
# and the matrix would "pass" while testing nothing.
###############################################################################

note "asserting synthetic version ordering with dpkg --compare-versions"
docker run --rm "$IMAGE" bash -c '
	set -e
	base=$1; mism=$2; same=$3
	for pair in "$base $mism" "$base $same" "$mism $same"; do
		set -- $pair
		if dpkg --compare-versions "$1" lt "$2"; then
			echo "OK: $1 < $2"
		else
			echo "E: $1 is not less than $2" >&2; exit 1
		fi
	done' _ "$BASE_VERSION" "$MISMATCH_VERSION" "$SAMEABI_VERSION"

###############################################################################

build_variant() {
	_variant=$1; _version=$2; _abi=$3; _cohort=$4
	note "building fixture variant: $_variant ($_version, vinyld-abi-$_abi, vinyld-cohort-$_cohort)"
	docker run --rm \
		-v "$recipe_dir/container:/stage:ro" \
		-v "$out_dir:/out" \
		-e "FIXTURE_VARIANT=$_variant" \
		-e "FIXTURE_VERSION=$_version" \
		-e "FIXTURE_ABI=$_abi" \
		-e "FIXTURE_COHORT=$_cohort" \
		-e "BASE_VERSION=$BASE_VERSION" \
		-e "BASE_ABI=$BASE_ABI" \
		-e "BASE_COHORT=$BASE_COHORT" \
		-e "DEB_HOST_ARCH=$DEB_HOST_ARCH" \
		-e "SOURCE_DATE_EPOCH=$FIXTURE_SOURCE_DATE_EPOCH" \
		"$IMAGE" bash /stage/make-mismatch.sh \
		> "$log_dir/mismatch-$_variant.log" 2>&1 || {
			tail -n 60 "$log_dir/mismatch-$_variant.log" >&2
			die "fixture variant $_variant failed (see $log_dir/mismatch-$_variant.log)"
		}
	cat "$log_dir/mismatch-$_variant.log"
}

variants=${*:-mismatch sameabi}
for v in $variants; do
	case $v in
	mismatch) build_variant mismatch "$MISMATCH_VERSION" "$MISMATCH_ABI" "$MISMATCH_COHORT" ;;
	sameabi)  build_variant sameabi  "$SAMEABI_VERSION"  "$SAMEABI_ABI"  "$SAMEABI_COHORT" ;;
	*)        die "unknown variant: $v" ;;
	esac
done

###############################################################################
# Provenance: retain the source and the digest, as the plan requires.
###############################################################################

note "writing the fixture provenance manifest"
{
	printf '# Synthetic mismatched Vinyl candidate fixtures, Debian 13 lane\n'
	printf '#\n'
	printf '# Generated by vcache-packaging/recipes/debian-13/mismatch-fixture.sh\n'
	printf '# Buildroot: %s\n' "$IMAGE"
	printf '# SOURCE_DATE_EPOCH: %s (fixed, so the fixture digests reproduce)\n' \
		"$FIXTURE_SOURCE_DATE_EPOCH"
	printf '# This manifest written: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	printf '#\n'
	printf '# Fixture source (the retained baseline cohort, digests re-verified\n'
	printf '# against %s/SHA256SUMS at generation time):\n' "$out_dir"
	for _pkg in vinyl-cache vinyl-cache-dev; do
		_deb=${_pkg}_${BASE_VERSION}_${DEB_HOST_ARCH}.deb
		printf '#   %s  %s\n' \
			"$(awk -v f="$_deb" '$2 == f { print $1 }' "$out_dir/SHA256SUMS")" "$_deb"
	done
	printf '#\n'
	printf '# Baseline version:  %s\n' "$BASE_VERSION"
	printf '# Baseline ABI:      vinyld-abi-%s\n' "$BASE_ABI"
	printf '# Baseline cohort:   vinyld-cohort-%s\n' "$BASE_COHORT"
	printf '# mismatch variant:  %s  vinyld-abi-%s  vinyld-cohort-%s\n' \
		"$MISMATCH_VERSION" "$MISMATCH_ABI" "$MISMATCH_COHORT"
	printf '# sameabi variant:   %s  vinyld-abi-%s  vinyld-cohort-%s\n' \
		"$SAMEABI_VERSION" "$SAMEABI_ABI" "$SAMEABI_COHORT"
	printf '#\n'
	printf '# Both variants advertise a cohort id of their own. For sameabi that is the\n'
	printf '# case under test: identical baked-in ABI string, different provenance.\n'
	printf '#\n'
	printf '# Transformation: dpkg-deb -R, add usr/share/doc/<pkg>/SYNTHETIC-FIXTURE.txt,\n'
	printf '# rewrite the control Version / Provides (ABI and cohort) / exact-runtime\n'
	printf '# Depends / Installed-Size / Description banner, append the marker to md5sums,\n'
	printf '# dpkg-deb --build --root-owner-group. Payload otherwise byte-identical.\n'
	printf '#\n'
	printf '# NOT REAL BUILDS. Never publish these to a user-facing repository.\n'
} > "$mismatch_dir/PROVENANCE"

note "fixtures"
ls -la "$mismatch_dir"
cat "$mismatch_dir/SHA256SUMS"
