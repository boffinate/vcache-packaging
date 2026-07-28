#!/bin/sh
#
# vmod-source-archive.sh -- derive a deterministic source archive for a VMOD
# whose upstream publishes no release tarball.
#
# SCOPE.md's source-and-release policy requires a release build to identify its
# source by a human-meaningful release tag or version and to record the resolved
# commit and the archive digest as evidence. When upstream publishes a signed
# release tarball, use that: it is a stronger statement than anything we can
# derive, and `recipes/vmods/overlays/<id>/<id>.yml` says so explicitly with
# `source.archive.method: upstream-release`. This script serves the other case,
# `method: derived-git-tag`, where the only published artifact is the tag.
#
# Determinism is the whole point, so every input is named on the command line
# and nothing is read from the ambient environment:
#
#   vmod-source-archive.sh --url URL --tag TAG --commit SHA1 \
#       --stem NAME --version VERSION --epoch UNIXTIME --out FILE \
#       [--submodule PATH=SHA1]... [--sha256 EXPECTED] [--workdir DIR]
#
# What it guarantees:
#
#   * the ref is fetched as a tag and the tag is required to PEEL to --commit,
#     so an annotated tag that was moved, or a branch of the same name, fails
#     rather than silently producing a different tree;
#   * every declared submodule is checked out at exactly the declared commit,
#     and an undeclared submodule is a hard error -- upstream adding one must
#     change the recorded inputs, not slip into the archive;
#   * the `git://` clone URLs some upstreams still declare in `.gitmodules`
#     (vmod-dict's `acvmod` is one) are rewritten to `https://` through an
#     explicit `insteadOf`, configured on this repository only. That protocol is
#     unauthenticated and widely blocked; relying on ambient host or runner
#     configuration to fix it would make the build behave differently in
#     different places, which is the opposite of what this script is for;
#   * the tarball is byte-reproducible: GNU format (no pax atime/ctime headers),
#     names sorted, uid/gid 0, every mtime set to --epoch, group/other write
#     bits cleared under a fixed umask, and gzip -9n so no filename or timestamp
#     reaches the gzip header.
#
# THIS MUST RUN IN A LINUX CONTAINER, not on a macOS host. macOS ships bsdtar as
# /usr/bin/tar, which has neither --sort nor GNU's header layout, so a digest
# derived on the host would not be the digest CI produces. The script refuses to
# run without GNU tar for exactly that reason.
#
# It builds nothing and installs nothing: it resolves source and writes one
# tarball.

set -eu

# A fixed umask, because git creates files with 0666 & ~umask and the tar header
# records the result. Without this the digest depends on the caller's shell.
umask 022

self=$(basename "$0")

die() {
	echo "E: $self: $*" >&2
	exit 1
}

note() {
	echo "== $*"
}

usage() {
	cat >&2 <<EOF
usage: $self --url URL --tag TAG --commit SHA1 --stem NAME --version VERSION
             --epoch UNIXTIME --out FILE [--submodule PATH=SHA1]...
             [--sha256 EXPECTED] [--workdir DIR] [--keep]
EOF
	exit 2
}

url=
tag=
commit=
stem=
version=
epoch=
out=
expected=
workdir=
keep=no
submodules=

while [ $# -gt 0 ]; do
	case $1 in
	--url) url=${2:?}; shift 2 ;;
	--tag) tag=${2:?}; shift 2 ;;
	--commit) commit=${2:?}; shift 2 ;;
	--stem) stem=${2:?}; shift 2 ;;
	--version) version=${2:?}; shift 2 ;;
	--epoch) epoch=${2:?}; shift 2 ;;
	--out) out=${2:?}; shift 2 ;;
	--sha256) expected=${2:?}; shift 2 ;;
	--workdir) workdir=${2:?}; shift 2 ;;
	--submodule) submodules="$submodules $2"; shift 2 ;;
	--keep) keep=yes; shift ;;
	-h | --help) usage ;;
	*) die "unknown argument $1" ;;
	esac
done

[ -n "$url" ] || usage
[ -n "$tag" ] || usage
[ -n "$commit" ] || usage
[ -n "$stem" ] || usage
[ -n "$version" ] || usage
[ -n "$epoch" ] || usage
[ -n "$out" ] || usage

case $commit in
*[!0-9a-f]* | "") die "--commit must be 40 lowercase hex characters, got '$commit'" ;;
esac
[ ${#commit} -eq 40 ] || die "--commit must be 40 lowercase hex characters, got '$commit'"
case $epoch in
*[!0-9]* | "") die "--epoch must be a decimal Unix timestamp, got '$epoch'" ;;
esac

# GNU tar or nothing. See the header comment: bsdtar produces different bytes.
if tar --version 2>/dev/null | head -1 | grep -q '^tar (GNU tar)'; then
	:
else
	die "GNU tar is required (found: $(tar --version 2>/dev/null | head -1)).
Run this inside a Linux container; macOS bsdtar produces a different archive
and therefore a different digest from the one CI will pin."
fi
command -v git >/dev/null 2>&1 || die "git is required"
command -v gzip >/dev/null 2>&1 || die "gzip is required"

prefix="$stem-$version"

if [ -n "$workdir" ]; then
	mkdir -p "$workdir"
	work=$(CDPATH= cd -- "$workdir" && pwd)
	created_work=no
else
	work=$(mktemp -d)
	created_work=yes
fi
cleanup() {
	if [ "$keep" = no ] && [ "$created_work" = yes ]; then
		rm -rf "$work"
	fi
}
trap cleanup EXIT INT TERM

src="$work/$prefix"
rm -rf "$src"

note "clone $url at tag $tag"
git init --quiet "$src"
(
	cd "$src"
	git config advice.detachedHead false
	# Rewrite the unauthenticated protocol upstream still declares in
	# .gitmodules. Repository-local, so nothing about the caller's global git
	# configuration changes what this produces.
	git config url."https://".insteadOf "git://"
	git remote add origin "$url"
	# +refs/tags/X:refs/tags/X, and only that ref: a branch of the same name
	# cannot be fetched into the tag namespace by accident.
	git fetch --quiet --no-tags origin "+refs/tags/$tag:refs/tags/$tag"
)

kind=$(cd "$src" && git cat-file -t "refs/tags/$tag")
peeled=$(cd "$src" && git rev-parse "refs/tags/$tag^{commit}")
note "tag object type: $kind"
note "tag peels to:    $peeled"
[ "$peeled" = "$commit" ] ||
	die "tag $tag peels to $peeled, not the recorded commit $commit.
Do NOT change the recorded commit to make this pass. A mismatch means the tag
was moved or the recorded identity is wrong; find out which first."

(cd "$src" && git checkout --quiet "$commit")

# Submodules. Every one present in the tree must be declared, and every declared
# one must be present: an upstream that adds or drops a submodule changes what
# gets compiled, and that has to be a recorded decision.
declared_paths=""
for pair in $submodules; do
	path=${pair%%=*}
	want=${pair#*=}
	[ "$path" != "$pair" ] || die "--submodule takes PATH=SHA1, got '$pair'"
	[ ${#want} -eq 40 ] || die "--submodule $path: expected a 40-character commit, got '$want'"
	got=$(cd "$src" && git ls-tree "$commit" -- "$path" | awk '$2 == "commit" { print $3 }')
	[ -n "$got" ] || die "--submodule $path: no gitlink at that path in $commit"
	[ "$got" = "$want" ] ||
		die "--submodule $path: tree records $got, recorded input says $want"
	declared_paths="$declared_paths $path"
	note "submodule $path pinned at $want"
done

present=$(cd "$src" && git ls-tree -r "$commit" | awk '$2 == "commit" { print $4 }')
for path in $present; do
	found=no
	for declared in $declared_paths; do
		[ "$declared" = "$path" ] && found=yes
	done
	[ "$found" = yes ] ||
		die "submodule $path exists in $commit but was not declared with --submodule"
done

if [ -n "$declared_paths" ]; then
	note "initialising submodules"
	# shellcheck disable=SC2086 # declared_paths is a deliberate word list
	(cd "$src" && git submodule update --init --recursive -- $declared_paths >/dev/null)
	for pair in $submodules; do
		path=${pair%%=*}
		want=${pair#*=}
		got=$(cd "$src/$path" && git rev-parse HEAD)
		[ "$got" = "$want" ] ||
			die "submodule $path checked out $got, not $want"
	done
fi

note "removing version-control metadata"
find "$src" -name .git -maxdepth 4 -exec rm -rf {} + 2>/dev/null || true

note "normalising permissions"
find "$src" -type d -exec chmod 755 {} +
find "$src" -type f ! -perm -u+x -exec chmod 644 {} +
find "$src" -type f -perm -u+x -exec chmod 755 {} +

note "writing $out"
mkdir -p "$(dirname -- "$out")"
# --format=gnu: the POSIX/pax default writes extended headers carrying atime and
# ctime, which are not reproducible. --sort=name fixes member order, which
# readdir(3) otherwise leaves to the filesystem. gzip -9n drops the original
# filename and timestamp from the gzip header.
(
	cd "$work"
	tar --format=gnu \
		--sort=name \
		--mtime="@$epoch" \
		--owner=0 --group=0 --numeric-owner \
		--mode=go-w \
		-cf - "$prefix"
) | gzip -9n >"$out.tmp"
mv "$out.tmp" "$out"

digest=$(sha256sum "$out" | awk '{ print $1 }')
bytes=$(wc -c <"$out" | tr -d ' ')
note "sha256 $digest"
note "bytes  $bytes"

if [ -n "$expected" ]; then
	[ "$digest" = "$expected" ] ||
		die "$out sha256 $digest does not match the pinned value $expected.
Do NOT update the pin to make this pass. A mismatch means the upstream tag, a
submodule pin, or this derivation procedure has moved. Find out which, fix
that, and only then does the pin change -- deliberately, in the same commit
that explains why."
	note "digest matches the pinned value"
fi

printf '%s  %s\n' "$digest" "$(basename -- "$out")"
