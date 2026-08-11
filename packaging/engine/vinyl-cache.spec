# packaging/engine/vinyl-cache.spec -- EL9 engine package for the matrix.
# Simplified from v1 recipes/el9/vinyl-cache.spec.in: no ABI/cohort provides,
# no external dependency generator, no systemd integration, no hardening
# ceremony. Identity is stamped at build time by scripts/build-engine.sh:
#   rpmbuild -bb --define "engine_version 9.0.1" [--define "engine_release 1"]
#            [--define "engine_srcdir <tarball top dir>"]
#            [--define "build_date $(date '+%a %b %d %Y')"]
%{!?engine_version:%{error:pass --define "engine_version <ver>"}}
%{!?engine_release:%global engine_release 1}
%{!?engine_srcdir:%global engine_srcdir vinyl-cache-%{engine_version}}
%{!?build_date:%global build_date Thu Jan 01 1970}

# No debuginfo extraction and no LTO: matrix packages, not distro packages,
# and Vinyl dlopens VMODs and compiles VCL at run time.
%global debug_package %{nil}
%global _lto_cflags %{nil}

Name:           vinyl-cache
Version:        %{engine_version}
Release:        %{engine_release}%{?dist}
Summary:        High-performance HTTP accelerator (matrix build)
License:        BSD-2-Clause
URL:            https://vinyl-cache.org/
Source0:        vinyl-cache-%{engine_version}.tar.gz

BuildRequires:  gcc make autoconf automake autoconf-archive libtool pkgconfig
BuildRequires:  python3 python3-docutils python3-sphinx diffutils
BuildRequires:  libedit-devel ncurses-devel pcre2-devel jemalloc-devel libunwind-devel

# vinyld compiles each VCL program with the system toolchain at run time.
Requires:       gcc

%description
Vinyl Cache is a high-performance HTTP accelerator. This is a
compatibility-matrix build: daemon, command line tools, shared library and
bundled VMODs, with no systemd integration and no security-update commitment.
libunwind comes from EPEL.

%package devel
Summary:        Development files for %{name}
Requires:       %{name}%{?_isa} = %{version}-%{release}
Requires:       pkgconfig
Requires:       python3

%description devel
Headers, vinylapi.pc, the vinyl autoconf macros and the vmodtool.py/vsctool.py
generators needed to build a VMOD against Vinyl Cache. A VMOD is bound to the
exact runtime build, so this package requires the exact matching vinyl-cache.

%prep
%setup -q -n %{engine_srcdir}

%build
[ -x configure ] || ./autogen.sh
# VCC_CC is compiled into the daemon and runs on the *user's* machine every
# time a VCL is loaded, so it must not name build-only files. Left to itself,
# configure seeds it from the command-line CFLAGS, and %%configure's hardening
# set includes -specs=/usr/lib/rpm/redhat/redhat-hardened-cc1 — a file shipped
# by redhat-rpm-config, which is a BuildRequires-grade package absent from any
# normal install. Every VCL compile then dies with "cannot read spec file",
# including one that imports nothing. Depending on redhat-rpm-config at
# runtime would drag 20 packages / 21 MB onto production hosts to satisfy a
# build artefact; dropping the flags from CFLAGS outright would unharden the
# daemon itself. So we set VCC_CC explicitly: upstream's own Linux/gcc shape,
# minus the -specs= options. Debian's build flags never carried -specs=, so
# this also makes the two targets compile VCL the same way.
VCC_CFLAGS=$(echo "%{build_cflags}" | sed -e 's|-specs=[^ ]*||g')
export VCC_CC="exec %{__cc} $VCC_CFLAGS %%w -pthread -fpic -shared -Wl,-x -o %%o %%s"
%configure --disable-static --with-unwind
%make_build

%install
# Relative VINYL_STATE_DIR override: the 9.0.1 tarball's install-data-local
# mkdirs the state dir without $(DESTDIR); this keeps it inside the build tree.
%make_install VINYL_STATE_DIR=var/lib/vinyl-cache
find %{buildroot} -name '*.la' -delete
# Build-time help-text generator with no runtime user.
rm -f %{buildroot}%{_bindir}/vinylstat_help_gen

%files
%{_sbindir}/*
%{_bindir}/*
%{_libdir}/libvinylapi.so.*
%dir %{_libdir}/vinyl-cache
%{_libdir}/vinyl-cache/vmods/
%dir %{_datadir}/vinyl-cache
%{_datadir}/vinyl-cache/vcl/
%{_pkgdocdir}/
%{_mandir}/man1/*
%{_mandir}/man3/*
%{_mandir}/man7/*

%files devel
%{_includedir}/vinyl-cache/
%{_libdir}/libvinylapi.so
%{_libdir}/pkgconfig/vinylapi.pc
%{_datadir}/aclocal/*.m4
%{_datadir}/vinyl-cache/vmodtool.py
%{_datadir}/vinyl-cache/vsctool.py
%{_datadir}/vinyl-cache/vcc/

%changelog
* %{build_date} Vinyl Cache matrix CI <vcache-matrix-ci@invalid> - %{engine_version}-%{engine_release}
- Automated matrix build; version stamped by scripts/build-engine.sh.
