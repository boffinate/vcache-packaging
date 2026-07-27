#!/bin/sh
# Generate the varnish-name compatibility shim inside the vinyl lane image.
#
# vinyl-cache-dev ships only the renamed development surface (vinylapi.pc,
# VINYL_* aclocal macros, vinyl* binaries). Third-party VMODs probe for the
# varnish names, so without this shim every configure fails on the rename
# and the survey would measure nothing else. The shim is survey-local; it is
# not something the vinyl packages ship.
#
# Three parts:
#   varnishapi.pc      copy of vinylapi.pc advertising SHIM_API_VERSION (the
#                      trunk package says "Version: trunk", which breaks the
#                      AS_VERSION_COMPARE in VARNISH_PREREQ; a deb-derived
#                      number would skew VARNISH_PREREQ floors against the
#                      varnish lane's release version — see pins.env)
#   varnish.m4         vinyl.m4 with the macro/variable/module names renamed
#   varnish* symlinks  for configure scripts probing the tool names
#
# The native vinylapi.pc's Version is rewritten to the same value, so both
# names present the identical version surface on both lanes.

set -eu

version="${SHIM_API_VERSION:?SHIM_API_VERSION must be set (see harness/pins.env)}"
pkg_version=$(dpkg-query -W -f '${Version}' vinyl-cache)

pc_src=$(find /usr/lib -name vinylapi.pc | head -n 1)
[ -n "$pc_src" ] || { echo "vinylapi.pc not found" >&2; exit 1; }
pc_dir=$(dirname "$pc_src")

sed -e "s/^Name:.*/Name: VarnishAPI (vinyl survey shim)/" \
    -e "s/^Description:.*/Description: Varnish API name shim over vinylapi/" \
    -e "s/^Version:.*/Version: ${version}/" \
    "$pc_src" > "$pc_dir/varnishapi.pc"
sed -i "s/^Version:.*/Version: ${version}/" "$pc_src"

# Rename the macro files, dropping vinyl's m4_pattern_forbid tripwires: they
# exist to catch un-migrated VARNISH_* usage, which is exactly what the shim
# is here to satisfy.
[ -f /usr/share/aclocal/vinyl.m4 ] || { echo "vinyl.m4 not found" >&2; exit 1; }
for m4 in vinyl:varnish vinyl-legacy:varnish-legacy; do
    src="/usr/share/aclocal/${m4%%:*}.m4"
    dst="/usr/share/aclocal/${m4##*:}.m4"
    [ -f "$src" ] || continue
    sed -e '/m4_pattern_forbid/d' \
        -e 's/VINYLAPI/VARNISHAPI/g' \
        -e 's/vinylapi/varnishapi/g' \
        -e 's/VINYL_/VARNISH_/g' \
        "$src" > "$dst"
done

for tool in vinyld vinyladm vinylstat vinyllog vinylncsa vinylhist vinyltop vinyltest; do
    path=$(command -v "$tool" 2>/dev/null) || continue
    alias_name=$(echo "$tool" | sed 's/^vinyl/varnish/')
    ln -sf "$path" "/usr/local/bin/$alias_name"
done

echo "shim installed: $pc_dir/varnishapi.pc (Version: ${version}, package: ${pkg_version}), /usr/share/aclocal/varnish.m4"
