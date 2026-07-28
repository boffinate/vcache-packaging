#!/bin/sh
#
# Shared helpers for the CI scripts under scripts/ci/. Sourced, not executed
# directly (`. scripts/ci/lib/common.sh`).
#
# DRAFT, unexecuted -- see ../../../DESIGN.md. Written to match this
# repository's existing shell style (recipes/*/build.sh): POSIX sh, `set -eu`
# in callers, `note`/`die` helpers, explicit assertions rather than silent
# fallbacks.

note() { printf '\n===== %s =====\n' "$*"; }
die() { printf 'E: %s\n' "$*" >&2; exit 1; }

sha256_file() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{print $1}'
	else
		shasum -a 256 "$1" | awk '{print $1}'
	fi
}

# ci_verify_cachetag_release_checkout CHECKOUT TAG EXPECTED_COMMIT
#
# Package workflows name the upstream release tag because that is the source
# identity a maintainer understands and can inspect. The peeled commit remains
# recorded separately as evidence of exactly what was tested. Verify both here:
# a moved tag, a lightweight tag, or a checkout at some other commit must fail
# before an archive or package build starts.
ci_verify_cachetag_release_checkout() {
	_src=$1; _tag=$2; _expected_commit=$3

	git -C "$_src" rev-parse --git-dir >/dev/null 2>&1 ||
		die "$_src is not a git checkout"

	_tag_type=$(git -C "$_src" cat-file -t "refs/tags/$_tag" 2>/dev/null || true)
	[ "$_tag_type" = tag ] ||
		die "cachetag release ref $_tag is not an annotated tag in $_src (found: ${_tag_type:-missing})"

	_tag_commit=$(git -C "$_src" rev-parse "refs/tags/$_tag^{commit}")
	[ "$_tag_commit" = "$_expected_commit" ] ||
		die "cachetag release tag $_tag resolves to $_tag_commit, not recorded commit $_expected_commit"

	_head=$(git -C "$_src" rev-parse HEAD)
	[ "$_head" = "$_expected_commit" ] ||
		die "cachetag checkout HEAD is $_head, not recorded commit $_expected_commit from $_tag"

	printf 'OK: cachetag %s -> %s (annotated tag)\n' "$_tag" "$_head"
}

# ci_checkout_vinyl_cache DEST_DIR SUPERPROJECT_COMMIT VTEST2_COMMIT
#
# vinyl-cache is not a GitHub repository -- its Vcs-Git fields (see
# recipes/debian-13/vinyl/debian/control and the built .dsc files) point at
# https://code.vinyl-cache.org/vinyl-cache/vinyl-cache.git, which
# actions/checkout cannot authenticate against. This clones it directly and
# asserts both pinned commits resolve exactly, mirroring the assertion
# recipes/debian-13/build.sh's stage_source already performs against a host
# checkout (`git -C "$VINYL_SRC" rev-parse --verify "$VINYL_GIT_COMMIT^{commit}"`).
#
# NEVER relax this assertion to "closest available commit" or similar: a
# mismatch here means the sibling checkout does not contain the input the
# rest of this pipeline is pinned against, and every downstream digest
# assertion depends on that not being silently true.
ci_checkout_vinyl_cache() {
	_dest=$1; _commit=$2; _vtest2_commit=$3
	_remote=https://code.vinyl-cache.org/vinyl-cache/vinyl-cache.git

	note "cloning $_remote (pinned commit $_commit)"
	rm -rf "$_dest"
	mkdir -p "$_dest"

	# A full clone is the only option here. Measured 2026-07-25: this server
	# answers /info/refs?service=git-upload-pack with `content-type:
	# text/plain`, i.e. it serves the repository over *dumb* HTTP, so any
	# commit-targeted `git fetch --depth 1` dies with "dumb http transport
	# does not support shallow capabilities" regardless of the commit. The
	# earlier draft attempted a shallow fetch first; that attempt could never
	# succeed against this remote, and its failure masked the real error
	# below behind git's obscure "unable to read tree" message.
	rm -rf "$_dest"
	git clone -q "$_remote" "$_dest"

	# A dumb-HTTP clone carries exactly what the published refs reach. If the
	# pinned commit is not among them the checkout below fails with an error
	# that names an object id but not the cause, so say the cause plainly.
	#
	# NEVER "fix" this by picking a commit the remote does have. A pinned
	# commit the public remote cannot serve means the input this pipeline is
	# pinned against has not been published; publishing it (or deliberately
	# re-pinning, in a change that explains why) is the fix.
	git -C "$_dest" cat-file -e "$_commit^{commit}" 2>/dev/null || die \
"pinned Vinyl commit $_commit is not reachable from any ref published by
$_remote (a full clone of it does not contain the object). The pin is not
wrong-valued, it is unpublished: nothing this workflow can do makes it
fetchable. Push the branch carrying it to the public remote, or re-pin
deliberately, and update recipes/debian-13/build.sh, recipes/el9/cohort.env,
scripts/ci/debian13/pinned.sh and .github/workflows/*.yml together."

	git -C "$_dest" checkout -q --detach "$_commit"

	_got=$(git -C "$_dest" rev-parse HEAD)
	[ "$_got" = "$_commit" ] ||
		die "vinyl-cache checkout resolved to $_got, not the pinned $_commit"

	note "vtest2 submodule (pinned commit $_vtest2_commit)"
	git -C "$_dest" submodule update -q --init bin/vinyltest/vtest2
	_got_sub=$(git -C "$_dest/bin/vinyltest/vtest2" rev-parse HEAD)
	[ "$_got_sub" = "$_vtest2_commit" ] ||
		die "vtest2 submodule resolved to $_got_sub, not the pinned $_vtest2_commit"

	printf 'OK: vinyl-cache @ %s, vtest2 @ %s\n' "$_got" "$_got_sub"
}
