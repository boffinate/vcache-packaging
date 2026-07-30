#!/bin/sh
#
# Debian 13 (trixie) lane: the upgrade-transaction matrix required by the
# "Upgrade transaction safety" section and the Phase 3 acceptance criterion
# "every documented upgrade command has a tested, documented resolver outcome,
# and the supported path never silently removes an imported VMOD".
#
# Each scenario runs in its OWN throwaway container, so no outcome can
# contaminate the next. Inside, container/stage-transactions.sh installs the
# retained baseline cohort through a local apt repository, publishes the
# synthetic candidate produced by mismatch-fixture.sh into that same
# repository, and then runs exactly one transaction command.
#
# Usage:
#   recipes/debian-13/transactions.sh                 run the whole matrix
#   recipes/debian-13/transactions.sh s04 s07         run named scenarios
#   recipes/debian-13/transactions.sh --list          list the matrix
#   recipes/debian-13/transactions.sh --summary       re-print the summary
#                                                     from existing results
#
# Prerequisites: recipes/debian-13/build.sh (the baseline cohort) and
# recipes/debian-13/mismatch-fixture.sh (the candidates).
#
# Output: dist/debian-13/logs/transactions/<scenario>.log with the complete apt
# output, <scenario>.result with the machine-readable classification, and
# dist/debian-13/logs/transactions/SUMMARY.tsv.
#
# Environment, all defaulted so the cachetag invocation above is unchanged:
#   TXN_OUT_DIR      the directory mounted at /out. It must contain the baseline
#                    cohort debs at its top level, their SHA256SUMS, and is where
#                    mismatch/ and logs/transactions/ are read and written.
#                    Defaults to dist/debian-13, the lane's own layout; the
#                    reusable workflow points it at a staging directory instead,
#                    because a generated VMOD's packages live in lane/out and the
#                    engine's in lane/engine.
#   DEB_HOST_ARCH    read from $TXN_OUT_DIR/work/target.txt when unset
#   VINYL_VMODDIR    read from $TXN_OUT_DIR/work/target.txt when unset
#   VMOD_PACKAGE     the VMOD binary package name under test (libvmod-cachetag)
#   VMOD_VERSION     its Debian version (pins.env's CACHETAG_DEBIAN_VERSION)
#   VMOD_IMPORT      its VCL import token (cachetag)
#   VMOD_SO          its installed shared object (libvmod_cachetag.so)
#   VMOD_PROBE_VCL   container path of the probe VCL; empty composes a bare
#                    `import VMOD_IMPORT` probe (see container/stage-transactions.sh)

set -eu

# Read from the single definition of the lane's pinned inputs -- pins.env
# selects the baseline on VINYL_TRACK and derives the synthetic candidate
# versions from it. Until 2026-07-28 this script mirrored the trunk values by
# hand, which silently pinned the whole matrix to the trunk baseline and made
# a release-track run impossible. IMAGE also comes from pins.env (same
# env-overridable IMAGE_REF/IMAGE_DIGEST defaults this file used to restate).
. "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/pins.env"

BASE_ABI=$VINYL_STRICT_ABI
BASE_VERSION=$VINYL_PACKAGE_VERSION

# Which VMOD is installed alongside the engine candidates. The scenario table
# below acts on the ENGINE, so none of it changes per VMOD; only the assertions
# about what survived do, and those are the four values here. The defaults are
# libvmod-cachetag's, so this lane's own invocation is what it always was.
VMOD_PACKAGE=${VMOD_PACKAGE:-libvmod-cachetag}
VMOD_VERSION=${VMOD_VERSION:-$CACHETAG_DEBIAN_VERSION}
VMOD_IMPORT=${VMOD_IMPORT:-cachetag}
VMOD_SO=${VMOD_SO:-libvmod_cachetag.so}
VMOD_PROBE_VCL=${VMOD_PROBE_VCL-/stage/probe-cachetag.vcl}

# Scenario containers start from a derived image rather than from the pinned
# base directly. It is the pinned base, fully dist-upgraded once, with the
# baseline cohort's own runtime dependencies already present. That is not a
# shortcut around the test: the relations under test are between vinyl-cache,
# vinyl-cache-dev and the VMOD package, and pre-resolving Debian's own packages
# keeps `apt upgrade` output free of unrelated base-system churn that would
# otherwise make every scenario log ambiguous. It also avoids re-downloading
# ~200 MB of Debian per scenario.
#
# The image carries the ENGINE's runtime dependencies. A VMOD whose package needs
# a distribution library the engine does not is still installed by apt from the
# local repository in each scenario, so the resolver sees the same relations; it
# only costs that scenario a download.
BASE_IMAGE_TAG=${BASE_IMAGE_TAG:-vinyl-txn-base-debian13:1}

recipe_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$recipe_dir/../.." && pwd)

out_dir=${TXN_OUT_DIR:-$repo_dir/dist/debian-13}
log_dir=$out_dir/logs/transactions
mismatch_dir=$out_dir/mismatch

note() { printf '\n===== %s =====\n' "$*"; }
die() { printf 'E: %s\n' "$*" >&2; exit 1; }

# target.txt is build.sh's record of what the lane built for, and stays the
# default source of both values. A staging directory assembled from an engine
# artifact has no build tree to have written one, so either may be supplied
# directly; nothing else about the scenarios changes.
DEB_HOST_ARCH=${DEB_HOST_ARCH:-$(sed -n 1p "$out_dir/work/target.txt" 2>/dev/null || true)}
VINYL_VMODDIR=${VINYL_VMODDIR:-$(sed -n 3p "$out_dir/work/target.txt" 2>/dev/null || true)}
[ -n "$DEB_HOST_ARCH" ] || die "no DEB_HOST_ARCH, and none in $out_dir/work/target.txt"
[ -n "$VINYL_VMODDIR" ] || die "no VINYL_VMODDIR, and none in $out_dir/work/target.txt"

###############################################################################
# THE MATRIX
#
# One record per line: id | candidate | with-dev | pre-step | transaction
#
# The plan's documented transaction list for apt is `apt upgrade`,
# `apt full-upgrade` and direct installation of the candidate Vinyl package.
# Each of those is run both in its non-interactive-confirmation form and in the
# form an administrator actually types, because apt's answer to "what would you
# do" and "do it" are not required to agree. `apt-get upgrade` and
# `apt-get dist-upgrade` are included because unattended tooling still calls
# them and they are NOT synonyms of the apt equivalents.
###############################################################################

matrix() {
	cat <<-EOF
	s01-control-apt-upgrade|none|0||apt upgrade -y
	s02-apt-upgrade-mismatch|mismatch|0||apt upgrade -y
	s03-apt-full-upgrade-mismatch-prompt|mismatch|0||apt full-upgrade
	s04-apt-full-upgrade-mismatch-yes|mismatch|0||apt full-upgrade -y
	s05-apt-install-candidate-prompt|mismatch|0||apt install vinyl-cache=$MISMATCH_VERSION
	s06-apt-install-candidate-yes|mismatch|0||apt install -y vinyl-cache=$MISMATCH_VERSION
	s07-apt-mark-hold-full-upgrade|mismatch|0|apt-mark hold vinyl-cache|apt full-upgrade -y
	s08-apt-pin-full-upgrade|mismatch|0|printf 'Package: vinyl-cache vinyl-cache-dev\nPin: version $MISMATCH_VERSION\nPin-Priority: -1\n' > /etc/apt/preferences.d/90-vinyl-cohort-freeze|apt full-upgrade -y
	s09-apt-get-upgrade-mismatch|mismatch|0||apt-get upgrade -y
	s10-apt-get-dist-upgrade-mismatch|mismatch|0||apt-get dist-upgrade -y
	s11-dev-installed-full-upgrade|mismatch|1||apt full-upgrade -y
	s12-sameabi-apt-upgrade|sameabi|0||apt upgrade -y
	s13-sameabi-full-upgrade|sameabi|0||apt full-upgrade -y
	s14-sameabi-install-candidate|sameabi|0||apt install -y vinyl-cache=$SAMEABI_VERSION
	s15-hold-vs-direct-install|mismatch|0|apt-mark hold vinyl-cache|apt install -y vinyl-cache=$MISMATCH_VERSION
	s16-pin-vs-direct-install|mismatch|0|printf 'Package: vinyl-cache vinyl-cache-dev\nPin: version $MISMATCH_VERSION\nPin-Priority: -1\n' > /etc/apt/preferences.d/90-vinyl-cohort-freeze|apt install -y vinyl-cache=$MISMATCH_VERSION
	EOF
}

if [ "${1:-}" = "--list" ]; then
	matrix | while IFS='|' read -r id cand dev pre tx; do
		printf '%-38s candidate=%-9s dev=%s  %s\n' "$id" "$cand" "$dev" "$tx"
	done
	exit 0
fi

###############################################################################

check_inputs() {
	[ -f "$out_dir/vinyl-cache_${BASE_VERSION}_${DEB_HOST_ARCH}.deb" ] ||
		die "baseline vinyl-cache deb missing; run recipes/debian-13/build.sh"
	[ -f "$out_dir/${VMOD_PACKAGE}_${VMOD_VERSION}_${DEB_HOST_ARCH}.deb" ] ||
		die "baseline $VMOD_PACKAGE deb missing from $out_dir"
	[ -f "$mismatch_dir/SHA256SUMS" ] ||
		die "no fixtures; run recipes/debian-13/mismatch-fixture.sh"
	for _v in "$MISMATCH_VERSION" "$SAMEABI_VERSION"; do
		for _p in vinyl-cache vinyl-cache-dev; do
			[ -f "$mismatch_dir/${_p}_${_v}_${DEB_HOST_ARCH}.deb" ] ||
				die "fixture missing: ${_p}_${_v}_${DEB_HOST_ARCH}.deb"
		done
	done
	note "verifying the fixtures against dist/debian-13/mismatch/SHA256SUMS"
	( cd "$mismatch_dir" && while read -r _sum _file; do
		_got=$(shasum -a 256 "$_file" 2>/dev/null | awk '{print $1}')
		[ -n "$_got" ] || _got=$(sha256sum "$_file" | awk '{print $1}')
		[ "$_got" = "$_sum" ] || { printf 'E: %s digest mismatch\n' "$_file" >&2; exit 1; }
		printf 'OK: %s  %s\n' "$_sum" "$_file"
	done < SHA256SUMS )
}

build_base_image() {
	if docker image inspect "$BASE_IMAGE_TAG" >/dev/null 2>&1; then
		note "scenario base image $BASE_IMAGE_TAG already present"
		return 0
	fi
	note "building the scenario base image $BASE_IMAGE_TAG from $IMAGE"
	_cid=$(docker run -d "$IMAGE" bash -c '
		set -e
		export DEBIAN_FRONTEND=noninteractive
		apt-get update -qq
		apt-get -y dist-upgrade
		apt-get install -y --no-install-recommends \
			dpkg-dev python3 curl ca-certificates procps \
			adduser gcc libc6-dev libedit2 libncursesw6 libpcre2-8-0 \
			libtinfo6 libunwind8 pkgconf
		apt-get clean')
	docker logs -f "$_cid" > "$out_dir/logs/transactions-base-image.log" 2>&1 || true
	_rc=$(docker wait "$_cid")
	if [ "$_rc" != 0 ]; then
		tail -n 40 "$out_dir/logs/transactions-base-image.log" >&2
		docker rm -f "$_cid" >/dev/null 2>&1 || true
		die "scenario base image build failed"
	fi
	docker commit "$_cid" "$BASE_IMAGE_TAG" >/dev/null
	docker rm -f "$_cid" >/dev/null
	printf 'built %s\n' "$BASE_IMAGE_TAG"
}

run_scenario() {
	_id=$1; _cand=$2; _dev=$3; _pre=$4; _tx=$5
	case $_cand in
	mismatch) _cver=$MISMATCH_VERSION ;;
	sameabi)  _cver=$SAMEABI_VERSION ;;
	none)     _cver= ;;
	*) die "unknown candidate variant: $_cand" ;;
	esac

	note "scenario $_id: $_tx"
	rm -f "$log_dir/$_id.result"
	docker run --rm \
		-v "$recipe_dir/container:/stage:ro" \
		-v "$out_dir:/out" \
		-e "SCENARIO=$_id" \
		-e "TRANSACTION=$_tx" \
		-e "CANDIDATE_VARIANT=$_cand" \
		-e "CANDIDATE_VERSION=$_cver" \
		-e "BASE_VERSION=$BASE_VERSION" \
		-e "BASE_ABI=$BASE_ABI" \
		-e "VMOD_PACKAGE=$VMOD_PACKAGE" \
		-e "VMOD_VERSION=$VMOD_VERSION" \
		-e "VMOD_IMPORT=$VMOD_IMPORT" \
		-e "VMOD_SO=$VMOD_SO" \
		-e "VMOD_PROBE_VCL=$VMOD_PROBE_VCL" \
		-e "DEB_HOST_ARCH=$DEB_HOST_ARCH" \
		-e "VINYL_VMODDIR=$VINYL_VMODDIR" \
		-e "WITH_DEV=$_dev" \
		-e "PRE_STEP=$_pre" \
		"$BASE_IMAGE_TAG" bash /stage/stage-transactions.sh \
		> "$log_dir/$_id.log" 2>&1 || {
			tail -n 40 "$log_dir/$_id.log" >&2
			die "scenario $_id did not complete (see $log_dir/$_id.log)"
		}
	[ -f "$log_dir/$_id.result" ] || die "scenario $_id produced no result block"
	sed -n 's/^RESULT //p' "$log_dir/$_id.result" |
		sed -n '/^outcome=/p;/^exit=/p;/^vinyl=/p;/^vmod=/p;/^vcl_compile=/p;/^needs_warning=/p' |
		sed 's/^/  /'
}

summarise() {
	note "summary"
	{
		printf 'scenario\tcandidate\tcommand\texit\tvinyl\tvmod\tvmod_so\tvcl_compile\toutcome\twarn\n'
		matrix | while IFS='|' read -r id cand dev pre tx; do
			_f=$log_dir/$id.result
			[ -f "$_f" ] || continue
			_get() { sed -n "s/^RESULT $1=//p" "$_f"; }
			printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
				"$id" "$(_get candidate)" "$(_get command)" "$(_get exit)" \
				"$(_get vinyl)" "$(_get vmod)" "$(_get vmod_so)" \
				"$(_get vcl_compile)" "$(_get outcome)" "$(_get needs_warning)"
		done
	} > "$log_dir/SUMMARY.tsv"
	awk -F'\t' '{ printf "%-38s %-9s %-26s %-3s %-24s %s\n", $1, $2, $9, $4, $6, $10 }' \
		"$log_dir/SUMMARY.tsv"
	printf '\nfull table: %s\n' "$log_dir/SUMMARY.tsv"
	printf 'per-scenario apt output: %s/<scenario>.log\n' "$log_dir"
}

mkdir -p "$log_dir"

if [ "${1:-}" = "--summary" ]; then
	summarise
	exit 0
fi

note "VMOD under test: $VMOD_PACKAGE $VMOD_VERSION (import $VMOD_IMPORT, $VMOD_SO)"
printf 'packages and evidence directory: %s\n' "$out_dir"

check_inputs
build_base_image

wanted=${*:-all}
matrix | while IFS='|' read -r id cand dev pre tx; do
	_run=0
	for w in $wanted; do
		case $w in
		all) _run=1 ;;
		*) case $id in $w*) _run=1 ;; esac ;;
		esac
	done
	[ "$_run" = 1 ] || continue
	run_scenario "$id" "$cand" "$dev" "$pre" "$tx"
done

summarise
