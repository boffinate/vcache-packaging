#!/bin/bash
#
# EL9 lane, in-container half. Runs inside the almalinux:9 build container that
# recipes/el9/build.sh starts; never on the host.
#
# Mounts, all established by build.sh:
#   /recipes      recipes/el9, read-only
#   /vinyl-src    the pinned Vinyl Cache checkout, read-only
#   /cachetag     the libvmod-cachetag checkout, read-only
#   /out          dist/el9, writable; the only place anything is written
#
# Stages are named on the command line and run in the order given.

set -euo pipefail

. /recipes/cohort.env

topdir=/out/rpmbuild
logdir=/out/logs
srcdir=$topdir/SOURCES
vinyl_srcname=vinyl-cache-$VINYL_VERSION
vinyl_evr="$VINYL_VERSION-$VINYL_RELEASE.el9"

mkdir -p "$topdir"/{SOURCES,SPECS,SRPMS,RPMS,BUILD,BUILDROOT} "$logdir" /out/packages

say() { printf '\n===== %s =====\n' "$*"; }

rpmb() {
	rpmbuild --define "_topdir $topdir" "$@"
}

changelog_date() {
	LC_ALL=C date -u -d "@$VINYL_SOURCE_DATE_EPOCH" '+%a %b %d %Y'
}

# ---------------------------------------------------------------- stage: deps

stage_deps() {
	say "build dependencies"
	dnf -y install dnf-plugins-core
	# CRB (CodeReady Builder) carries python3-sphinx; EPEL carries
	# libunwind-devel and, on x86_64, jemalloc-devel. Both are enabled here
	# rather than in the image so the supplying repository of every build
	# dependency is a recorded fact.
	dnf config-manager --set-enabled crb
	dnf -y install epel-release
	# Weak dependencies off: a buildroot is a recorded input, and recommends
	# drag in a desktop stack (fonts, gtk, tracker) that has nothing to do
	# with building an HTTP accelerator and would make the recorded
	# dependency list useless as an audit artefact.
	dnf -y --setopt=install_weak_deps=False install \
		autoconf autoconf-archive automake libtool \
		diffutils file findutils gcc make patch \
		libedit-devel libunwind-devel ncurses-devel pcre2-devel \
		pkgconf-pkg-config \
		python3 python3-docutils python3-sphinx \
		rpm-build rpmlint redhat-rpm-config systemd-rpm-macros \
		git tar gzip which procps-ng
	case "$(uname -m)" in
	aarch64) : ;;
	*) dnf -y --setopt=install_weak_deps=False install jemalloc-devel ;;
	esac

	say "resolved build dependencies and their repositories"
	dnf repoquery --installed --qf '%{name}-%{evr}.%{arch}\t%{from_repo}\n' \
		2>/dev/null | sort | tee "$logdir/buildroot-packages.tsv"
	rpm -q rpm gcc make autoconf automake libtool python3 \
		| tee "$logdir/buildroot-toolchain.txt"
}

# -------------------------------------------------------------- stage: source

# `git archive` is the pinned-source export the plan asks for, but it drops the
# .git directory that Vinyl's include/generate.py reads to derive VCS_Version.
# Without it that script does not fail; it writes the literal string NOGIT and
# the build produces a well-formed package advertising a completely wrong ABI.
# The generated headers are therefore synthesised here, from the pinned commit,
# exactly as `make dist` from a git checkout would have shipped them.
stage_source() {
	say "export pinned Vinyl source"
	git config --global --add safe.directory /vinyl-src
	git config --global --add safe.directory /vinyl-src/bin/vinyltest/vtest2

	rm -rf /tmp/src && mkdir -p /tmp/src
	git -C /vinyl-src archive --format=tar \
		--prefix="$vinyl_srcname/" "$VINYL_GIT_COMMIT" \
		| tar -C /tmp/src -xf -
	git -C /vinyl-src/bin/vinyltest/vtest2 archive --format=tar \
		--prefix="$vinyl_srcname/bin/vinyltest/vtest2/" "$VINYL_VTEST2_COMMIT" \
		| tar -C /tmp/src -xf -

	test -f "/tmp/src/$vinyl_srcname/bin/vinyltest/vtest2/src/vtc_main.c"

	cat > "/tmp/src/$vinyl_srcname/include/vcs_version.h" <<EOF
/* $VINYL_GIT_COMMIT */
/*
 * NB:  This file is machine generated, DO NOT EDIT!
 *
 * Edit and run include/generate.py instead.
 */

#define VCS_Version "$VINYL_GIT_COMMIT"
EOF

	cat > "/tmp/src/$vinyl_srcname/include/vmod_abi.h" <<EOF
/*
 * NB:  This file is machine generated, DO NOT EDIT!
 *
 * Edit and run include/generate.py instead.
 */

#define VMOD_ABI_Version "$VINYL_PACKAGE_STRING $VINYL_GIT_COMMIT"
EOF

	# Deterministic archive: fixed mtime, ownership, and member order.
	tar --sort=name \
		--mtime="@$VINYL_SOURCE_DATE_EPOCH" \
		--owner=0 --group=0 --numeric-owner \
		--format=gnu \
		-C /tmp/src -cf - "$vinyl_srcname" \
		| gzip -n -9 > "$srcdir/$vinyl_srcname.tar.gz"

	sha256sum "$srcdir/$vinyl_srcname.tar.gz" | tee "$logdir/vinyl-source.sha256"
}

# --------------------------------------------------------------- stage: vinyl

stage_vinyl() {
	say "generate the vinyl-cache spec from cohort.env"
	sed \
		-e "s|@VINYL_VERSION@|$VINYL_VERSION|g" \
		-e "s|@VINYL_RELEASE@|$VINYL_RELEASE|g" \
		-e "s|@VINYL_GIT_COMMIT@|$VINYL_GIT_COMMIT|g" \
		-e "s|@COHORT_ID@|$COHORT_ID|g" \
		-e "s|@RPM_CHANGELOG_DATE@|$(changelog_date)|g" \
		-e "s|@MAINTAINER_NAME@|$MAINTAINER_NAME|g" \
		-e "s|@MAINTAINER_EMAIL@|$MAINTAINER_EMAIL|g" \
		/recipes/vinyl-cache.spec.in > "$topdir/SPECS/vinyl-cache.spec"
	if grep -n '@[A-Z_]\+@' "$topdir/SPECS/vinyl-cache.spec"; then
		echo "E: unsubstituted token reached the generated spec" >&2
		exit 1
	fi

	say "vinyl-cache source RPM"
	install -m 0755 /recipes/find-provides "$srcdir/"
	install -m 0644 /recipes/systemd/vinyl-cache.service "$srcdir/"
	install -m 0644 /recipes/systemd/vinylncsa.service "$srcdir/"
	install -m 0755 /recipes/systemd/vinylreload "$srcdir/"
	install -m 0644 /recipes/systemd/vinyl-cache.logrotate "$srcdir/"
	install -m 0644 /recipes/systemd/vinyl-cache.tmpfiles "$srcdir/"
	install -m 0644 /recipes/systemd/vinyl-cache.sysusers "$srcdir/"

	export SOURCE_DATE_EPOCH="$VINYL_SOURCE_DATE_EPOCH"
	rpmb -bs "$topdir/SPECS/vinyl-cache.spec" 2>&1 | tee "$logdir/vinyl-srpm.log"

	say "vinyl-cache binary RPMs (rebuild from the source RPM)"
	rpmb --rebuild "$topdir/SRPMS/vinyl-cache-$vinyl_evr.src.rpm" \
		${VINYL_UNPACKAGED_OK:+--define "_unpackaged_files_terminate_build 0"} \
		2>&1 | tee "$logdir/vinyl-build.log"

	if [ -n "${VINYL_UNPACKAGED_OK:-}" ]; then
		say "buildroot contents (file-listing run)"
		find "$topdir/BUILDROOT" -mindepth 2 \
			| sed "s#$topdir/BUILDROOT/[^/]*##" | sort \
			| tee "$logdir/vinyl-buildroot-files.txt"
	fi

	find "$topdir/RPMS" -name 'vinyl-cache*.rpm' -exec cp -p {} /out/packages/ \;
	cp -p "$topdir/SRPMS/vinyl-cache-$vinyl_evr.src.rpm" /out/packages/
}

# ------------------------------------------------------------ stage: cachetag

# Substitute the packaging scaffolding's @TOKEN@ vocabulary. Values come from
# cohort.env and from the Vinyl packages that were just built, never from a
# guess: VINYL_VRT and VINYL_VMODDIR are read back out of the installed
# development package so a substitution that disagrees with reality cannot
# silently produce a package whose ABI dependency is a lie.
stage_cachetag() {
	say "install the cohort Vinyl packages"
	arch=$(uname -m)
	dnf -y install \
		/out/packages/vinyl-cache-"$vinyl_evr.$arch".rpm \
		/out/packages/vinyl-cache-devel-"$vinyl_evr.$arch".rpm

	say "read the substitution values back from the installed packages"
	vmoddir=$(pkg-config --define-variable=libdir="$(rpm --eval %{_libdir})" \
		--variable=vmoddir vinylapi)
	incdir=$(pkg-config --variable=pkgincludedir vinylapi)
	vrt_major=$(sed -n 's/^#define[[:space:]]\+VRT_MAJOR_VERSION[[:space:]]\+\([0-9]\+\).*/\1/p' "$incdir/vrt.h")
	vrt_minor=$(sed -n 's/^#define[[:space:]]\+VRT_MINOR_VERSION[[:space:]]\+\([0-9]\+\).*/\1/p' "$incdir/vrt.h")
	vrt="$vrt_major.$vrt_minor"
	abi=$(sed -n 's/^#define[[:space:]]\+VMOD_ABI_Version[[:space:]]\+"\(.*\)"[[:space:]]*$/\1/p' \
		"$incdir/vmod_abi.h" | awk 'NR == 1 { print $NF }')

	printf 'vmoddir=%s\nvrt=%s\nabi=%s\n' "$vmoddir" "$vrt" "$abi" \
		| tee "$logdir/cachetag-substitutions.txt"

	test "$abi" = "$VINYL_STRICT_ABI"

	changelog_date=$(LC_ALL=C date -u -d "@$VINYL_SOURCE_DATE_EPOCH" '+%a %b %d %Y')

	say "substitute the cachetag spec scaffolding"
	sed \
		-e "s|@CACHETAG_VERSION@|$CACHETAG_VERSION|g" \
		-e "s|@PACKAGE_REVISION@|$CACHETAG_RELEASE|g" \
		-e "s|@SOURCE_URL@|$CACHETAG_SOURCE_URL|g" \
		-e "s|@VINYL_PACKAGE_VERSION@|$vinyl_evr|g" \
		-e "s|@VINYL_STRICT_ABI@|$VINYL_STRICT_ABI|g" \
		-e "s|@VINYL_VRT@|$vrt|g" \
		-e "s|@VINYL_VMODDIR@|$vmoddir|g" \
		-e "s|@COHORT_ID@|$COHORT_ID|g" \
		-e "s|@RPM_CHANGELOG_DATE@|$changelog_date|g" \
		-e "s|@MAINTAINER_NAME@|$MAINTAINER_NAME|g" \
		-e "s|@MAINTAINER_EMAIL@|$MAINTAINER_EMAIL|g" \
		/cachetag/packaging/rpm/libvmod-cachetag.spec \
		> "$topdir/SPECS/libvmod-cachetag.spec"

	# An unsubstituted template must never reach a build.
	sh /cachetag/packaging/check-tokens.sh --substituted "$topdir/SPECS"

	say "stage the canonical cachetag source archive"
	install -m 0644 "/cachetag/release/dist/$CACHETAG_TARBALL" "$srcdir/"
	echo "$CACHETAG_SHA256  $srcdir/$CACHETAG_TARBALL" | sha256sum -c -

	say "libvmod-cachetag source RPM"
	rpmbuild --define "_topdir $topdir" -bs "$topdir/SPECS/libvmod-cachetag.spec" \
		2>&1 | tee "$logdir/cachetag-srpm.log"

	say "libvmod-cachetag binary RPM (rebuild from the source RPM)"
	rpmbuild --define "_topdir $topdir" --rebuild \
		"$topdir/SRPMS/libvmod-cachetag-$CACHETAG_VERSION-$CACHETAG_RELEASE.el9.src.rpm" \
		2>&1 | tee "$logdir/cachetag-build.log"

	find "$topdir/RPMS" -name 'libvmod-cachetag*.rpm' -exec cp -p {} /out/packages/ \;
	cp -p "$topdir/SRPMS/libvmod-cachetag-$CACHETAG_VERSION-$CACHETAG_RELEASE.el9.src.rpm" \
		/out/packages/
}

# -------------------------------------------------------------- stage: report

stage_report() {
	say "package metadata"
	for f in /out/packages/*.rpm; do
		printf '\n--- %s ---\n' "${f##*/}"
		rpm -qp --qf 'Name    : %{NAME}\nVersion : %{VERSION}\nRelease : %{RELEASE}\nArch    : %{ARCH}\nSize    : %{SIZE}\n' "$f"
		printf 'Provides:\n'; rpm -qp --provides "$f" | sed 's/^/  /'
		printf 'Requires:\n'; rpm -qp --requires "$f" | sed 's/^/  /'
		printf 'Files:\n';    rpm -qlp "$f" | sed 's/^/  /'
	done | tee "$logdir/package-metadata.txt"

	say "artifact digests"
	( cd /out/packages && sha256sum ./*.rpm | sed 's#\./##' | tee /out/SHA256SUMS )

	say "hardening inspection"
	# The plan requires native hardening inspection of the production build,
	# because a configure option that reports the stack protector as enabled
	# is not evidence that any object was compiled with it.
	dnf -y --setopt=install_weak_deps=False install annobin-annocheck binutils \
		>/dev/null 2>&1 || true
	# Inspection only: nothing in this block is allowed to abort the lane.
	set +e +o pipefail
	rm -rf /tmp/harden && mkdir -p /tmp/harden
	( cd /tmp/harden && rpm2cpio /out/packages/vinyl-cache-"$vinyl_evr".*"$(uname -m)".rpm | cpio -idm --quiet )
	( cd /tmp/harden && rpm2cpio /out/packages/libvmod-cachetag-*"$(uname -m)".rpm | cpio -idm --quiet 2>/dev/null || true )
	{
		for elf in /tmp/harden/usr/sbin/vinyld \
			/tmp/harden/usr/lib64/libvinylapi.so.3.*.* \
			/tmp/harden/usr/lib64/vinyl-cache/vmods/libvmod_cachetag.so; do
			[ -f "$elf" ] && [ ! -L "$elf" ] || continue
			printf '\n--- %s ---\n' "${elf#/tmp/harden}"
			printf 'type          : %s\n' "$(readelf -h "$elf" | awk '/Type:/ {print $2}')"
			printf 'RELRO         : %s\n' "$(readelf -lW "$elf" | grep -c GNU_RELRO)"
			printf 'BIND_NOW      : %s\n' "$(readelf -dW "$elf" | grep -c 'BIND_NOW\|FLAGS.*NOW')"
			printf 'stack protector: %s\n' "$(readelf -sW "$elf" | grep -c '__stack_chk_fail')"
			printf 'fortified libc calls: %s\n' "$(readelf -sW "$elf" | grep -c '_chk@')"
			# annocheck is informational here, not a gate. Its
			# stack-prot and stack-clash tests read annobin notes,
			# which native debuginfo generation moves into the
			# -debuginfo package, so it reports MAYB rather than
			# PASS on a stripped binary and exits non-zero. The
			# readelf evidence above is the primary check; a full
			# annocheck run against installed debuginfo is CI work.
			if command -v annocheck >/dev/null 2>&1; then
				{ annocheck "$elf" 2>&1 || true; } | grep -E 'FAIL|MAYB|Overall' | sort -u
			else
				echo "annocheck not available in this buildroot"
			fi
		done
	} | tee "$logdir/hardening.txt"
	set -e -o pipefail
}

# ---------------------------------------------------------------- stage: lint

stage_lint() {
	say "rpmlint"
	rpmlint --version
	set +e
	rpmlint /out/packages/*.rpm 2>&1 | tee "$logdir/rpmlint.log"
	set -e
	tail -n 3 "$logdir/rpmlint.log"
}

# ------------------------------------------------------------------- dispatch

for stage in "$@"; do
	"stage_$stage"
done

say "container stages complete: $*"
