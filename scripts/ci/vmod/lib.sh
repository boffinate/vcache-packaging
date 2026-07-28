# Shared helpers for the generated-recipe VMOD lane. Sourced, never executed.
#
# This lane is deliberately separate from recipes/debian-13/ and recipes/el9/,
# which drive the Vinyl engine and cachetag's audited upstream-owned recipes.
# Threading a second VMOD through those scripts would have meant editing the
# code paths that produce cachetag's package bytes, and the equivalence
# contract for this wave is that cachetag's bytes do not move. Keeping the two
# lanes apart makes that argument trivial rather than reasoned: cachetag's
# scripts are not touched at all.
#
# What is shared is the machinery that matters -- the same pinned buildroot
# images, the same pbuilder and Mock clean rooms, the same registry-generated
# ABI expressions, and the same evidence and classification vocabulary.

# shellcheck shell=sh

note() { printf '\n===== %s =====\n' "$*"; }
die() {
	printf 'E: %s\n' "$*" >&2
	exit 1
}

sha256_of() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{ print $1 }'
	else
		shasum -a 256 "$1" | awk '{ print $1 }'
	fi
}

# Refuse a digest mismatch loudly and without suggesting the pin be changed.
# The same wording as scripts/ci/source-archive.sh, for the same reason: the
# only correct response to this failure is to find out what moved.
assert_sha256() {
	_file=$1
	_want=$2
	_got=$(sha256_of "$_file")
	printf 'file     : %s\nproduced : %s\npinned   : %s\n' "$_file" "$_got" "$_want"
	[ "$_got" = "$_want" ] || die "$_file sha256 $_got does not match the pinned $_want.
Do NOT update the pinned value to make this pass. A mismatch means the upstream
archive, its publication, or the recorded identity has moved. Find out which,
fix that, and only then does the pin change -- deliberately, in the same commit
that explains why."
	printf 'OK: digest matches the pinned value\n'
}

# One place that knows where a lane's outputs go, so the workflow, the build
# scripts and the artifact upload cannot disagree.
vmod_lane_dir() {
	printf '%s/dist/vmods/%s/%s' "$1" "$2" "$3"
}
