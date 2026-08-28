# The family contract selects this spec and stamps its identity.
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
Source1:        varnish.service
Source2:        varnish.reload

BuildRequires:  gcc make autoconf automake autoconf-archive libtool pkgconfig
BuildRequires:  python3 python3-docutils python3-sphinx diffutils
BuildRequires:  libedit-devel ncurses-devel pcre2-devel jemalloc-devel libunwind-devel
BuildRequires:  openssl-devel
BuildRequires:  systemd-rpm-macros

Requires:       gcc
Requires:       openssl
Requires(pre):  shadow-utils
%{?systemd_requires}

%description
Varnish Cache is a high-performance HTTP accelerator. This package includes
systemd integration based on the official Varnish package.

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
# The command is compiled into varnishd and runs on the user's machine, where
# RPM's build-only hardening spec files are not installed. Keep the hardening
# flags for varnishd itself while removing only those external file references
# from runtime VCL compilation.
VCC_CFLAGS=$(echo "%{build_cflags}" | sed -e 's|-specs=[^ ]*||g')
export VCC_CC="exec %{__cc} $VCC_CFLAGS %%w -pthread -fpic -shared -Wl,-x -o %%o %%s"
%configure --disable-static --localstatedir=/var/lib --with-unwind
%make_build

%install
%make_install
find %{buildroot} -name '*.la' -delete
install -D -m 0644 etc/example.vcl %{buildroot}%{_sysconfdir}/varnish/default.vcl
install -D -m 0644 %{SOURCE1} %{buildroot}%{_unitdir}/varnish.service
install -D -m 0755 %{SOURCE2} %{buildroot}%{_sbindir}/varnishreload
install -d %{buildroot}%{_sharedstatedir}/varnish

%pre
getent group varnish >/dev/null || groupadd -r varnish
getent passwd varnish >/dev/null || useradd -r -g varnish -d /nonexistent -s /sbin/nologin -c "Varnish Cache" varnish

%post
%systemd_post varnish.service

%preun
%systemd_preun varnish.service

%postun
%systemd_postun_with_restart varnish.service

%files
%config(noreplace) %{_sysconfdir}/varnish/default.vcl
%{_unitdir}/varnish.service
%dir %attr(0755,varnish,varnish) %{_sharedstatedir}/varnish
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
- Build the engine and install its systemd integration.
