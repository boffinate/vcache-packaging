#!/bin/bash
#
# Builds one generated-recipe VMOD's RPM with Mock, inside the pinned
# almalinux:9 container that ../run.sh starts.
#
# Mount contract (set by ../run.sh):
#   /repo   the vcache-packaging checkout, read-only
#   /lane   the per-row work directory: recipe/ the generated spec, src/ the
#           verified upstream archive, engine/ the verified engine RPMs,
#           out/ the results
#
# Structurally the same as scripts/ci/el9/container-mock.sh, and separate from
# it for the same reason build-deb.sh is separate from container-pbuilder.sh:
# that script produces cachetag's package bytes and this wave's equivalence
# contract is that they do not move. See ../lib.sh.
#
# ---------------------------------------------------------------------------
# PORTED FROM container-mock.sh, WITH ITS LINE NUMBERS.
#
# Wave B runs 30405770446 and 30407186693 each cost a CI round trip to
# rediscover one lesson that script had already learned and written down. The
# rest were then ported by reading it end to end rather than by failing again.
# Every non-obvious item below carries the line it came from, so the next
# person can diff the two files instead of re-deriving the reasoning:
#
#   :122-123  epel-release BEFORE mock, and mock-core-configs alongside it.
#             mock is not in AlmaLinux 9's own repositories.
#   :126-148  mock refuses to run as root, and /usr/bin/mock is a symlink to
#             usermode's consolehelper which exits 6 "Insufficient rights."
#             on a runner rather than degrading usefully. Every invocation
#             runs as an unprivileged user in the mock group.
#   :132-138  that user takes the uid/gid owning the bind-mounted output, so
#             mock can write its resultdir and the RPMs land owned by the
#             account that started the job. A root-owned output directory is
#             fatal, not something to work around.
#   :142      the resultdir and topdir are chowned to that user first.
#   :152-171  SOURCE_DATE_EPOCH must reach the chroot through
#             config_opts['environment']; a host export does not cross into
#             it, and EL9's redhat-rpm-config then derives the epoch from the
#             newest %changelog entry truncated to midnight UTC.
#   :158-160  the derived config pins `root` to the STOCK config's name, so
#             every invocation shares one --no-clean root regardless of which
#             config file it names. This lane had that wrong: it declared a
#             root of its own and would have built a second chroot from
#             scratch on every step.
#   :211-222  the two epoch macros are passed as --define on EVERY invocation.
#             EL9's rpm 4.16 ships %use_source_date_epoch_as_buildtime
#             defaulting to 0, so without them BUILDTIME comes from the wall
#             clock and payload mtimes are unclamped. Setting them only as
#             config macros, as this lane did, is not the measured-good form.
#   :207      an explicit `mock --init` before any build.
#   :232+     --no-clean on every invocation after --init.
#   :276-293  --addrepo, NOT `mock --install` plus --no-clean: every mock
#             --rebuild begins with a chroot init that restores the root cache
#             and discards whatever --install left, measured in run
#             30167536066. mock's dnf runs outside the chroot, so a file://
#             URL resolves. The repository is chowned to the build user.
#   :79-116   an EXIT trap copying mock's own build.log and root.log. Under
#             `set -e` a failing build kills the script before any
#             success-path copy runs, which is what made run 30344401137's EL9
#             failures undiagnosable. build.log is also the only record of
#             what %configure expanded to and which CFLAGS/LDFLAGS reached the
#             compiler -- the registry's build.configure_options, build.cflags
#             and build.ldflags have no other honest source on this lane.
# ---------------------------------------------------------------------------

set -euo pipefail

lane=/lane
out=$lane/out
topdir=$lane/rpmbuild
logdir=$lane/logs
resultdir=$lane/mockresult
localrepo=$lane/localrepo
mock_cfg=${MOCK_ROOT:-alma+epel-9-x86_64}

note() { printf '\n===== %s =====\n' "$*"; }
die() {
	printf 'E: %s\n' "$*" >&2
	exit 1
}

: "${VMOD_RPM_NAME:?}" "${VMOD_UPSTREAM_VERSION:?}" "${VMOD_RPM_RELEASE:?}"
: "${VMOD_SOURCE_DATE_EPOCH:?}"

mkdir -p "$out" "$logdir" "$resultdir" "$topdir/SPECS" "$topdir/SOURCES"

# container-mock.sh:79-116. Registered before anything can fail.
copy_mock_log() { # SRC DEST
	if [ -f "$1" ]; then
		cp -p "$1" "$2" || true
		printf 'copied %s (%s lines)\n' "$2" "$(wc -l <"$2" | tr -d ' ')"
	else
		printf 'W: no %s to copy\n' "$1" >&2
	fi
}
copy_mock_logs() {
	copy_mock_log "$resultdir/build.log" "$logdir/mock-build.log"
	copy_mock_log "$resultdir/root.log" "$logdir/mock-root.log"
}
trap copy_mock_logs EXIT

note "build toolchain"
# container-mock.sh:122-123.
dnf -y -q install epel-release >/dev/null
dnf -y -q install mock mock-core-configs rpm-build createrepo_c >/dev/null
rpm -q mock mock-core-configs createrepo_c

note "an unprivileged user for mock"
# container-mock.sh:126-148.
# The BIND MOUNT, not a subdirectory. container-mock.sh stats /out because /out
# is its mount point; the equivalent here is /lane. Statting $out would ask who
# owns a directory this script may have just created itself as root, which is
# an artefact of this script rather than a fact about the caller.
#
# What mock actually needs is A non-root uid that can write the results back.
# On a Linux runner the bind mount carries the caller's uid, so that is the
# right one to use and the results land owned by the account that started the
# job -- container-mock.sh's reasoning exactly. On a macOS Docker host the
# file-sharing layer reports the mount as root-owned whatever the host
# ownership is, and maps writes back to the host user regardless of the
# in-container uid, so any unprivileged uid serves. Taking the mount owner
# where it is meaningful and falling back where it is not keeps the CI
# guarantee intact and makes the lane debuggable locally, which is where four
# of this wave's defects should have been found.
build_uid=$(stat -c %u "$lane")
build_gid=$(stat -c %g "$lane")
if [ "$build_uid" -eq 0 ]; then
	printf 'W: %s reports root ownership; this is a non-Linux bind mount.\n' "$lane" >&2
	printf 'W: using uid/gid 1000 so mock has an unprivileged account to drop to.\n' >&2
	build_uid=1000
	build_gid=1000
fi
getent group "$build_gid" >/dev/null || groupadd -g "$build_gid" mockbuild
getent passwd "$build_uid" >/dev/null ||
	useradd -o -u "$build_uid" -g "$build_gid" -m -d /home/mockbuild mockbuild
build_user=$(getent passwd "$build_uid" | cut -d: -f1)
usermod -aG mock "$build_user"
chown -R "$build_uid:$build_gid" "$resultdir" "$topdir" "$out" "$logdir"

# Every mock call goes through this. A bare `mock` below is a bug.
mock_as() { runuser -u "$build_user" -- mock "$@"; }
printf 'mock runs as %s (uid %s, groups: %s)\n' \
	"$build_user" "$build_uid" "$(runuser -u "$build_user" -- id -nG)"
mock_as --version

note "publish the verified engine packages as a local repository"
# container-mock.sh:276-293. --addrepo, not --install: --no-clean does not
# preserve installed packages across a --rebuild's chroot init.
rm -rf "$localrepo"
mkdir -p "$localrepo"
find "$lane/engine" -maxdepth 1 -name '*.rpm' ! -name '*.src.rpm' \
	-exec cp -p {} "$localrepo/" \;
[ -n "$(ls -A "$localrepo")" ] ||
	die "no engine RPMs in $lane/engine; the engine artifact was not delivered"
createrepo_c --quiet "$localrepo"
chown -R "$build_uid:$build_gid" "$localrepo"
ls -1 "$localrepo"

spec=$lane/recipe/$VMOD_RPM_NAME.spec
[ -f "$spec" ] || die "no generated spec at $spec"
install -m 0644 "$spec" "$topdir/SPECS/"

note "an unsubstituted token must never reach rpmbuild"
if grep -n '@[A-Z][A-Z0-9_]\{1,\}@' "$topdir/SPECS/$VMOD_RPM_NAME.spec"; then
	die "an unsubstituted token is present in the generated spec"
fi

note "stage the verified upstream archive"
archive=$(find "$lane/src" -maxdepth 1 -name '*.tar.gz' | sort | head -1)
[ -n "$archive" ] || die "no verified source archive in $lane/src"
install -m 0644 "$archive" "$topdir/SOURCES/"
chown -R "$build_uid:$build_gid" "$topdir"

note "derived Mock configuration"
# container-mock.sh:152-171. The root name is the STOCK config's, so every
# invocation shares one --no-clean root regardless of which config it names.
cfg=$topdir/mock-$VMOD_RPM_NAME.cfg
cat >"$cfg" <<EOF
include('/etc/mock/$mock_cfg.cfg')
config_opts['root'] = '$mock_cfg'
config_opts['environment']['SOURCE_DATE_EPOCH'] = '$VMOD_SOURCE_DATE_EPOCH'
EOF
chmod 0644 "$cfg"
cat "$cfg"

# container-mock.sh:211-222, passed on every invocation below.
epoch_defines=(--define "use_source_date_epoch_as_buildtime 1"
	--define "clamp_mtime_to_source_date_epoch 1")

note "Mock: initialise the $mock_cfg root"
mock_as -r "$mock_cfg" --init

srpm_name=$VMOD_RPM_NAME-$VMOD_UPSTREAM_VERSION-$VMOD_RPM_RELEASE.src.rpm

note "Mock: source RPM"
# The export covers anything running outside the chroot; the chroot itself
# takes the value from the derived config above.
export SOURCE_DATE_EPOCH=$VMOD_SOURCE_DATE_EPOCH
mock_as -r "$cfg" --no-clean "${epoch_defines[@]}" \
	--addrepo="file://$localrepo" \
	--resultdir="$resultdir" \
	--buildsrpm --spec "$topdir/SPECS/$VMOD_RPM_NAME.spec" \
	--sources "$topdir/SOURCES" 2>&1 | tee "$logdir/mock-srpm.log"

srpm=$resultdir/$srpm_name
[ -f "$srpm" ] || die "Mock produced no $srpm_name"

note "Mock: rebuild the source RPM in a fresh chroot"
mock_as -r "$cfg" --no-clean "${epoch_defines[@]}" \
	--addrepo="file://$localrepo" \
	--resultdir="$resultdir" \
	--rebuild "$srpm" 2>&1 | tee "$logdir/mock-rebuild.log"

note "record the buildroot package set"
# The registry's per-VMOD build.build_dependencies has no other honest source
# on this lane. Debian's equivalent falls out of dpkg for free -- the
# .buildinfo's Installed-Build-Depends is dpkg's own record of the chroot -- but
# Mock resolves the buildroot itself and writes no such list, and root.log
# records only the packages each transaction ADDED, not what the build finally
# saw. So the chroot is asked directly, after the build, which is the same thing
# recipes/el9/container/build.sh:76-77 does with `dnf repoquery --installed` on
# its own lane.
#
# Not fatal if it fails: the packages are already built and copied below, and a
# row that produced a good package must not be failed by a bookkeeping step.
if mock_as -r "$cfg" --no-clean --quiet \
	--chroot -- rpm -qa --qf '%{NAME}\t%{VERSION}-%{RELEASE}.%{ARCH}\n' \
	>"$logdir/buildroot-packages.tsv" 2>"$logdir/buildroot-packages.err"; then
	sort -o "$logdir/buildroot-packages.tsv" "$logdir/buildroot-packages.tsv"
	printf 'buildroot: %s packages\n' \
		"$(wc -l <"$logdir/buildroot-packages.tsv" | tr -d ' ')"
else
	printf 'W: could not query the buildroot package set; see buildroot-packages.err\n' >&2
fi

find "$resultdir" -maxdepth 1 -name '*.rpm' -exec cp -p {} "$out/" \;
chown -R "$build_uid:$build_gid" "$out" "$logdir"

note "EL9 VMOD lane complete"
ls -la "$out"
