#!/bin/sh
#
# Generate stage: render one VMOD's native recipe into a per-row work
# directory, and lay out the source tree the buildroot will consume.
#
#   generate.sh --manifest PATH --overlay PATH --id ID --cohort ID \
#               --target ID --archive FILE --out DIR [--inject-token]
#
# AGENTS.md's rule that generated content is never hand-edited applies with
# full force to what this produces. The recipe is an output: if it disagrees
# with the manifest, the overlay or the adapter, the defect is in one of those
# or in the generator, and the fix goes there. Nothing downstream may patch it,
# not even to unblock a build.
#
# --inject-token is the plan's verification case 4 and is reachable only from a
# dispatched workflow. It writes an unresolved @TOKEN@ into an already-rendered
# recipe, which is a different failure from the generator refusing to render:
# it proves the *lane* catches a recipe that a build would consume literally,
# not merely that the generator refuses one. The generator's own refusal is
# covered by tools/vmod_recipe_selftest.py.

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../../.." && pwd)
. "$here/lib.sh"

manifest=
overlay=
vmod_id=
cohort=
target=
archive=
out=
channel=release
inject_token=no

while [ $# -gt 0 ]; do
	case $1 in
	--manifest) manifest=${2:?}; shift 2 ;;
	--overlay) overlay=${2:?}; shift 2 ;;
	--id) vmod_id=${2:?}; shift 2 ;;
	--cohort) cohort=${2:?}; shift 2 ;;
	--target) target=${2:?}; shift 2 ;;
	--archive) archive=${2:?}; shift 2 ;;
	--out) out=${2:?}; shift 2 ;;
	--channel) channel=${2:?}; shift 2 ;;
	--inject-token) inject_token=yes; shift ;;
	*) die "unknown argument $1" ;;
	esac
done

for required in manifest overlay vmod_id cohort target archive out; do
	eval "value=\$$required"
	[ -n "$value" ] || die "--${required} is required"
done
[ -f "$archive" ] || die "no source archive at $archive"

# The maintainer and the changelog suite are project identities that already
# live in the lane pin file. The generator refuses without a maintainer rather
# than inventing a placeholder, so they are passed in from the one place that
# already records them instead of being duplicated into the overlay.
# shellcheck source=/dev/null
. "$repo/recipes/debian-13/pins.env"
: "${MAINTAINER_NAME:?}" "${MAINTAINER_EMAIL:?}" "${DEBIAN_DISTRIBUTION:?}"

recipe_dir=$out/recipe
build_dir=$out/build
rm -rf "$recipe_dir" "$build_dir"
mkdir -p "$recipe_dir" "$build_dir"

note "render the native recipe for $vmod_id on $target"
python3 "$repo/tools/vmod_recipe.py" generate \
	--manifest "$manifest" \
	--overlay "$overlay" \
	--cohort "$cohort" \
	--target "$target" \
	--channel "$channel" \
	--maintainer "$MAINTAINER_NAME <$MAINTAINER_EMAIL>" \
	--debian-distribution "$DEBIAN_DISTRIBUTION" \
	--out "$recipe_dir"

names=$out/names.json
python3 "$repo/tools/vmod_recipe.py" names \
	--manifest "$manifest" --overlay "$overlay" \
	--cohort "$cohort" --target "$target" --channel "$channel" > "$names"
cat "$names"

[ -f "$recipe_dir/generation-record.json" ] ||
	die "the generator produced no generation record; there is nothing to record as evidence"

if [ "$inject_token" = yes ]; then
	note "INJECTED: writing an unresolved token into the rendered recipe"
	# After rendering, on purpose. The generator already refused everything it
	# could refuse; this proves the lane refuses what reaches it.
	find "$recipe_dir" -type f -name 'control' -o -type f -name '*.spec' |
		while IFS= read -r f; do
			printf '\n# @INJECTED_UNRESOLVED_TOKEN@\n' >> "$f"
		done
fi

note "no unsubstituted token may reach a build"
# The same two-sided discipline libvmod-cachetag/packaging/check-tokens.sh
# applies to the hand-written recipes. The generator refuses a token it cannot
# resolve; this refuses one that survived anyway, from any cause.
if leftover=$(grep -rn '@[A-Z][A-Z0-9_]\{1,\}@' "$recipe_dir" 2>/dev/null); then
	printf '%s\n' "$leftover" >&2
	die "an unsubstituted token is present in the generated recipe. This is never
fixed by editing the recipe: the recipe is generated content. Fix the manifest,
the overlay, the adapter or the generator."
fi
printf 'OK: no unsubstituted tokens in the generated recipe\n'

note "lay out the source tree"
# One tree, unpacked from the verified archive, with the generated recipe
# placed into it. The archive is copied under the Debian orig name as well,
# because dpkg-source needs it beside the tree with exactly that name.
stem=$(python3 - "$names" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["source_package_name"])
PY
)
version=$(python3 - "$names" <<'PY'
import json, sys
name = json.load(open(sys.argv[1]))["upstream_archive"]
print(name[:-len(".tar.gz")].rsplit("-", 1)[1])
PY
)
srcdir=$build_dir/$stem-$version
tar -C "$build_dir" -xzf "$archive"
unpacked=$(find "$build_dir" -mindepth 1 -maxdepth 1 -type d | head -1)
[ -n "$unpacked" ] || die "the archive does not unpack to a single directory"
[ "$unpacked" = "$srcdir" ] || mv "$unpacked" "$srcdir"
cp -p "$archive" "$build_dir/${stem}_${version}.orig.tar.gz"

if [ -d "$recipe_dir/debian" ]; then
	cp -R "$recipe_dir/debian" "$srcdir/debian"
	chmod 0755 "$srcdir/debian/rules"
	[ ! -d "$srcdir/debian/source" ] || chmod 0755 "$srcdir/debian/source"
fi

note "stage the verification scripts and the ported VTCs into the lane"
# The verify stages run in a container that mounts ONLY the lane directory --
# no repository checkout, deliberately, because a fresh container that has
# never seen the build tree is the whole point. So everything they need has to
# be placed here first.
mkdir -p "$out/scripts" "$out/tests"
cp -p "$here/container/verify-deb.sh" "$here/container/verify-rpm.sh" \
	"$here/container/check-build-flags.sh" "$out/scripts/"
chmod 0755 "$out/scripts"/*.sh
tests_dir=$repo/recipes/vmods/overlays/$vmod_id/tests
if [ -d "$tests_dir" ]; then
	cp -p "$tests_dir"/*.vtc "$out/tests/"
	ls -1 "$out/tests"
else
	die "$tests_dir does not exist: a VMOD with no ported behaviour suite cannot be
verified against its installed package, and load-only verification is
explicitly insufficient (Step 5 exit gate)."
fi

note "generate stage complete"
find "$out" -maxdepth 3 -type f | sort
