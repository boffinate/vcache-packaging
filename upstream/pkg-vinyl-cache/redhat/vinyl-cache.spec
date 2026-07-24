%global __python %{__python3}
%global vd_rc %{?v_rc:0.%{?v_rc}.}
%global debug_package %{nil}
%global _lto_cflags %{nil}
%global _use_internal_dependency_generator 0
%global __find_provides %{_builddir}/%{srcname}/find-provides %__find_provides


Summary: High-performance HTTP accelerator
Name:    vinyl-cache
Version: %{versiontag}
Release: %{?vd_rc}%{releasetag}%{?dist}
License: BSD
Group:   System Environment/Daemons
URL:     https://vinyl-cache.org/
Source:  %{srcname}.tgz

BuildRequires: diffutils
BuildRequires: gcc
%ifnarch aarch64
BuildRequires: jemalloc-devel
%endif
BuildRequires: libedit-devel
BuildRequires: make
BuildRequires: ncurses-devel
BuildRequires: pcre2-devel
BuildRequires: pkgconfig
BuildRequires: python3
BuildRequires: python3-sphinx
%if 0%{?fedora}
BuildRequires: systemd-rpm-macros
%endif

Requires: gcc
Requires: logrotate
%{?systemd_requires}
%if 0%{?rhel} >= 8
Requires: redhat-rpm-config
%endif

Provides:  vinyl-cache-libs%{?_isa} = %{version}-%{release}
Provides:  vinyl-cache-libs = %{version}-%{release}
Obsoletes: vinyl-cache-libs

Provides:  vinyl-cache-docs = %{version}-%{release}
Obsoletes: vinyl-cache-docs

Provides:  vinyl-cache-debuginfo%{?_isa} = %{version}-%{release}
Provides:  vinyl-cache-debuginfo = %{version}-%{release}
Obsoletes: vinyl-cache-debuginfo


%description
This is Vinyl Cache, a high-performance HTTP accelerator.

Vinyl Cache stores web pages in memory so web servers don't have to
create the same web page over and over again. Vinyl Cache serves
pages much faster than any application server; giving the website a
significant speed up.

Documentation wiki and additional information about Vinyl Cache is
available on: https://vinyl-cache.org/


%package devel
Summary:   Development files for %{name}
Group:     System Environment/Libraries
Requires:  %{name}%{?_isa} = %{version}-%{release}
Requires:  pkgconfig
Requires:  python(abi) >= 3.4
Provides:  vinyl-cache-libs-devel%{?_isa} = %{version}-%{release}
Provides:  vinyl-cache-libs-devel = %{version}-%{release}
Obsoletes: vinyl-cache-libs-devel


%description devel
Development files for %{name}
Vinyl Cache is a high-performance HTTP accelerator


%prep
%setup -q -n %{srcname}


%build
%configure --localstatedir=/var/lib --with-contrib
%make_build V=1


%check
%if 0%{?nocheck} == 0
%make_build check VERBOSE=1
%endif


%install
export DONT_STRIP=1
%make_install

find %{buildroot}/%{_libdir}/ -name '*.la' -exec rm -f {} ';'

mkdir -p %{buildroot}/var/lib/vinyl-cache
mkdir -p %{buildroot}/var/log/vinyl-cache
mkdir -p %{buildroot}/var/run/vinyl-cache
mkdir -p %{buildroot}%{_datadir}/%{name}
mkdir -p %{buildroot}%{_sysconfdir}/ld.so.conf.d/
install -D -m 0644 etc/example.vcl %{buildroot}%{_sysconfdir}/vinyl-cache/default.vcl
install -D -m 0644 vinyl-cache.logrotate %{buildroot}%{_sysconfdir}/logrotate.d/vinyl-cache

mkdir -p %{buildroot}%{_unitdir}
install -D -m 0644 vinyl-cache.service %{buildroot}%{_unitdir}/vinyl-cache.service
install -D -m 0644 vinylncsa.service %{buildroot}%{_unitdir}/vinylncsa.service
install -D -m 0755 vinylreload %{buildroot}%{_sbindir}/vinylreload

echo %{_libdir}/%{name} > %{buildroot}%{_sysconfdir}/ld.so.conf.d/%{name}-%{_arch}.conf


%clean
rm -rf %{buildroot}


%files
%{_sbindir}/*
%{_bindir}/*
%{_libdir}/*.so.*
%{_libdir}/%{name}
%{_var}/lib/vinyl-cache
%{_mandir}/man1/*.1*
%{_mandir}/man3/*.3*
%{_mandir}/man7/*.7*
%{_docdir}/%{name}/
%{_datadir}/%{name}
%{_unitdir}/*
%attr(-,vinyllog,vinyl) %{_var}/log/vinyl-cache
%exclude %{_datadir}/%{name}/vmodtool*
%exclude %{_datadir}/%{name}/vsctool*
%doc README*
%doc LICENSE
%doc doc/html
%doc doc/changes*.html
%doc doc/changes*.rst
%dir %{_sysconfdir}/vinyl-cache/
%config(noreplace) %{_sysconfdir}/vinyl-cache/default.vcl
%config(noreplace) %{_sysconfdir}/logrotate.d/vinyl-cache
%config %{_sysconfdir}/ld.so.conf.d/%{name}-%{_arch}.conf


%files devel
%{_libdir}/lib*.so
%{_includedir}/%{name}
%{_libdir}/pkgconfig/vinylapi.pc
%{_datadir}/%{name}/vmodtool*
%{_datadir}/%{name}/vsctool*
%{_datadir}/aclocal/*


%pre
getent group vinyl >/dev/null ||
groupadd -r vinyl

getent passwd vinyllog >/dev/null ||
useradd -r -g vinyl -d /dev/null -s /sbin/nologin \
	-c "Vinyl Cache Log User" vinyllog

getent passwd vinyl >/dev/null ||
useradd -r -g vinyl -d /var/lib/vinyl-cache -s /sbin/nologin \
	-c "Vinyl Cache Daemon User" vinyl

exit 0


%post
/sbin/ldconfig
%systemd_post vinyl-cache vinylncsa


%preun
%systemd_preun vinyl-cache vinylncsa


%postun
/sbin/ldconfig
%systemd_postun_with_restart vinyl-cache vinylncsa


%changelog
* Thu Jul 24 2014 Varnish Software <opensource@varnish-software.com> - 3.0.0-1
- This changelog is not in use. See doc/changes.rst for release notes.
