# Shared EL9 Mock build-driver core. Sourced inside a privileged almalinux:9
# container, never executed, and never on the host.
#
# ONE implementation of the Mock clean-room, used by both recipe strategies:
#
#   scripts/ci/el9/container-mock.sh          Vinyl and cachetag
#   scripts/ci/vmod/container/build-rpm.sh    every generated recipe
#
# Both reach it at /ci/lib/mock.sh and /repo/scripts/ci/lib/mock.sh
# respectively; the mount differs, the file does not.
#
# WHY IT IS SHARED, AND WHAT IT COST NOT TO BE. Every non-obvious line below
# was learned once and then rediscovered by failing. Step 6 Wave B's build-rpm.sh
# was written as a deliberate copy of container-mock.sh and still cost two CI
# round trips (B2 and B4) before its header was rewritten to cite the original
# line by line. Three of Wave B's ten defects -- B3, B6, B9 -- were the same
# class in the verify scripts. This file is the answer to that class: the lesson
# exists once, and a lane that does not call it does not get to be wrong about
# it separately.
#
# THE LESSONS, each beside the code that applies it:
#
#   epel-release BEFORE mock            mock is not in AlmaLinux 9's own
#                                       repositories (B2)
#   an unprivileged uid in group mock   mock refuses to run as root, and
#                                       /usr/bin/mock is a symlink to
#                                       usermode's consolehelper, which exits 6
#                                       "Insufficient rights." on a runner
#                                       rather than degrading usefully (B4)
#   config_opts['root'] pinned to the   otherwise each derived config builds its
#   STOCK config's name                 own chroot from scratch and --no-clean
#                                       shares nothing
#   SOURCE_DATE_EPOCH through           a host export does not cross into the
#   config_opts['environment']          chroot; EL9's redhat-rpm-config then
#                                       derives the epoch from the newest
#                                       %changelog entry truncated to midnight
#   both epoch macros as --define on    EL9's rpm 4.16 ships
#   EVERY invocation                    %use_source_date_epoch_as_buildtime
#                                       defaulting to 0, so without them
#                                       BUILDTIME is the wall clock and payload
#                                       mtimes are unclamped. Setting them only
#                                       as config macros is not the
#                                       measured-good form
#   --addrepo, NOT `mock --install`     every mock --rebuild begins with a chroot
#   plus --no-clean                     init that restores the root cache and
#                                       discards whatever --install left,
#                                       measured in run 30167536066. mock's dnf
#                                       runs outside the chroot, so a file://
#                                       URL resolves
#   an EXIT trap copying build.log      under `set -e` a failing build kills the
#   and root.log                        script before any success-path copy
#                                       runs, which is what made run
#                                       30344401137's EL9 failures
#                                       undiagnosable. build.log is also the
#                                       only record of what %configure expanded
#                                       to and which CFLAGS/LDFLAGS reached the
#                                       compiler -- the registry's
#                                       build.configure_options, build.cflags
#                                       and build.ldflags have no other honest
#                                       source on this lane, and neither does
#                                       the hardening flag assertion

# shellcheck shell=bash

###############################################################################
# Log capture
###############################################################################

# Flat array of (RESULTDIR, BUILD_DEST, ROOT_DEST) triples. An array rather
# than a delimited string so no path can be mistaken for a separator.
MOCK_LOG_WATCHES=()

# mock_watch_logs RESULTDIR BUILD_DEST ROOT_DEST
#
# Register one Mock result directory whose build.log and root.log are to be
# copied out when this script exits, however it exits. Destinations are named
# explicitly rather than derived, because they are read by name downstream: the
# hardening flag assertion, and the artifact upload globs.
mock_watch_logs() {
	MOCK_LOG_WATCHES+=("$1" "$2" "$3")
}

mock_copy_log() { # SRC DEST
	if [ -f "$1" ]; then
		cp -p "$1" "$2" || true
		printf 'copied %s (%s lines)\n' "$2" "$(wc -l <"$2" | tr -d ' ')"
	else
		printf 'W: no %s to copy\n' "$1" >&2
	fi
}

# Logs that do not exist yet -- a failure before or between builds -- are
# tolerated and warned about, never fatal: this runs from an EXIT trap and must
# not turn a diagnosable failure into an undiagnosable one.
mock_capture_logs() {
	_mock_i=0
	while [ "$_mock_i" -lt "${#MOCK_LOG_WATCHES[@]}" ]; do
		mock_copy_log "${MOCK_LOG_WATCHES[$_mock_i]}/build.log" \
			"${MOCK_LOG_WATCHES[$((_mock_i + 1))]}"
		mock_copy_log "${MOCK_LOG_WATCHES[$_mock_i]}/root.log" \
			"${MOCK_LOG_WATCHES[$((_mock_i + 2))]}"
		_mock_i=$((_mock_i + 3))
	done
}

# mock_install_log_trap
#
# Called before anything can fail, which is the whole point of it.
mock_install_log_trap() {
	trap mock_capture_logs EXIT
}

###############################################################################
# Toolchain
###############################################################################

# mock_install_toolchain [extra package...]
#
# epel-release first and on its own: mock and mock-core-configs are in EPEL, not
# in AlmaLinux 9, so installing them in the same transaction as the repository
# that carries them cannot work. createrepo_c is here rather than at the point
# of use because both lanes need it and a package installed in the container is
# not part of any buildroot.
mock_install_toolchain() {
	dnf -y -q install epel-release >/dev/null
	dnf -y -q install mock mock-core-configs rpm-build createrepo_c "$@" >/dev/null
	rpm -q mock mock-core-configs createrepo_c
}

###############################################################################
# The unprivileged build user
###############################################################################

# mock_setup_build_user MOUNTPOINT [DIR-TO-CHOWN...]
#
# Defines mock_as() and exports MOCK_BUILD_UID / MOCK_BUILD_GID /
# MOCK_BUILD_USER. Every mock call goes through mock_as; a bare `mock` in a
# caller is a bug.
#
# MOUNTPOINT is the BIND MOUNT, not a subdirectory of it. What mock actually
# needs is a non-root uid that can write the results back. On a Linux runner the
# bind mount carries the caller's uid, so that is the right one to use and the
# results land owned by the account that started the job. Statting a
# subdirectory this script may have created itself as root would ask a question
# about this script instead of about the caller.
#
# On a macOS Docker host the file-sharing layer reports the mount as root-owned
# whatever the host ownership is, and maps writes back to the host user
# regardless of the in-container uid, so any unprivileged uid serves. Taking the
# mount owner where it is meaningful and falling back where it is not keeps the
# CI guarantee intact and makes the lane debuggable locally -- which is where
# four of Wave B's defects should have been found. Before Step 7 Wave 0 the
# cachetag lane died here instead; on a Linux runner the branch is unreachable,
# so adopting the fallback changes nothing CI does.
#
# And because "unreachable in CI" is an assumption rather than a mechanism, the
# fallback is FATAL when CI=true. Both host drivers forward CI into the
# container for that reason; without the forward the guard would be decorative,
# since docker does not inherit the runner's environment.
mock_setup_build_user() {
	_mock_mount=$1
	shift

	MOCK_BUILD_UID=$(stat -c %u "$_mock_mount")
	MOCK_BUILD_GID=$(stat -c %g "$_mock_mount")
	if [ "$MOCK_BUILD_UID" -eq 0 ]; then
		# The fallback is a local-development affordance and nothing else. On a
		# Linux runner the bind mount carries the caller's uid, so a root-owned
		# mount there means something about the job changed -- and silently
		# building as uid 1000 instead would leave the results owned by an
		# account the job does not have, with only a warning nobody reads. In
		# CI it is fatal; elsewhere it is the warning.
		if [ "${CI:-}" = "true" ]; then
			printf 'E: %s is owned by root, in CI.\n' "$_mock_mount" >&2
			printf 'E: mock cannot run as root and the uid-1000 fallback is a local-host affordance, not a CI path.\n' >&2
			return 1
		fi
		printf 'W: %s reports root ownership; this is a non-Linux bind mount.\n' "$_mock_mount" >&2
		printf 'W: using uid/gid 1000 so mock has an unprivileged account to drop to.\n' >&2
		MOCK_BUILD_UID=1000
		MOCK_BUILD_GID=1000
	fi
	getent group "$MOCK_BUILD_GID" >/dev/null || groupadd -g "$MOCK_BUILD_GID" mockbuild
	getent passwd "$MOCK_BUILD_UID" >/dev/null ||
		useradd -o -u "$MOCK_BUILD_UID" -g "$MOCK_BUILD_GID" -m -d /home/mockbuild mockbuild
	MOCK_BUILD_USER=$(getent passwd "$MOCK_BUILD_UID" | cut -d: -f1)
	usermod -aG mock "$MOCK_BUILD_USER"
	[ $# -eq 0 ] || chown -R "$MOCK_BUILD_UID:$MOCK_BUILD_GID" "$@"

	printf 'mock runs as %s (uid %s, groups: %s)\n' \
		"$MOCK_BUILD_USER" "$MOCK_BUILD_UID" \
		"$(runuser -u "$MOCK_BUILD_USER" -- id -nG)"
	mock_as --version
}

mock_as() { runuser -u "$MOCK_BUILD_USER" -- mock "$@"; }

###############################################################################
# Configuration
###############################################################################

# The two epoch macros, passed unchanged to every mock invocation in both lanes.
# _buildhost is deliberately not pinned: whole-RPM reproducibility is not this
# lane's contract, and the Step 4 report measured BUILDHOST as the only
# difference between two otherwise identical builds.
# shellcheck disable=SC2034 # read by the sourcing lane scripts, not by this file
mock_epoch_defines=(--define "use_source_date_epoch_as_buildtime 1"
	--define "clamp_mtime_to_source_date_epoch 1")

# mock_derived_config PATH STOCK_CFG EPOCH
#
# Writes a config that includes the stock one and forwards this package's epoch
# into the chroot environment, then prints the path. The root name is pinned to
# the stock config's, so every invocation keeps sharing the one --no-clean root
# regardless of which config file it names.
mock_derived_config() {
	cat >"$1" <<EOF
include('/etc/mock/$2.cfg')
config_opts['root'] = '$2'
config_opts['environment']['SOURCE_DATE_EPOCH'] = '$3'
EOF
	chmod 0644 "$1"
	cat "$1"
}

###############################################################################
# The local repository the exact-version engine dependency resolves from
###############################################################################

# mock_publish_localrepo REPODIR SRCDIR NAMEGLOB
#
# Every VMOD this project packages BuildRequires vinyl-cache-devel at an exact
# EVR, which is on no mirror. --addrepo is the documented mechanism for
# "build against packages I just built"; see this file's header for why
# `mock --install` plus --no-clean is not.
mock_publish_localrepo() {
	_mock_repo=$1
	_mock_src=$2
	_mock_glob=$3

	rm -rf "$_mock_repo"
	mkdir -p "$_mock_repo"
	find "$_mock_src" -maxdepth 1 -name "$_mock_glob" ! -name '*.src.rpm' \
		-exec cp -p {} "$_mock_repo/" \;
	[ -n "$(ls -A "$_mock_repo")" ] || {
		printf 'E: no RPMs matching %s in %s; the engine artifact was not delivered\n' \
			"$_mock_glob" "$_mock_src" >&2
		return 1
	}
	createrepo_c "$_mock_repo"
	chown -R "$MOCK_BUILD_UID:$MOCK_BUILD_GID" "$_mock_repo"
	ls -1 "$_mock_repo"
}
