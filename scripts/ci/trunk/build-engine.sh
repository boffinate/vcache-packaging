#!/bin/bash
#
# Build Vinyl Cache trunk HEAD from source into a relocatable-by-agreement
# prefix, inside the pinned Debian container the trunk early-warning workflow
# starts. Runs ONCE per workflow run; every VMOD's harness row unpacks the
# result. That sharing is the whole economics of the lane: at the roadmap's
# ~40-VMOD ambition, building the engine per VMOD would be forty Vinyl builds
# to answer one question about Vinyl.
#
# Mount contract:
#   /src    the Vinyl trunk checkout, writable (the build happens in it)
#   /out    where the installed prefix tarball and the logs are written
#
# Environment:
#   VINYL_PREFIX   the install prefix. MUST be the same absolute path here and
#                  in the harness container: libtool bakes it into the .la
#                  files, pkg-config into vinylapi.pc, and vmod_abi.h into the
#                  ABI string. Unpacking this tarball anywhere else produces a
#                  prefix whose recorded paths point at nothing.
#
# This builds an UNPINNED input on purpose, which no other build in this
# repository does. Nothing installable comes out of it and nothing is
# published: the tarball is a build intermediate with a 30-day retention, not a
# durability promise, and SCOPE.md is explicit that a CI-derived archive passed
# between jobs is exactly that.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C

: "${VINYL_PREFIX:?}"

note() { printf '\n===== %s =====\n' "$*"; }
die() { printf 'E: %s\n' "$*" >&2; exit 1; }

mkdir -p /out/logs

note "build dependencies"
# The union of two authorities, and neither is guessed here: the Build-Depends
# of the audited Vinyl packaging in upstream/pkg-vinyl-cache/debian/control,
# plus the autotools set that a git checkout needs and a `make dist` tarball
# does not. libunwind-dev is the survey lane image's addition, kept for the
# same reason it has it -- the backtrace support is compiled in when present,
# so leaving it out silently builds a different daemon.
apt-get update -qq
apt-get install -y --no-install-recommends \
	build-essential automake autoconf autoconf-archive libtool pkg-config \
	git ca-certificates \
	python3 python3-docutils python3-sphinx \
	libedit-dev libjemalloc-dev libncurses-dev libpcre2-dev libunwind-dev \
	>/dev/null
printf 'gcc: %s\n' "$(gcc --version | head -1)"

cd /src

note "resolved input"
# Recorded here as well as by the workflow, so the log of the build says what
# was built without anyone having to correlate it with a job summary.
git config --global --add safe.directory /src
vinyl_commit=$(git rev-parse HEAD)
printf 'vinyl-cache HEAD : %s\n' "$vinyl_commit"
printf 'committed        : %s\n' "$(git show -s --format=%cI HEAD)"
if [ -d bin/vinyltest/vtest2 ]; then
	git config --global --add safe.directory /src/bin/vinyltest/vtest2
	printf 'vtest2 submodule : %s\n' \
		"$(git -C bin/vinyltest/vtest2 rev-parse HEAD 2>/dev/null || echo '(not initialised)')"
fi

note "bootstrap"
[ -x ./autogen.sh ] || die "no autogen.sh in the Vinyl checkout; this is not a source tree"
sh ./autogen.sh 2>&1 | tee /out/logs/autogen.log

note "configure --prefix=$VINYL_PREFIX"
# No hardening or profile flags. This is not a package build and must not
# pretend to be one: what it produces is a development surface for compiling
# VMODs against, and the packaged engine is built by an entirely different lane
# from a pinned release.
./configure --prefix="$VINYL_PREFIX" 2>&1 | tee /out/logs/configure.log

note "make"
make -j"$(nproc)" 2>&1 | tee /out/logs/make.log

note "make install"
make install 2>&1 | tee /out/logs/make-install.log

note "what the prefix advertises"
export PKG_CONFIG_PATH="$VINYL_PREFIX/lib/pkgconfig"
export PATH="$VINYL_PREFIX/bin:$VINYL_PREFIX/sbin:$PATH"
export LD_LIBRARY_PATH="$VINYL_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
pkg-config --modversion vinylapi
pkg-config --variable=vmoddir vinylapi
pkg-config --variable=datarootdir vinylapi
vinyld -V 2>&1 | head -3
command -v vinyltest >/dev/null || die "the prefix has no vinyltest; the harness rows cannot run a suite"
vinyltest -h 2>&1 | head -2 || true

note "tar the installed prefix"
# Rooted at / so it unpacks to exactly the path it was configured for. The
# harness container untars it at / and the baked-in paths are correct by
# construction rather than by a relocation step that would have to rewrite
# vinylapi.pc, the .la files and vmod_abi.h consistently.
tar -C / -czf /out/vinyl-trunk-prefix.tar.gz "${VINYL_PREFIX#/}"
printf 'prefix tarball: %s bytes\n' "$(stat -c %s /out/vinyl-trunk-prefix.tar.gz)"

note "identity"
{
	printf 'engine=vinyl-trunk-head\n'
	printf 'vinyl_git_commit=%s\n' "$vinyl_commit"
	printf 'vinyl_prefix=%s\n' "$VINYL_PREFIX"
	printf 'vinyl_api_version=%s\n' "$(pkg-config --modversion vinylapi)"
	printf 'vinyl_vmoddir=%s\n' "$(pkg-config --variable=vmoddir vinylapi)"
	printf 'run_id=%s\n' "${GITHUB_RUN_ID:-local}"
	printf 'built_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} | tee /out/trunk-engine-identity.env

note "trunk engine prefix complete"
