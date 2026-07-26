#!/bin/sh
# Generate the vinyl-name compatibility shim inside the varnish lane image.
#
# Mirror image of make-shim.sh: some actively maintained VMODs have already
# migrated to the vinyl development names (vinylapi.pc, VINYL_* macros) and
# would otherwise fail on the varnish lane for naming reasons alone. With
# shims in both directions the lanes are name-agnostic and the sweep
# measures API compatibility only.

set -eu

pc_src=$(find /usr/local/lib /usr/lib -name varnishapi.pc 2>/dev/null | head -n 1)
[ -n "$pc_src" ] || { echo "varnishapi.pc not found" >&2; exit 1; }
pc_dir=$(dirname "$pc_src")

sed -e "s/^Name:.*/Name: VinylAPI (varnish survey shim)/" \
    -e "s/^Description:.*/Description: Vinyl API name shim over varnishapi/" \
    "$pc_src" > "$pc_dir/vinylapi.pc"

aclocal_dir=$(dirname "$(find /usr/local/share/aclocal /usr/share/aclocal -name varnish.m4 2>/dev/null | head -n 1)")
[ -f "$aclocal_dir/varnish.m4" ] || { echo "varnish.m4 not found" >&2; exit 1; }
for m4 in varnish:vinyl varnish-legacy:vinyl-legacy; do
    src="$aclocal_dir/${m4%%:*}.m4"
    dst="$aclocal_dir/${m4##*:}.m4"
    [ -f "$src" ] || continue
    sed -e '/m4_pattern_forbid/d' \
        -e 's/VARNISHAPI/VINYLAPI/g' \
        -e 's/varnishapi/vinylapi/g' \
        -e 's/VARNISH_/VINYL_/g' \
        "$src" > "$dst"
done

for tool in varnishd varnishadm varnishstat varnishlog varnishncsa varnishhist varnishtop varnishtest; do
    path=$(command -v "$tool" 2>/dev/null) || continue
    alias_name=$(echo "$tool" | sed 's/^varnish/vinyl/')
    ln -sf "$path" "/usr/local/bin/$alias_name"
done

echo "reverse shim installed: $pc_dir/vinylapi.pc, $aclocal_dir/vinyl.m4"
