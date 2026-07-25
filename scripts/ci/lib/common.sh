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

	# Try a shallow, commit-targeted fetch first to bound clone time; a
	# commit that is not a branch/tag tip may not be shallow-fetchable
	# depending on the server's uploadpack.allowReachableSHA1InWant setting,
	# so a full clone is the documented, unconditionally-correct fallback.
	# UNVERIFIED in this draft: no network access to code.vinyl-cache.org
	# was available while writing this script.
	git -C "$_dest" init -q
	git -C "$_dest" remote add origin "$_remote"
	if git -C "$_dest" fetch -q --depth 1 origin "$_commit" 2>/dev/null; then
		git -C "$_dest" checkout -q FETCH_HEAD
	else
		note "shallow fetch of $_commit failed or is unsupported by the server; falling back to a full clone"
		rm -rf "$_dest"
		git clone -q "$_remote" "$_dest"
		git -C "$_dest" checkout -q "$_commit"
	fi

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
