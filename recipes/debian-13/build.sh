#!/bin/sh
#
# Debian 13 (trixie) lane: build the coordinated Vinyl Cache 9 + cachetag
# package cohort, lint it, and run the installed-package smoke test.
#
# Everything that compiles, installs or resolves a package runs inside a
# debian:trixie container. The host is used only to read the pinned Git
# checkouts with `git archive` (a read-only operation), to assemble the source
# tarballs, and to substitute recipe tokens. No host package is installed.
#
# Usage:
#   recipes/debian-13/build.sh [stage ...]
#
# Stages, in order, default "all":
#   source     assemble the pinned Vinyl source archive and both orig tarballs
#   vinyl      build the Vinyl Cache 9 Debian source and binary packages
#   cachetag   build libvmod-cachetag against the INSTALLED vinyl-cache-dev
#   lint       run lintian over every produced package
#   smoke      the plan's 11-step installed-package scenario, fresh container
#
# Artifacts land in dist/debian-13/, logs in dist/debian-13/logs/.

set -eu

###############################################################################
# PINNED INPUTS
#
# These are the compatibility inputs of the cohort. They belong in the cohort
# registry (vcache-packaging/registry/), and they will be read from there as
# soon as a `candidate` cohort manifest exists; the checked-in cohort is still
# a `template` with placeholder identity values, so for this first process-
# proof run they are stated here, once, and every one of them is *asserted*
# against the built artifacts rather than merely copied into metadata.
###############################################################################

# Vinyl Cache source revision. The strict VMOD ABI token is, by construction,
# this commit id: include/generate.py writes VMOD_ABI_Version as
# "<PACKAGE_STRING> <commit>".
VINYL_GIT_COMMIT=a90954814766d933a75d4c808c449cb9bc0ae3d3
VINYL_STRICT_ABI=$VINYL_GIT_COMMIT
VINYL_ABI_STRING="Vinyl Cache trunk $VINYL_GIT_COMMIT"
VTEST2_GIT_COMMIT=db5ccb4a078da40b3ec1ca3c18bf498bb1520888

# Digest of the canonical pinned Vinyl source archive, as produced by the
# identical assembly procedure in libvmod-cachetag/scripts/release-source-archive.sh
# and recorded in release/dist/libvmod-cachetag-1.0.0.metadata.json.
VINYL_SOURCE_SHA256=2587f03289b3e16d36b4b688def4b78fb5af07a9aacc620a55e094a5c0f6ee15

# Snapshot version convention, shared with the EL9 lane. This is a pre-9.0.0
# experimental snapshot: Vinyl's own AC_INIT still says "trunk".
VINYL_UPSTREAM_VERSION=9.0.0~git20260613.a909548147
VINYL_PACKAGE_REVISION=1
VINYL_PACKAGE_VERSION=$VINYL_UPSTREAM_VERSION-$VINYL_PACKAGE_REVISION

# Canonical cachetag source archive. Package jobs consume this, never a Git
# checkout.
CACHETAG_VERSION=1.0.0
CACHETAG_PACKAGE_REVISION=1
CACHETAG_DEBIAN_VERSION=$CACHETAG_VERSION-$CACHETAG_PACKAGE_REVISION
CACHETAG_SOURCE_SHA256=c7054e69219ff3c54501d9c68857f2117944c4658db4cb08e2821b09b27821a2
CACHETAG_SOURCE_DATE_EPOCH=1784926281

# Vinyl commit timestamp, used as SOURCE_DATE_EPOCH for the Vinyl lane.
VINYL_SOURCE_DATE_EPOCH=1781307021

DEBIAN_DISTRIBUTION=trixie

# The maintainer address is real but deliberately does not accept mail; the
# support channel is the issue tracker reachable via each package's
# Homepage/Vcs fields. Decided 2026-07-25 (packaging plan step 2 identity).
MAINTAINER_NAME='Boffinate'
MAINTAINER_EMAIL='noreply@boffinate.com'

# No cohort identifier is minted here. The registry assigns it; this build
# reports every input the assignment needs.
#
# The value is baked into the runtime package's vinyld-cohort-<id> provide and
# into cachetag's dependency on it, so it has to be usable inside a Debian
# package name: [a-z0-9] first, then [a-z0-9+.-]. debian/rules asserts that
# before emitting the substvar rather than letting dpkg-gencontrol produce a
# malformed relation.
COHORT_ID='unassigned-local-process-proof'

SOURCE_URL="https://github.com/boffinate/libvmod-cachetag/releases/download/v$CACHETAG_VERSION/libvmod-cachetag-$CACHETAG_VERSION.tar.gz"

# Buildroot identity, pinned by digest as the plan requires.
IMAGE_REF=${IMAGE_REF:-debian:trixie}
IMAGE_DIGEST=${IMAGE_DIGEST:-sha256:fac46bff2e02f51425b6e33b0e1169f55dfb053d83511ca28aa50c09fd5ed7a4}
IMAGE="$IMAGE_REF@$IMAGE_DIGEST"

###############################################################################

recipe_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$recipe_dir/../.." && pwd)
ws_dir=$(CDPATH= cd -- "$repo_dir/.." && pwd)

VINYL_SRC=${VINYL_SRC:-$ws_dir/vinyl-cache}
CACHETAG_SRC=${CACHETAG_SRC:-$ws_dir/libvmod-cachetag}
CACHETAG_TARBALL=$CACHETAG_SRC/release/dist/libvmod-cachetag-$CACHETAG_VERSION.tar.gz

out_dir=$repo_dir/dist/debian-13
log_dir=$out_dir/logs
work_dir=$out_dir/work

note() { printf '\n===== %s =====\n' "$*"; }
die() { printf 'E: %s\n' "$*" >&2; exit 1; }

sha256() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{print $1}'
	else
		shasum -a 256 "$1" | awk '{print $1}'
	fi
}

run_in_container() {
	# run_in_container <log-name> <script> [docker args ...]
	_log=$1; _script=$2; shift 2
	note "container stage: $_log (log: $log_dir/$_log.log)"
	docker run --rm \
		-v "$recipe_dir/container:/stage:ro" \
		-v "$out_dir:/out" \
		-e "VINYL_UPSTREAM_VERSION=$VINYL_UPSTREAM_VERSION" \
		-e "VINYL_PACKAGE_VERSION=$VINYL_PACKAGE_VERSION" \
		-e "VINYL_STRICT_ABI=$VINYL_STRICT_ABI" \
		-e "VINYL_ABI_STRING=$VINYL_ABI_STRING" \
		-e "COHORT_ID=$COHORT_ID" \
		-e "VINYL_SOURCE_DATE_EPOCH=$VINYL_SOURCE_DATE_EPOCH" \
		-e "CACHETAG_VERSION=$CACHETAG_VERSION" \
		-e "CACHETAG_DEBIAN_VERSION=$CACHETAG_DEBIAN_VERSION" \
		-e "CACHETAG_SOURCE_DATE_EPOCH=$CACHETAG_SOURCE_DATE_EPOCH" \
		"$@" \
		"$IMAGE" bash "/stage/$_script" > "$log_dir/$_log.log" 2>&1 || {
			tail -n 120 "$log_dir/$_log.log" >&2
			die "stage $_log failed (see $log_dir/$_log.log)"
		}
	tail -n 25 "$log_dir/$_log.log"
}

###############################################################################
# stage: source
###############################################################################

stage_source() {
	note "assembling pinned source archives"
	# Files under work/ were created by root inside a container, so clear them
	# from a container too rather than relying on the host mapping.
	docker run --rm -v "$out_dir:/out" "$IMAGE" rm -rf /out/work >/dev/null 2>&1 || true
	rm -rf "$work_dir"
	mkdir -p "$work_dir/pin" "$work_dir/build"

	[ -d "$VINYL_SRC/.git" ] || die "$VINYL_SRC is not a Git checkout"
	_have=$(git -C "$VINYL_SRC" rev-parse --verify "$VINYL_GIT_COMMIT^{commit}")
	[ "$_have" = "$VINYL_GIT_COMMIT" ] || die "pinned Vinyl commit not found in $VINYL_SRC"

	# The canonical pinned Vinyl source archive. This reproduces, byte for
	# byte, the procedure in libvmod-cachetag/scripts/release-source-archive.sh,
	# so its digest is the value the cohort manifest records as
	# vinyl.source_sha256 and the value the cohort identity is hashed over.
	git -C "$VINYL_SRC" archive --format=tar --prefix="vinyl-src/" \
		"$VINYL_GIT_COMMIT" > "$work_dir/pin/00-superproject.tar"

	git -C "$VINYL_SRC" ls-tree -r "$VINYL_GIT_COMMIT" |
		awk '$2 == "commit" { print $3" "$4 }' | sort -k2,2 > "$work_dir/submodules.txt"

	_idx=1
	while read -r _sub_commit _sub_path; do
		[ -n "${_sub_path:-}" ] || continue
		git -C "$VINYL_SRC/$_sub_path" cat-file -e "$_sub_commit^{commit}" 2>/dev/null ||
			die "submodule $_sub_path does not contain $_sub_commit"
		printf 'pinned submodule: %s at %s\n' "$_sub_path" "$_sub_commit"
		git -C "$VINYL_SRC/$_sub_path" archive --format=tar \
			--prefix="vinyl-src/$_sub_path/" "$_sub_commit" \
			> "$work_dir/pin/$(printf '%02d' "$_idx")-$(printf '%s' "$_sub_path" | tr '/' '_').tar"
		_idx=$((_idx + 1))
	done < "$work_dir/submodules.txt"

	# The cachetag orig tarball is the canonical release archive verbatim.
	[ -f "$CACHETAG_TARBALL" ] || die "canonical cachetag archive not found: $CACHETAG_TARBALL"
	_got=$(sha256 "$CACHETAG_TARBALL")
	[ "$_got" = "$CACHETAG_SOURCE_SHA256" ] ||
		die "cachetag archive digest $_got != pinned $CACHETAG_SOURCE_SHA256"
	printf 'OK: canonical cachetag archive digest matches the pinned value\n'
	cp "$CACHETAG_TARBALL" \
		"$work_dir/build/libvmod-cachetag_$CACHETAG_VERSION.orig.tar.gz"

	# Resolve the target architecture from the buildroot itself.
	docker run --rm "$IMAGE" bash -c \
		'export DEBIAN_FRONTEND=noninteractive
		 apt-get update -qq >/dev/null 2>&1
		 apt-get install -y --no-install-recommends dpkg-dev >/dev/null 2>&1
		 dpkg-architecture -qDEB_HOST_ARCH
		 dpkg-architecture -qDEB_HOST_MULTIARCH' \
		> "$work_dir/arch.txt"
	DEB_HOST_ARCH=$(sed -n 1p "$work_dir/arch.txt")
	DEB_HOST_MULTIARCH=$(sed -n 2p "$work_dir/arch.txt")
	VINYL_VMODDIR=/usr/lib/$DEB_HOST_MULTIARCH/vinyl-cache/vmods
	printf 'target architecture: %s (multiarch %s)\nexpected vmoddir: %s\n' \
		"$DEB_HOST_ARCH" "$DEB_HOST_MULTIARCH" "$VINYL_VMODDIR"
	printf '%s\n%s\n%s\n' "$DEB_HOST_ARCH" "$DEB_HOST_MULTIARCH" "$VINYL_VMODDIR" \
		> "$work_dir/target.txt"

	substitute_recipes

	docker run --rm \
		-v "$recipe_dir/container:/stage:ro" \
		-v "$work_dir:/work" \
		-e "VINYL_GIT_COMMIT=$VINYL_GIT_COMMIT" \
		-e "VINYL_UPSTREAM_VERSION=$VINYL_UPSTREAM_VERSION" \
		-e "VINYL_SOURCE_DATE_EPOCH=$VINYL_SOURCE_DATE_EPOCH" \
		-e "VINYL_ABI_STRING=$VINYL_ABI_STRING" \
		-e "CACHETAG_VERSION=$CACHETAG_VERSION" \
		"$IMAGE" bash /stage/assemble-source.sh > "$log_dir/source.log" 2>&1 || {
			tail -n 60 "$log_dir/source.log" >&2
			die "source assembly failed (see $log_dir/source.log)"
		}
	tail -n 20 "$log_dir/source.log"

	_got=$(sha256 "$work_dir/vinyl-source-$VINYL_GIT_COMMIT.tar")
	printf 'canonical Vinyl source archive sha256: %s\n' "$_got"
	[ "$_got" = "$VINYL_SOURCE_SHA256" ] ||
		die "canonical Vinyl source digest $_got != pinned $VINYL_SOURCE_SHA256"
	printf 'OK: matches the digest recorded by the cachetag release script\n'
}

# Token substitution. Both recipe trees are templates; an unsubstituted token
# must never reach dpkg-buildpackage.
substitute_recipes() {
	DEB_HOST_ARCH=$(sed -n 1p "$work_dir/target.txt")
	DEB_HOST_MULTIARCH=$(sed -n 2p "$work_dir/target.txt")
	VINYL_VMODDIR=$(sed -n 3p "$work_dir/target.txt")

	_vinyl_date=$(TZ=UTC0 perl -e \
		'use POSIX; print strftime("%a, %d %b %Y %H:%M:%S +0000", gmtime($ARGV[0]))' \
		"$VINYL_SOURCE_DATE_EPOCH")
	_cachetag_date=$(TZ=UTC0 perl -e \
		'use POSIX; print strftime("%a, %d %b %Y %H:%M:%S +0000", gmtime($ARGV[0]))' \
		"$CACHETAG_SOURCE_DATE_EPOCH")
	_rpm_date=$(TZ=UTC0 perl -e \
		'use POSIX; print strftime("%a %b %d %Y", gmtime($ARGV[0]))' \
		"$CACHETAG_SOURCE_DATE_EPOCH")

	note "substituting Vinyl recipe tokens"
	rm -rf "$work_dir/vinyl-debian"
	cp -R "$recipe_dir/vinyl/debian" "$work_dir/vinyl-debian"
	_subst "$work_dir/vinyl-debian" \
		"COHORT_ID=$COHORT_ID" \
		"VINYL_DEBIAN_VERSION=$VINYL_PACKAGE_VERSION" \
		"DEBIAN_DISTRIBUTION=$DEBIAN_DISTRIBUTION" \
		"DEBIAN_DATE=$_vinyl_date" \
		"VINYL_GIT_COMMIT=$VINYL_GIT_COMMIT" \
		"VTEST2_GIT_COMMIT=$VTEST2_GIT_COMMIT" \
		"VINYL_STRICT_ABI=$VINYL_STRICT_ABI" \
		"MAINTAINER_NAME=$MAINTAINER_NAME" \
		"MAINTAINER_EMAIL=$MAINTAINER_EMAIL"

	note "substituting cachetag recipe tokens"
	rm -rf "$work_dir/cachetag-debian"
	cp -R "$CACHETAG_SRC/packaging/debian" "$work_dir/cachetag-debian"
	_subst "$work_dir/cachetag-debian" \
		"COHORT_ID=$COHORT_ID" \
		"CACHETAG_VERSION=$CACHETAG_VERSION" \
		"PACKAGE_REVISION=$CACHETAG_PACKAGE_REVISION" \
		"VINYL_PACKAGE_VERSION=$VINYL_PACKAGE_VERSION" \
		"VINYL_STRICT_ABI=$VINYL_STRICT_ABI" \
		"VINYL_VRT=$VINYL_VRT_EXPECTED" \
		"VINYL_VMODDIR=$VINYL_VMODDIR" \
		"SOURCE_URL=$SOURCE_URL" \
		"MAINTAINER_NAME=$MAINTAINER_NAME" \
		"MAINTAINER_EMAIL=$MAINTAINER_EMAIL" \
		"DEBIAN_VERSION=$CACHETAG_DEBIAN_VERSION" \
		"DEBIAN_DISTRIBUTION=$DEBIAN_DISTRIBUTION" \
		"DEBIAN_DATE=$_cachetag_date" \
		"RPM_CHANGELOG_DATE=$_rpm_date"

	sh "$CACHETAG_SRC/packaging/check-tokens.sh" --substituted "$work_dir/cachetag-debian" ||
		die "an unsubstituted token survived into the cachetag build tree"
	if grep -rl '@[A-Z0-9_]\{2,\}@' "$work_dir/vinyl-debian" >/dev/null 2>&1; then
		grep -rn '@[A-Z0-9_]\{2,\}@' "$work_dir/vinyl-debian" >&2
		die "an unsubstituted token survived into the Vinyl build tree"
	fi
	printf 'OK: no unsubstituted tokens in either build tree\n'
}

_subst() {
	_dir=$1; shift
	for _pair in "$@"; do
		_k=${_pair%%=*}
		_v=${_pair#*=}
		find "$_dir" -type f -print | while IFS= read -r _f; do
			_esc=$(printf '%s' "$_v" | sed -e 's/[\/&|]/\\&/g')
			sed -i.bak "s|@$_k@|$_esc|g" "$_f" && rm -f "$_f.bak"
		done
	done
}

###############################################################################
# stages that run in containers
###############################################################################

stage_vinyl() {
	run_in_container vinyl stage-vinyl.sh -v "$work_dir:/work"
}

stage_cachetag() {
	run_in_container cachetag stage-cachetag.sh \
		-v "$work_dir:/work" \
		-e "VINYL_VMODDIR=$(sed -n 3p "$work_dir/target.txt")"
}

stage_lint() {
	run_in_container lint stage-lint.sh -v "$work_dir:/work"
}

stage_smoke() {
	run_in_container smoke stage-smoke.sh \
		-e "VINYL_VMODDIR=$(sed -n 3p "$work_dir/target.txt")"
}

stage_sums() {
	note "checksums"
	( cd "$out_dir" && ls -1 *.deb *.ddeb *.dsc *.tar.* *.changes *.buildinfo 2>/dev/null |
		sort | while IFS= read -r f; do
			printf '%s  %s\n' "$(sha256 "$f")" "$f"
		done > SHA256SUMS )
	cat "$out_dir/SHA256SUMS"
}

# VRT is read out of the built tree by debian/rules; the value below is the
# assertion, not the source of truth. build.sh fails if the package disagrees.
VINYL_VRT_EXPECTED=${VINYL_VRT_EXPECTED:-23.0}

mkdir -p "$out_dir" "$log_dir"
printf '*\n' > "$out_dir/.gitignore"

stages=${*:-all}
for s in $stages; do
	case $s in
	all)      stage_source; stage_vinyl; stage_cachetag; stage_lint; stage_smoke; stage_sums ;;
	source)   stage_source ;;
	vinyl)    stage_vinyl ;;
	cachetag) stage_cachetag ;;
	lint)     stage_lint ;;
	smoke)    stage_smoke ;;
	sums)     stage_sums ;;
	subst)    substitute_recipes ;;
	*) die "unknown stage: $s" ;;
	esac
done

note "done"
