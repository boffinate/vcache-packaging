#!/bin/sh
#
# Source stage for a generated-recipe VMOD whose upstream publishes a release
# archive. Produces exactly one verified tarball and nothing else.
#
#   source.sh --manifest PATH --id ID --channel CHANNEL --out DIR [--ref REF]
#
# Four checks, in this order, because each one makes the next meaningful:
#
#   1. download the archive the manifest names;
#   2. assert its sha256 against the manifest's pin -- these exact bytes;
#   3. `git ls-remote` the recorded tag and require it to peel to the recorded
#      commit;
#   4. unpack it and cross-check the manifest's version against the archive's
#      own AC_INIT.
#
# WHY NO CLONE. cachetag's lane checks its source out with actions/checkout and
# derives the archive; this VMOD is not on GitHub, and Step 5's ruling 5 flagged
# that as a schema problem to solve. It turned out not to need solving for the
# source stage at all: when upstream publishes the archive, a clone buys
# nothing. `git ls-remote --tags` costs one request, needs no host-specific
# action and no working tree, and answers the only question a clone would have
# answered -- whether the tag we recorded still names the commit we recorded.
# The archive's own bytes are pinned separately and more strongly.
#
# Check 3 is not redundant with check 2. The digest proves what we built;
# ls-remote proves the human-meaningful release identity still points at the
# same place, which is what SCOPE.md's source policy actually requires and what
# would catch a re-tagged or moved release even when the old archive is still
# served.
#
# --ref OVERRIDES the ref the manifest records, and exists for exactly one
# reason: tools/ci_matrix.py's `expand` injects a source failure by rewriting
# the row's ref, and the workflow passes that row value here. Without the
# override this script would read the ref back out of the manifest and the
# injection would be inert -- which is what it was until 2026-07-28, making the
# two-VMOD source-isolation case unprovable. The cachetag path has always taken
# its ref from the matrix row for the same reason. An override changes nothing
# else: the recorded commit, digest and version are still the manifest's, so an
# overridden ref that does not resolve to the recorded commit fails check 3,
# which is precisely the injected failure.

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../../.." && pwd)
. "$here/lib.sh"

manifest=
vmod_id=
channel=release
out=
ref_override=

while [ $# -gt 0 ]; do
	case $1 in
	--manifest) manifest=${2:?}; shift 2 ;;
	--id) vmod_id=${2:?}; shift 2 ;;
	--channel) channel=${2:?}; shift 2 ;;
	--out) out=${2:?}; shift 2 ;;
	--ref) ref_override=${2:?}; shift 2 ;;
	*) die "unknown argument $1" ;;
	esac
done

[ -n "$manifest" ] || die "--manifest is required"
[ -n "$vmod_id" ] || die "--id is required"
[ -n "$out" ] || die "--out is required"

command -v git >/dev/null 2>&1 || die "git is required"
command -v curl >/dev/null 2>&1 || die "curl is required"

# Every value below comes from the manifest through the tool that owns the
# schema. Nothing is parsed out of YAML with sed here: a second parser is a
# second thing that can disagree with the validator.
eval "$(python3 "$repo/tools/ci_matrix.py" source-facts \
	--manifest "$manifest" --id "$vmod_id" --channel "$channel" --format shell)"

: "${VMOD_SOURCE_REF:?}" "${VMOD_SOURCE_COMMIT:?}" "${VMOD_SOURCE_VERSION:?}"
: "${VMOD_SOURCE_ARCHIVE_URL:?}" "${VMOD_SOURCE_ARCHIVE_SHA256:?}" "${VMOD_CLONE_URL:?}"

if [ -n "$ref_override" ] && [ "$ref_override" != "$VMOD_SOURCE_REF" ]; then
	note "ref overridden on the command line: $VMOD_SOURCE_REF -> $ref_override"
	VMOD_SOURCE_REF=$ref_override
fi

mkdir -p "$out"
archive_name=$(basename -- "$VMOD_SOURCE_ARCHIVE_URL")
archive="$out/$archive_name"

note "1 -- download $VMOD_SOURCE_ARCHIVE_URL"
curl -sSfL --retry 3 --retry-delay 2 -o "$archive.part" "$VMOD_SOURCE_ARCHIVE_URL" ||
	die "could not download $VMOD_SOURCE_ARCHIVE_URL"
mv "$archive.part" "$archive"
ls -l "$archive"

note "2 -- assert the pinned archive digest"
assert_sha256 "$archive" "$VMOD_SOURCE_ARCHIVE_SHA256"

note "3 -- the recorded tag still peels to the recorded commit"
# ^{} is the peeled entry an annotated tag publishes alongside itself, so this
# resolves the commit without fetching an object. A lightweight tag has no
# peeled entry, hence the fallback -- and the fallback is not a relaxation:
# either way the value compared is the commit the tag names today.
peeled=$(git ls-remote "$VMOD_CLONE_URL" "refs/tags/$VMOD_SOURCE_REF^{}" | awk '{ print $1 }')
[ -n "$peeled" ] ||
	peeled=$(git ls-remote "$VMOD_CLONE_URL" "refs/tags/$VMOD_SOURCE_REF" | awk '{ print $1 }')
[ -n "$peeled" ] || die "tag $VMOD_SOURCE_REF does not exist at $VMOD_CLONE_URL"
printf 'tag      : %s\nresolves : %s\nrecorded : %s\n' \
	"$VMOD_SOURCE_REF" "$peeled" "$VMOD_SOURCE_COMMIT"
[ "$peeled" = "$VMOD_SOURCE_COMMIT" ] ||
	die "tag $VMOD_SOURCE_REF now resolves to $peeled, not the recorded commit
$VMOD_SOURCE_COMMIT. Do NOT change the recorded commit to make this pass: the
tag was moved, or the recorded identity is wrong, and which one it is has to be
established before anything is built from it."
printf 'OK: the recorded release identity is unchanged\n'

note "4 -- the archive's own version agrees with the manifest"
work=$out/unpacked
rm -rf "$work"
mkdir -p "$work"
tar -C "$work" -xzf "$archive"
root=$(find "$work" -mindepth 1 -maxdepth 1 -type d | head -1)
[ -n "$root" ] || die "$archive_name does not unpack to a single directory"
[ -f "$root/configure.ac" ] || die "$root has no configure.ac to cross-check"
declared=$(sed -n 's/^AC_INIT(\[[^]]*\],[[:space:]]*\[\([^]]*\)\].*/\1/p' "$root/configure.ac" | head -1)
printf 'AC_INIT  : %s\nmanifest : %s\n' "$declared" "$VMOD_SOURCE_VERSION"
[ "$declared" = "$VMOD_SOURCE_VERSION" ] ||
	die "the archive declares version '$declared' but the manifest records
'$VMOD_SOURCE_VERSION'. This is the same cross-check the cachetag lane runs
against configure.ac after its checkout; it exists so a manifest can never
describe source it was not written for."
printf 'OK: the archive is version %s\n' "$declared"

# The archive keeps upstream's own filename here. Renaming it to the Debian
# `.orig.tar.gz` form is the generate stage's job, because that name is derived
# from the package name in the overlay and this stage deliberately knows
# nothing about packaging.
rm -rf "$work"
note "source stage complete"
ls -l "$out"
