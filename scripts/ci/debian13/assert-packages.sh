#!/bin/bash
#
# ABI and hardening assertions against the sbuild-produced .deb files in
# dist/debian-13/. This is the ONE deliberate piece of logic duplication in
# this design (see DESIGN.md sections 2 and 4, point 7): the equivalent
# checks already exist in recipes/debian-13/container/stage-vinyl.sh and
# stage-cachetag.sh, interleaved with their own `dpkg-buildpackage` call, so
# they cannot be called standalone against a package sbuild produced instead.
# They are reproduced here verbatim (not reinvented), attributed by comment
# to their line ranges in each source file at the time this draft was
# written, and DESIGN.md section 7 recommends factoring the two source files
# so a future change only has to happen once.
#
# Does not need to run as root; only reads the produced .deb files.
#
# DRAFT, unexecuted -- see ../../../DESIGN.md sections 2 and 4.

set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$here/pinned.sh"
. "$(CDPATH= cd -- "$here/../lib" && pwd)/common.sh"

repo_dir=$(CDPATH= cd -- "$here/../../.." && pwd)
out_dir=$repo_dir/dist/debian-13

command -v dpkg-deb >/dev/null 2>&1 || die "dpkg-deb not available on this runner"
command -v readelf >/dev/null 2>&1 || {
	note "installing binutils for readelf"
	sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends binutils
}

hardening_check() {
	# Mirrors stage-vinyl.sh's/stage-cachetag.sh's `check()` helper.
	if [ "$1" -eq 0 ]; then printf 'PASS  %-18s %s\n' "$2" "$3"; else printf 'FAIL  %-18s %s\n' "$2" "$3"; _fail=1; fi
}

inspect_hardening() {
	# ELF_PATH LABEL
	_elf=$1; _label=$2
	_fail=0
	_dyn=$(readelf -W --dyn-syms --syms "$_elf" 2>/dev/null || true)
	_seg=$(readelf -W -l "$_elf" 2>/dev/null || true)
	_dynm=$(readelf -W -d "$_elf" 2>/dev/null || true)
	_hdr=$(readelf -W -h "$_elf" 2>/dev/null || true)
	case "$_dyn" in *__stack_chk_fail*) hardening_check 0 stack-protector "referenced ($_label)" ;; *) hardening_check 1 stack-protector "absent ($_label)" ;; esac
	case "$_seg" in *GNU_RELRO*) hardening_check 0 relro-segment "present ($_label)" ;; *) hardening_check 1 relro-segment "absent ($_label)" ;; esac
	case "$_dynm" in *BIND_NOW*|*NOW*) hardening_check 0 bind-now "set ($_label)" ;; *) hardening_check 1 bind-now "absent ($_label)" ;; esac
	case "$_hdr" in *"Type:"*DYN*) hardening_check 0 pie "ELF type DYN ($_label)" ;; *) hardening_check 1 pie "not DYN ($_label)" ;; esac
	_chk=$( { printf '%s' "$_dyn" | grep -oE '__[a-z0-9_]+_chk\b' || true; } | sort -u | tr '\n' ' ')
	if [ -n "$_chk" ]; then hardening_check 0 fortify-source "$_chk ($_label)"; else hardening_check 1 fortify-source "no __*_chk symbols ($_label)"; fi
	[ "$_fail" -eq 0 ] || die "hardening inspection failed for $_label"
}

###############################################################################
note "vinyl-cache runtime: ABI Provides (mirrors stage-vinyl.sh assertions)"
###############################################################################

runtime_deb=$(ls "$out_dir"/vinyl-cache_"${VINYL_PACKAGE_VERSION}"_*.deb)
provides=$(dpkg-deb -f "$runtime_deb" Provides)
printf 'Provides: %s\n' "$provides"
case "$provides" in
	*"vinyld-abi-$VINYL_STRICT_ABI"*) printf 'OK: runtime provides vinyld-abi-%s\n' "$VINYL_STRICT_ABI" ;;
	*) die "runtime does not provide vinyld-abi-$VINYL_STRICT_ABI" ;;
esac
case "$provides" in
	*"vinyld-vrt (= "*) printf 'OK: runtime provides a versioned vinyld-vrt\n' ;;
	*) die "runtime does not provide vinyld-vrt" ;;
esac

dev_deb=$(ls "$out_dir"/vinyl-cache-dev_"${VINYL_PACKAGE_VERSION}"_*.deb)
dev_depends=$(dpkg-deb -f "$dev_deb" Depends)
printf 'vinyl-cache-dev Depends: %s\n' "$dev_depends"
case "$dev_depends" in
	*"vinyl-cache (= $VINYL_PACKAGE_VERSION)"*) printf 'OK: dev package depends on the exact matching runtime\n' ;;
	*) die "dev package does not depend on the exact matching runtime" ;;
esac

note "vinyl-cache runtime: hardening inspection (production profile)"
mkdir -p /tmp/hx-vinyl
dpkg-deb -x "$runtime_deb" /tmp/hx-vinyl
inspect_hardening /tmp/hx-vinyl/usr/sbin/vinyld vinyld

###############################################################################
note "libvmod-cachetag: ABI Depends and content (mirrors stage-cachetag.sh assertions)"
###############################################################################

cachetag_deb=$(ls "$out_dir"/libvmod-cachetag_"${CACHETAG_DEBIAN_VERSION}"_*.deb)
depends=$(dpkg-deb -f "$cachetag_deb" Depends)
printf 'Depends: %s\n' "$depends"
case "$depends" in
	*"vinyld-abi-$VINYL_STRICT_ABI"*) printf 'OK: depends on vinyld-abi-%s\n' "$VINYL_STRICT_ABI" ;;
	*) die "missing exact strict-ABI dependency" ;;
esac
case "$depends" in
	*"vinyld-vrt"*) printf 'OK: depends on vinyld-vrt\n' ;;
	*) die "missing vinyld-vrt dependency" ;;
esac

vmoddir=$(sed -n 3p "$repo_dir/dist/debian-13/work/target.txt")
[ -n "$vmoddir" ] || die "cannot read the target VMOD directory from dist/debian-13/work/target.txt"
contents=$(dpkg-deb -c "$cachetag_deb")
case "$contents" in
	*"$vmoddir/libvmod_cachetag.so"*) printf 'OK: libvmod_cachetag.so is packaged into %s\n' "$vmoddir" ;;
	*) die "libvmod_cachetag.so is not in $vmoddir" ;;
esac
stray=$(printf '%s\n' "$contents" | { grep -E '\.(la|a)$' || true; })
[ -z "$stray" ] || die "libtool archive or static library shipped: $stray"
printf 'OK: no libtool archives or static libraries\n'

note "libvmod-cachetag: hardening inspection"
mkdir -p /tmp/hx-cachetag
dpkg-deb -x "$cachetag_deb" /tmp/hx-cachetag
inspect_hardening "/tmp/hx-cachetag$vmoddir/libvmod_cachetag.so" libvmod_cachetag.so

printf '\nOK: all ABI and hardening assertions passed against the sbuild-produced packages\n'
