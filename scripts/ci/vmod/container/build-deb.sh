#!/bin/bash
#
# Builds one generated-recipe VMOD's Debian package with pbuilder, inside the
# pinned debian:trixie container that ../build-deb.sh starts.
#
# Mount contract (set by ../build-deb.sh):
#   /repo   the vcache-packaging checkout, read-only (pins.env)
#   /lane   the per-row work directory: build/ holds the source tree and the
#           orig tarball, engine/ the verified engine .debs, out/ the results
#
# Structurally the same as scripts/ci/debian13/container-pbuilder.sh, and for
# the same reasons -- pbuilder rather than sbuild, an apt resolver rather than
# aptitude, a D hook so apt lists exist inside the chroot, and the cohort's
# engine .debs published as a local repository so the exact-version
# Build-Depends is satisfiable. That file is not reused because reusing it
# would mean editing the script that produces cachetag's package bytes, and
# this wave's equivalence contract is that those bytes do not move. The
# duplication is deliberate and bounded; if a third VMOD family appears, merge
# them in a change whose only purpose is that.

set -euo pipefail

. /repo/recipes/debian-13/pins.env

lane=/lane
work=$lane/build
out=$lane/out
base_tar=$lane/chroot/$DEBIAN_DISTRIBUTION-amd64.tar
base_tgz=/base.tgz
localrepo=/localrepo

note() { printf '\n===== %s =====\n' "$*"; }
die() {
	printf 'E: %s\n' "$*" >&2
	exit 1
}

: "${VMOD_SOURCE_NAME:?}" "${VMOD_UPSTREAM_VERSION:?}" "${VMOD_DEBIAN_VERSION:?}"
: "${VMOD_SOURCE_DATE_EPOCH:?}"

[ -f "$base_tar" ] || die "no base tarball at $base_tar; run make-chroot.sh first"
mkdir -p "$out"

note "build toolchain"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
	pbuilder debhelper dpkg-dev fakeroot procps
dpkg-query -W -f='pbuilder ${Version}\n' pbuilder

note "pbuilder configuration"
cat >/etc/pbuilderrc <<'PBUILDERRC'
PBUILDERSATISFYDEPENDSCMD=/usr/lib/pbuilder/pbuilder-satisfydepends-apt
PBUILDERRC

mkdir -p /pbuilder-hooks
cat >/pbuilder-hooks/D05update <<'HOOK'
#!/bin/sh
set -e
apt-get update
HOOK
chmod 0755 /pbuilder-hooks/D05update

note "compressing the mmdebstrap base tarball"
gzip -1 -c "$base_tar" >"$base_tgz"

note "publishing the verified engine packages as a local repository"
# The generated recipe Build-Depends on the engine development package at an
# exact version, which is on no mirror. Same shape as the cachetag lane and the
# EL9 createrepo_c step: publish what the row was handed, and let apt resolve.
rm -rf "$localrepo"
mkdir -p "$localrepo"
cp -v "$lane"/engine/*.deb "$localrepo/"
(cd "$localrepo" && dpkg-scanpackages -m . /dev/null >Packages && gzip -9c Packages >Packages.gz)
ls -1 "$localrepo"

srcdir=$work/$VMOD_SOURCE_NAME-$VMOD_UPSTREAM_VERSION
dsc=$work/${VMOD_SOURCE_NAME}_${VMOD_DEBIAN_VERSION}.dsc

[ -d "$srcdir/debian" ] || die "$srcdir has no generated debian/ tree"

note "dpkg-buildpackage -S: $VMOD_SOURCE_NAME"
(cd "$srcdir" && SOURCE_DATE_EPOCH=$VMOD_SOURCE_DATE_EPOCH dpkg-buildpackage -S -us -uc -d)
[ -f "$dsc" ] || die "expected $dsc after dpkg-buildpackage -S"

note "pbuilder build: $VMOD_SOURCE_NAME"
SOURCE_DATE_EPOCH=$VMOD_SOURCE_DATE_EPOCH pbuilder build \
	--basetgz "$base_tgz" \
	--buildresult "$out" \
	--override-config \
	--distribution "$DEBIAN_DISTRIBUTION" \
	--components main \
	--mirror "$DEBIAN_SNAPSHOT_URI" \
	--architecture amd64 \
	--hookdir /pbuilder-hooks \
	--no-auto-cross \
	--bindmounts "$localrepo" \
	--othermirror "deb [trusted=yes] file://$localrepo ./" \
	"$dsc"

note "source package artefacts"
cp -v "$work/${VMOD_SOURCE_NAME}"_*.dsc \
	"$work/${VMOD_SOURCE_NAME}"_*.orig.tar.gz \
	"$work/${VMOD_SOURCE_NAME}"_*.debian.tar.* \
	"$out/" 2>/dev/null || true

note "Debian VMOD lane complete"
ls -la "$out"
