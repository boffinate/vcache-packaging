#!/bin/bash
#
# EL9 mismatch fixture, in-container half. Runs inside the almalinux:9 container
# that recipes/el9/mismatch-fixture.sh starts; never on the host.
#
# Mounts, all established by mismatch-fixture.sh:
#   /recipes   recipes/el9, read-only
#   /out       dist/el9, writable
#
# Arguments: the variant names to build (mismatch, sameabi).
#
# Produces, under /out/mismatch:
#   packages/    the fixture RPMs
#   specs/       the exact generated spec for each variant
#   logs/        build logs and the verification report
#   SHA256SUMS   digests of the fixture RPMs
#   PROVENANCE   what each fixture was made from, and with which digest

set -euo pipefail

. /recipes/cohort.env

arch=$(uname -m)
isa=$(rpm --eval '%{?_isa}')
baseline_evr="$VINYL_VERSION-$VINYL_RELEASE.el9"
baseline_runtime="vinyl-cache-$baseline_evr.$arch.rpm"
baseline_devel="vinyl-cache-devel-$baseline_evr.$arch.rpm"

topdir=/out/mismatch/rpmbuild
outdir=/out/mismatch
logdir=$outdir/logs

say()  { printf '\n===== %s =====\n' "$*"; }
fail=0
ok()   { printf 'PASS: %s\n' "$*"; }
bad()  { printf 'FAIL: %s\n' "$*"; fail=1; }

mkdir -p "$topdir"/{SOURCES,SPECS,SRPMS,RPMS,BUILD,BUILDROOT} \
	"$outdir"/{packages,specs} "$logdir"

# --------------------------------------------------------------------- inputs

say "baseline cohort inputs"
for f in "$baseline_runtime" "$baseline_devel"; do
	test -f "/out/packages/$f" || { echo "missing baseline package: $f" >&2; exit 2; }
done
# The baseline digests are the fixture's provenance. Check them against the
# lane's own SHA256SUMS rather than trusting whatever is in the directory now.
( cd /out/packages && grep -E "^[0-9a-f]{64}  (vinyl-cache|vinyl-cache-devel)-" /out/SHA256SUMS \
	| sha256sum -c - ) | tee "$logdir/baseline-digests.txt"

say "install the build tooling"
dnf -y --setopt=install_weak_deps=False install \
	rpm-build rpmdevtools cpio systemd-rpm-macros redhat-rpm-config >/dev/null
rpm -q rpm-build rpmdevtools cpio

# The vmod() provides the real runtime publishes, re-emitted at the fixture EVR.
# Read out of the baseline package rather than listed here: a hand-maintained
# list would drift the moment Vinyl gains or loses a built-in VMOD.
vmod_names=$(rpm -qp --provides "/out/packages/$baseline_runtime" \
	| sed -n 's/^vmod(\([a-z0-9_]*\)).*/\1/p' | sort)
printf 'built-in VMODs carried into the fixture: %s\n' "$(echo $vmod_names)"

vrt=$(rpm -qp --provides "/out/packages/$baseline_runtime" \
	| sed -n 's/^vinyld(vrt).* = //p')
printf 'VRT version carried into the fixture: %s\n' "$vrt"
test -n "$vrt"

install -m 0644 "/out/packages/$baseline_runtime" "$topdir/SOURCES/"
install -m 0644 "/out/packages/$baseline_devel" "$topdir/SOURCES/"
install -m 0644 /recipes/systemd/vinyl-cache.sysusers "$topdir/SOURCES/"

# ------------------------------------------------------------------- variants

# Version choice, and why it is what it is:
#
#   * it must sort strictly above the baseline, or no upgrade transaction would
#     even be proposed and the whole matrix would test nothing;
#   * it must be unmistakably synthetic on sight, because these packages will
#     sit in a dist/ directory next to real ones. The snapshot convention is
#     9.0.0~git<date>.<12 hex of commit>; a commit id of ffffffffffff or
#     eeeeeeeeeeee is not a commit anybody will ever have, and the release field
#     carries the word "fixture" as well;
#   * the release field also has to sort above 1.el9. rpmvercmp segments
#     1.mismatchfixture.el9 as [1][mismatchfixture][el][9] against [1][el][9],
#     and "mismatchfixture" > "el" alphabetically, so it does. container.sh
#     asserts this with rpmdev-vercmp rather than trusting the reasoning.
#
# The ABI hash for the mismatch variant is 40 f's: syntactically a valid strict
# ABI token, semantically impossible, so a resolver that matches it is matching
# on the string and nothing else.

variant_setup() {
	case $1 in
	mismatch)
		fixture_version=9.0.0~git20260724.ffffffffffff
		fixture_release=1.mismatchfixture.el9
		fixture_abi=ffffffffffffffffffffffffffffffffffffffff
		variant_note='Simulates an incompatible Vinyl security upgrade: higher version-release, different VMOD ABI. A strict-ABI VMOD built against the baseline cannot resolve against it.'
		;;
	sameabi)
		fixture_version=9.0.0~git20260724.eeeeeeeeeeee
		fixture_release=1.sameabifixture.el9
		fixture_abi=$VINYL_STRICT_ABI
		variant_note='Simulates the plan'"'"'s known limitation: a different Vinyl package advertising the SAME baked-in ABI string. A strict-ABI VMOD resolves against it even though it is a different package.'
		;;
	*)
		echo "unknown variant: $1" >&2; exit 2 ;;
	esac
	fixture_evr="$fixture_version-$fixture_release"
}

build_variant() {
	local variant=$1
	variant_setup "$variant"

	say "variant $variant: $fixture_evr, vinyld(abi) = $fixture_abi"

	# Ordering is a load-bearing property of the fixture, not a detail.
	if rpmdev-vercmp "$baseline_evr" "$fixture_evr" >/dev/null 2>&1; then
		: # exit 0 means equal
		bad "fixture EVR $fixture_evr compares EQUAL to the baseline"
	else
		case $? in
		12) ok "fixture EVR $fixture_evr sorts above the baseline $baseline_evr" ;;
		11) bad "fixture EVR $fixture_evr sorts BELOW the baseline $baseline_evr" ;;
		*)  bad "rpmdev-vercmp could not compare $baseline_evr and $fixture_evr" ;;
		esac
	fi

	: > /tmp/vmod-provides
	for v in $vmod_names; do
		printf 'Provides:       vmod(%s)%%{?_isa} = %%{version}-%%{release}\n' \
			"$v" >> /tmp/vmod-provides
	done

	local spec="$topdir/SPECS/vinyl-cache-fixture-$variant.spec"
	sed \
		-e "s|@FIXTURE_VERSION@|$fixture_version|g" \
		-e "s|@FIXTURE_RELEASE@|$fixture_release|g" \
		-e "s|@FIXTURE_ABI@|$fixture_abi|g" \
		-e "s|@VARIANT@|$variant|g" \
		-e "s|@VARIANT_NOTE@|$variant_note|g" \
		-e "s|@BASELINE_EVR@|$baseline_evr|g" \
		-e "s|@BASELINE_RUNTIME_RPM@|$baseline_runtime|g" \
		-e "s|@BASELINE_DEVEL_RPM@|$baseline_devel|g" \
		-e "s|@VINYL_VRT@|$vrt|g" \
		-e "s|@COHORT_ID@|$COHORT_ID|g" \
		-e "s|@RPM_CHANGELOG_DATE@|$(LC_ALL=C date -u '+%a %b %d %Y')|g" \
		-e "s|@MAINTAINER_NAME@|$MAINTAINER_NAME|g" \
		-e "s|@MAINTAINER_EMAIL@|$MAINTAINER_EMAIL|g" \
		-e "/@VMOD_PROVIDES@/r /tmp/vmod-provides" \
		-e "/@VMOD_PROVIDES@/d" \
		/recipes/mismatch/vinyl-cache-fixture.spec.in > "$spec"

	if grep -n '@[A-Z_]\+@' "$spec"; then
		bad "unsubstituted token reached the generated fixture spec"
		return
	fi
	cp -p "$spec" "$outdir/specs/"

	# A retained digest is only worth retaining if rebuilding reproduces it.
	# Without this, rpm stamps BUILDTIME with the current clock and every run
	# of this script produces a different sha256 for the same fixture.
	# SOURCE_DATE_EPOCH alone is not enough on EL9: rpm 4.16 ships
	# %use_source_date_epoch_as_buildtime defaulting to 0, so the header
	# BUILDTIME still comes from the wall clock. Both macros are set here,
	# and mismatch-fixture.sh's own reproducibility check is what proves it
	# worked.
	export SOURCE_DATE_EPOCH="$VINYL_SOURCE_DATE_EPOCH"
	# _buildhost too: it defaults to the container's hostname, which Docker
	# randomises per run, so two identical builds in two containers differ by
	# exactly that one header string and nothing else.
	rpmbuild --define "_topdir $topdir" \
		--define "use_source_date_epoch_as_buildtime 1" \
		--define "clamp_mtime_to_source_date_epoch 1" \
		--define "_buildhost vinyl-packaging-fixture.invalid" \
		-bb "$spec" \
		2>&1 | tee "$logdir/fixture-$variant-build.log" | tail -n 5

	find "$topdir/RPMS" -name "vinyl-cache*-$fixture_version-$fixture_release.*.rpm" \
		-exec cp -p {} "$outdir/packages/" \;

	verify_variant "$variant"
}

# --------------------------------------------------------------- verification

verify_variant() {
	local variant=$1
	variant_setup "$variant"
	local cand="$outdir/packages/vinyl-cache-$fixture_evr.$arch.rpm"
	local cand_devel="$outdir/packages/vinyl-cache-devel-$fixture_evr.$arch.rpm"

	say "variant $variant: verification"
	test -f "$cand" && test -f "$cand_devel"

	# 1. the ABI provide is exactly what the variant asked for
	if rpm -qp --provides "$cand" | grep -qx "vinyld(abi)$isa = $fixture_abi"; then
		ok "candidate provides vinyld(abi)$isa = $fixture_abi"
	else
		bad "candidate does not provide the intended ABI"
		rpm -qp --provides "$cand"
	fi

	# 2. the file payload is the baseline's, so a transaction that succeeds
	#    leaves a working Vinyl behind
	# %{_docdir} is only defined inside a spec build; %{_defaultdocdir} is the
	# one rpm will expand from the command line.
	local docdir="$(rpm --eval %{_defaultdocdir})/vinyl-cache"
	rpm -qlp "/out/packages/$baseline_runtime" | grep -v '^/usr/lib/\.build-id' \
		| sort > /tmp/base-files
	rpm -qlp "$cand" | sort > /tmp/cand-files
	comm -13 /tmp/base-files /tmp/cand-files > /tmp/only-candidate
	comm -23 /tmp/base-files /tmp/cand-files > /tmp/only-baseline
	if [ ! -s /tmp/only-baseline ] &&
	   [ "$(cat /tmp/only-candidate)" = "$docdir/FIXTURE.txt" ]; then
		ok "candidate file list equals the baseline's, plus FIXTURE.txt"
	else
		bad "candidate file list differs from the baseline beyond FIXTURE.txt"
		printf 'only in baseline:\n'; sed 's/^/  /' /tmp/only-baseline
		printf 'only in candidate:\n'; sed 's/^/  /' /tmp/only-candidate
	fi

	rpm -qlp "/out/packages/$baseline_devel" | grep -v '^/usr/lib/\.build-id' \
		| sort > /tmp/base-devel-files
	rpm -qlp "$cand_devel" | sort > /tmp/cand-devel-files
	if diff -u /tmp/base-devel-files /tmp/cand-devel-files > /tmp/devel-files-diff; then
		ok "candidate devel file list equals the baseline's"
	else
		bad "candidate devel file list differs from the baseline"
		cat /tmp/devel-files-diff
	fi

	# 3. the soname provides a VMOD links against survive the respin. Without
	#    them a transaction could fail for a reason that has nothing to do
	#    with the ABI dependency under test.
	if rpm -qp --provides "$cand" | grep -q "^libvinylapi.so.3()"; then
		ok "candidate still provides the libvinylapi.so.3 soname"
	else
		bad "candidate lost the libvinylapi soname provide"
	fi

	# 4. the devel half is pinned to the runtime half at the fixture EVR
	if rpm -qp --requires "$cand_devel" | grep -qx "vinyl-cache$isa = $fixture_evr"; then
		ok "candidate devel requires the candidate runtime exactly"
	else
		bad "candidate devel is not pinned to the candidate runtime"
		rpm -qp --requires "$cand_devel"
	fi

	printf '\ncandidate Provides:\n'
	rpm -qp --provides "$cand" | sed 's/^/  /'
	printf 'candidate Requires:\n'
	rpm -qp --requires "$cand" | sed 's/^/  /'
}

# ------------------------------------------------------------------- dispatch

for variant in "$@"; do
	build_variant "$variant"
done

say "fixture digests"
( cd "$outdir/packages" && sha256sum ./*.rpm | sed 's#\./##' | tee "$outdir/SHA256SUMS" )

{
	printf 'EL9 upgrade-transaction fixture provenance\n'
	printf 'generated: %s UTC\n' "$(date -u '+%Y-%m-%d %H:%M:%S')"
	printf 'generator: recipes/el9/mismatch-fixture.sh -> mismatch/container.sh\n'
	printf 'spec     : recipes/el9/mismatch/vinyl-cache-fixture.spec.in\n'
	printf 'image    : %s\n' "$(cat /etc/redhat-release)"
	printf '\nbaseline payload (the only source of file content):\n'
	( cd /out/packages && sha256sum "$baseline_runtime" "$baseline_devel" | sed 's/^/  /' )
	printf '\nfixture packages:\n'
	sed 's/^/  /' "$outdir/SHA256SUMS"
	printf '\ngenerated specs are retained under mismatch/specs/.\n'
	printf 'Signing: none. See the session note on the unsigned-local-repo trust model.\n'
} | tee "$outdir/PROVENANCE"

say "fixture build result"
if [ $fail -eq 0 ]; then echo "ALL FIXTURE CHECKS PASSED"; else echo "FIXTURE CHECKS FAILED"; fi
exit $fail
