# packaging/engine/varnish/varnish.spec -- provisional EL10 Varnish recipe.
# The family contract selects this spec and stamps its identity. The exact
# archive layout and normalized payload remain target-container proof; this
# recipe is deliberately not publishable while engines.yml says packages false.
%{!?engine_version:%{error:pass --define "engine_version <ver>"}}
%{!?engine_release:%{error:pass --define "engine_release <package-revision>"}}
%{!?engine_srcdir:%global engine_srcdir varnish-%{engine_version}}
%{!?build_date:%global build_date Thu Jan 01 1970}

%global debug_package %{nil}
%global _lto_cflags %{nil}

Name:           varnish
Version:        %{engine_version}
Release:        %{engine_release}%{?dist}
Summary:        High-performance HTTP accelerator (matrix build)
License:        BSD-2-Clause
URL:            https://varnish-cache.org/
Source0:        varnish-%{engine_version}.tar.gz

BuildRequires:  gcc make autoconf automake autoconf-archive libtool pkgconfig
BuildRequires:  python3 python3-docutils python3-sphinx diffutils
BuildRequires:  libedit-devel ncurses-devel pcre2-devel jemalloc-devel libunwind-devel

Requires:       gcc

%description
Varnish Cache is a high-performance HTTP accelerator. This provisional
matrix-build recipe has no systemd integration or distribution replacement
policy. It remains disabled until native target-container package proof.

%package devel
Summary:        Development files for %{name}
Requires:       %{name}%{?_isa} = %{version}-%{release}
Requires:       pkgconfig
Requires:       python3

%description devel
Headers, varnishapi.pc, autoconf macros and generator tools needed to build a
VMOD against Varnish Cache. A VMOD is bound to the exact runtime build.

%prep
%setup -q -n %{engine_srcdir}

%build
[ -x configure ] || ./autogen.sh
%configure --disable-static --with-unwind
%make_build

%install
%make_install
find %{buildroot} -name '*.la' -delete

# Provisional conventional file lists. The pinned archive's apparent
# Vinylization must be resolved before a package-enabled Varnish engine uses
# this recipe.
%files
%{_sbindir}/*
%{_bindir}/*
%{_libdir}/libvarnishapi.so.*
%dir %{_libdir}/varnish
%{_libdir}/varnish/vmods/
%{_datadir}/varnish/vcl/
%{_pkgdocdir}/
%{_mandir}/man1/*
%{_mandir}/man3/*
%{_mandir}/man7/*

%files devel
%{_includedir}/varnish/
%{_libdir}/libvarnishapi.so
%{_libdir}/pkgconfig/varnishapi.pc
%{_datadir}/aclocal/*.m4
%{_datadir}/varnish/vmodtool.py
%{_datadir}/varnish/vsctool.py
%{_datadir}/varnish/vcc/

%changelog
* %{build_date} Vinyl Cache matrix CI <vcache-matrix-ci@invalid> - %{engine_version}-%{engine_release}
- Provisional matrix recipe; target-container payload proof is required.
