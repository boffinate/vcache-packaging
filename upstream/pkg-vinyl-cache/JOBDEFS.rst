Job definitions from Jenkins
============================

To provide visibility into the packaging jobs, here are the job definitions from the Jenkins server.

VC-4.1-deb-nosystemd
--------------------

This job builds for Debian-related distributions that do not use systemd:

    # Jenkins performs a checkout similar to this:
    # git clone --branch main git@code.vinyl-cache.org:vinyl-cache/pkg-vinyl-cache.git pkg-vinyl-cache
    cd pkg-vinyl-cache

    git log -1
    ./dl-source $RELEASEVERSION sources $VERIFY $SOURCEURL

    # This is where we put the list of platforms moving forward.
    BINDISTS="wheezy precise trusty"
    export BINDISTS

    # This script reads BINDISTS and DEBVERSION if set.
    ./package-deb

    cd ..
    ln -s pkg-vinyl-cache/build deb-build


VC-4.1-deb-systemd
--------------------

This job builds for Debian-related distributions that do use systemd:

    # git clone --branch main git@code.vinyl-cache.org:vinyl-cache/pkg-vinyl-cache.git pkg-vinyl-cache
    cd pkg-vinyl-cache

    git log -1
    ./dl-source $RELEASEVERSION sources $VERIFY $SOURCEURL

    # This is where we put the list of platforms moving forward.
    BINDISTS="stretch jessie xenial"
    export BINDISTS

    # This script reads BINDISTS and DEBVERSION if set.
    ./package-deb

    cd ..
    ln -s pkg-vinyl-cache/build deb-build


VC-4.1-rpm-el6
--------------

This job builds rpms for EL7:

    # git clone --branch main git@code.vinyl-cache.org:vinyl-cache/pkg-vinyl-cache.git pkg-vinyl-cache
    cd pkg-vinyl-cache
    git log -1
    ./dl-source $RELEASEVERSION sources $VERIFY $SOURCEURL

    ELVER=el6 ./package-rpm
    mv build ../rpm-build


VC-4.1-rpm-el7
--------------

This job builds rpms for EL7:

    # git clone --branch main git@code.vinyl-cache.org:vinyl-cache/pkg-vinyl-cache.git pkg-vinyl-cache
    cd pkg-vinyl-cache
    git log -1
    ./dl-source $RELEASEVERSION sources $VERIFY $SOURCEURL

    ELVER=el7 ./package-rpm
    mv build ../rpm-build


