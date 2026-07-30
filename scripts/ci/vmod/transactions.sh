#!/bin/sh
#
# Host-side driver for the generated-recipe VMOD lane's upgrade-transaction
# stages. It stages a package directory the recipe lanes' own transaction
# scripts can read, and then invokes them with this VMOD's identity.
#
#   transactions.sh fixture-deb|matrix-deb|fixture-rpm|matrix-rpm \
#       --lane DIR --id ID --manifest PATH --overlay PATH \
#       --cohort ID --target ID [--channel release]
#
# Nothing here is a second implementation of the transaction matrix. The
# scenario tables, the synthetic fixture derivation and the classification all
# stay in recipes/debian-13/ and recipes/el9/, where cachetag's evidence comes
# from, and this script only supplies what those scripts cannot derive for
# themselves in a generated lane:
#
#   * a package directory in the layout they read. They were written against
#     dist/debian-13 and dist/el9, which a cachetag row still has; a generated
#     row has its package in lane/out and the engine's in lane/engine, so the
#     two are staged into one directory here and TXN_OUT_DIR points at it.
#   * this VMOD's package name, version, VCL import token and shared object,
#     every one of them read out of `vmod_recipe.py lane-env` -- the same model
#     the recipe was rendered from and the same command the verify stage's
#     driver uses. A value computed here would be a second place for it to be
#     wrong.
#   * an empty VMOD_PROBE_VCL, which selects the composed bare-import probe. A
#     generated VMOD has no reviewed probe VCL of its own, and compiling an
#     import is what makes the engine load the shared object and its
#     VCC-generated symbols -- which is the whole of what the probe asks.
#
# The engine facts (baseline version, strict ABI, cohort id, and the derived
# synthetic candidate versions) are NOT passed: the recipe scripts read them
# from their own lane pin files, dispatching on VINYL_TRACK, which is the same
# reader scripts/ci/engine-identity.sh used when this row verified the engine
# artifact it is about to test against.

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../../.." && pwd)
. "$here/lib.sh"

stage=${1:?stage required: fixture-deb|matrix-deb|fixture-rpm|matrix-rpm}
shift

lane=
vmod_id=
manifest=
overlay=
cohort=
target=
channel=release

while [ $# -gt 0 ]; do
	case $1 in
	--lane) lane=${2:?}; shift 2 ;;
	--id) vmod_id=${2:?}; shift 2 ;;
	--manifest) manifest=${2:?}; shift 2 ;;
	--overlay) overlay=${2:?}; shift 2 ;;
	--cohort) cohort=${2:?}; shift 2 ;;
	--target) target=${2:?}; shift 2 ;;
	--channel) channel=${2:?}; shift 2 ;;
	*) die "unknown argument $1" ;;
	esac
done

for required in lane vmod_id manifest overlay cohort target; do
	eval "value=\$$required"
	[ -n "$value" ] || die "--${required} is required"
done
lane=$(CDPATH= cd -- "$lane" && pwd)

eval "$(python3 "$repo/tools/vmod_recipe.py" lane-env \
	--manifest "$manifest" --overlay "$overlay" \
	--cohort "$cohort" --target "$target" --channel "$channel")"

# One directory per lane, beside out/ and engine/ rather than inside either, so
# the package artifact upload keeps naming exactly what it named before and the
# fixtures and scenario logs land under it as their own subtree.
TXN_OUT_DIR=$lane/txn
export TXN_OUT_DIR

# An explicitly EMPTY probe path: see the header. `-` would let a stray value in
# the environment select cachetag's probe for a VMOD that cannot import it.
VMOD_PROBE_VCL=
export VMOD_PROBE_VCL VMOD_IMPORT VMOD_OBJECT

stage_packages_deb() {
	mkdir -p "$TXN_OUT_DIR/logs"
	# *.deb only: lane/out also holds the .dsc, the source tarballs, the
	# .changes and the .buildinfo, none of which belongs in an apt repository
	# the scenarios install from.
	for f in "$lane"/engine/*.deb "$lane"/out/*.deb; do
		[ -f "$f" ] || continue
		cp -p "$f" "$TXN_OUT_DIR/"
	done
	# The digest list the fixture derivation verifies its inputs against. In the
	# cachetag lane `build.sh sums` writes it; here it is written over exactly
	# the files just staged, and it guards the same thing -- that nothing
	# mutated the directory between staging and the fixture build.
	( cd "$TXN_OUT_DIR" && sha256sum ./*.deb | sed 's#\./##' > SHA256SUMS )
	note "staged Debian packages in $TXN_OUT_DIR"
	cat "$TXN_OUT_DIR/SHA256SUMS"
}

stage_packages_rpm() {
	mkdir -p "$TXN_OUT_DIR/packages"
	# No .src.rpm: a source package is not part of an upgrade transaction, and
	# prep.sh drops them from the baseline repository anyway.
	for f in "$lane"/engine/*.rpm "$lane"/out/*.rpm; do
		[ -f "$f" ] || continue
		case $f in *.src.rpm) continue ;; esac
		cp -p "$f" "$TXN_OUT_DIR/packages/"
	done
	# Beside the rpms, matching the cachetag lane's dist/el9/packages/SHA256SUMS:
	# the fixture derivation resolves the listed names against the checksum
	# file's own directory, `sha256sum -c` style.
	( cd "$TXN_OUT_DIR/packages" && sha256sum ./*.rpm | sed 's#\./##' > SHA256SUMS )
	note "staged EL9 packages in $TXN_OUT_DIR/packages"
	cat "$TXN_OUT_DIR/packages/SHA256SUMS"
}

case $stage in
fixture-deb | matrix-deb)
	VMOD_PACKAGE=$VMOD_BINARY_NAME
	VMOD_VERSION=$VMOD_DEBIAN_VERSION
	VMOD_SO=$VMOD_OBJECT
	DEB_HOST_ARCH=$TARGET_ARCH
	export VMOD_PACKAGE VMOD_VERSION VMOD_SO DEB_HOST_ARCH VINYL_VMODDIR
	stage_packages_deb
	;;
fixture-rpm | matrix-rpm)
	# dnf resolves by name, so the RPM scenarios need no version: the EVRs they
	# compare are the engine's, and those come from the lane pin file.
	VMOD_PACKAGE=$VMOD_RPM_NAME
	export VMOD_PACKAGE
	stage_packages_rpm
	;;
*)
	die "unknown stage '$stage' (fixture-deb|matrix-deb|fixture-rpm|matrix-rpm)"
	;;
esac

case $stage in
fixture-deb)
	note "$vmod_id: synthetic mismatched engine candidates (Debian)"
	sh "$repo/recipes/debian-13/mismatch-fixture.sh"
	;;
matrix-deb)
	note "$vmod_id: upgrade-transaction matrix (Debian)"
	sh "$repo/recipes/debian-13/transactions.sh"
	;;
fixture-rpm)
	note "$vmod_id: synthetic mismatched engine candidates (EL9)"
	sh "$repo/recipes/el9/mismatch-fixture.sh"
	;;
matrix-rpm)
	note "$vmod_id: upgrade-transaction matrix (EL9)"
	sh "$repo/recipes/el9/transactions.sh"
	;;
esac

note "$stage complete"
