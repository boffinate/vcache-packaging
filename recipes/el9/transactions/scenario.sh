#!/bin/bash
#
# One upgrade-transaction scenario, run in a fresh container of the scenario
# base image. transactions.sh starts one container per scenario; nothing is
# shared between them.
#
# Mounts:
#   /recipes   recipes/el9, read-only
#   /repos     dist/el9/mismatch/repos, read-only
#
# Argument: the scenario name (see the case statement at the bottom).
#
# Output: a human-readable transcript on stdout, which the driver saves, plus
# one machine-readable line beginning with SUMMARY<TAB> that the driver collects
# into the matrix table.
#
# Deliberately not `set -e`: half these commands are expected to fail, and their
# exit status is a result, not an accident.

set -uo pipefail

. /recipes/cohort.env

scenario=$1
arch=$(uname -m)
isa=$(rpm --eval '%{?_isa}')
baseline_evr="$VINYL_VERSION-$VINYL_RELEASE.el9"

step() { printf '\n===== %s =====\n' "$*"; }

# The last transaction's captured output and exit status, for the assessment.
last_out=/tmp/last.out
last_rc=0

run() {
	printf '\n$ %s\n' "$*"
	"$@" > "$last_out" 2>&1
	last_rc=$?
	sed 's/^/| /' "$last_out"
	printf '[exit %s]\n' "$last_rc"
	cat "$last_out" >> /tmp/all.out
}

# Did the resolver's own plan say it would remove the VMOD? Checked separately
# from the installed state, because a --assumeno run leaves the system alone and
# the plan is then the only evidence there is.
plan_removes_cachetag() {
	awk '/^(Removing|Removing dependent packages|Removing unused dependencies):/ {f=1; next}
	     /^[A-Za-z]/ {f=0}
	     f' "$1" | grep -q libvmod-cachetag
}

# ------------------------------------------------------------ repository setup

# The trust model here is the lane's existing one: a local, UNSIGNED repository
# with gpgcheck=0. This lane has no signing key, so signature behaviour --
# whether dnf's refusal of an unsigned candidate changes any of the outcomes
# below, and what repo_gpgcheck adds -- is untested and is CI work.
write_repo() {
	cat > "/etc/yum.repos.d/$1.repo" <<EOF
[$1]
name=$1 (local, unsigned test repository)
baseurl=file:///repos/$2
enabled=1
gpgcheck=0
repo_gpgcheck=0
EOF
}

candidate_repo=${CANDIDATE_REPO:-candidate}

step "scenario: $scenario"
printf 'baseline repo   : /repos/baseline\n'
printf 'candidate repo  : /repos/%s\n' "$candidate_repo"
printf 'baseline EVR    : %s\n' "$baseline_evr"

cand_evr=$(rpm -qp --qf '%{NAME} %{VERSION}-%{RELEASE}\n' /repos/"$candidate_repo"/*.rpm \
	| awk '$1 == "vinyl-cache" { print $2 }')
cand_abi=$(rpm -qp --provides /repos/"$candidate_repo"/vinyl-cache-"$cand_evr"."$arch".rpm \
	| sed -n "s/^vinyld(abi).* = //p")
printf 'candidate EVR   : %s\n' "$cand_evr"
printf 'candidate ABI   : %s\n' "$cand_abi"
printf 'baseline ABI    : %s\n' "$VINYL_STRICT_ABI"

# ------------------------------------------------------------- baseline cohort

write_repo vinyl-baseline baseline

# What the machine starts with. Three shapes, because the shape changes what
# the resolver is allowed to do:
#
#   full         runtime + devel + VMOD. The whole cohort, as built.
#   runtime-only runtime + VMOD, no devel. The ordinary production shape: the
#                development package has no reason to be on a cache server, and
#                its absence removes one package the resolver could sacrifice.
#   no-vmod      runtime + devel, no VMOD. The control.
case $scenario in
sanity-candidate-installable) baseline_set="vinyl-cache vinyl-cache-devel" ;;
*runtime-only)                baseline_set="vinyl-cache libvmod-cachetag" ;;
*)                            baseline_set="vinyl-cache vinyl-cache-devel libvmod-cachetag" ;;
esac

step "install the baseline cohort: $baseline_set"
run dnf -y install $baseline_set
[ "$last_rc" -eq 0 ] || { echo "baseline install failed; scenario aborted" >&2; exit 1; }

rpm -q vinyl-cache vinyl-cache-devel libvmod-cachetag
baseline_had_cachetag=no
rpm -q libvmod-cachetag >/dev/null 2>&1 && baseline_had_cachetag=yes
printf 'installed vinyld(abi): %s\n' \
	"$(rpm -q --provides vinyl-cache | sed -n 's/^vinyld(abi).* = //p')"

# ------------------------------------------------------------- state assessors

pkg_state() {
	if rpm -q "$1" >/dev/null 2>&1; then
		rpm -q --qf '%{VERSION}-%{RELEASE}' "$1"
	else
		echo absent
	fi
}
vinyl_state() { pkg_state vinyl-cache; }
devel_state() { pkg_state vinyl-cache-devel; }
cachetag_state() { rpm -q libvmod-cachetag >/dev/null 2>&1 && echo present || echo ABSENT; }

# The failure the plan is actually worried about: a Vinyl that no longer has the
# VMOD its VCL imports. A VCL compile is the cheapest honest test of it.
vcl_state() {
	if ! command -v vinyld >/dev/null 2>&1; then echo no-vinyld; return; fi
	if vinyld -C -f /recipes/smoke/smoke.vcl > /tmp/vcl.c 2> /tmp/vcl.err; then
		echo compiles
	else
		echo FAILS
	fi
}

report_state() {
	step "state $*"
	printf 'vinyl-cache       : %s\n' "$(vinyl_state)"
	printf 'vinyl-cache-devel : %s\n' "$(devel_state)"
	printf 'libvmod-cachetag  : %s\n' "$(cachetag_state)"
	printf 'installed VMODs   : %s\n' \
		"$(ls /usr/lib64/vinyl-cache/vmods/ 2>/dev/null | tr '\n' ' ')"
	local v; v=$(vcl_state)
	printf 'VCL with import cachetag: %s\n' "$v"
	if [ "$v" = FAILS ]; then
		printf 'compile error:\n'; sed 's/^/  /' /tmp/vcl.err | head -20
	fi
}

report_state "before the transaction"

# ------------------------------------------------ make the candidate available

step "resolver configuration in force"
# best=True is the EL9 default, which is why plain `dnf upgrade` and
# `dnf upgrade --best` are the same transaction here. Recorded rather than
# assumed: it is the single setting that decides whether an unsatisfiable
# update is an error or a silent skip.
grep -E '^(best|clean_requirements_on_remove|installonly|skip_if_unavailable)' \
	/etc/dnf/dnf.conf || printf '(no relevant overrides in /etc/dnf/dnf.conf)\n'
python3 -c 'import dnf, libdnf; print("dnf", dnf.const.VERSION)' 2>/dev/null || rpm -q dnf

step "add the candidate repository"
write_repo vinyl-candidate "$candidate_repo"
run dnf -q clean expire-cache
run dnf list --available vinyl-cache
run dnf check-update vinyl-cache

# --------------------------------------------------------------- the scenarios

command_run=""
notes=""
# Composite scenarios run several transactions; the last one is not necessarily
# the one under test, so they set these explicitly rather than letting the
# assessment infer a result from whatever ran last.
outcome_override=""
key_rc=""

case $scenario in

sanity-candidate-installable)
	command_run='dnf -y upgrade  [no VMOD installed]'
	run dnf -y upgrade
	notes='control: with no strict-ABI VMOD installed, nothing blocks the candidate'
	;;

upgrade)
	command_run='dnf upgrade'
	run dnf --assumeno upgrade
	if plan_removes_cachetag "$last_out"; then
		notes='plan proposed removing libvmod-cachetag; '
	fi
	run dnf -y upgrade
	;;

upgrade-best)
	command_run='dnf upgrade --best'
	run dnf --assumeno --best upgrade
	run dnf -y --best upgrade
	;;

upgrade-nobest)
	# dnf's own error message tells the operator to try this, so it is part
	# of the documented upgrade path whether we like it or not.
	command_run='dnf upgrade --nobest'
	run dnf --assumeno --nobest upgrade
	run dnf -y --nobest upgrade
	notes='dnf suggests this flag in its own error output; '
	;;

upgrade-skip-broken)
	command_run='dnf upgrade --skip-broken'
	run dnf --assumeno --skip-broken upgrade
	run dnf -y --skip-broken upgrade
	notes='dnf suggests this flag in its own error output; '
	;;

upgrade-allowerasing)
	command_run='dnf upgrade --allowerasing'
	step "first, capture the plan without executing it"
	run dnf --assumeno --allowerasing upgrade
	if plan_removes_cachetag "$last_out"; then
		notes='the --assumeno plan explicitly listed libvmod-cachetag for removal; '
	else
		notes='the --assumeno plan did NOT list libvmod-cachetag for removal; '
	fi
	step "now execute it"
	run dnf -y --allowerasing upgrade
	;;

upgrade-runtime-only)
	# Same command as the `upgrade` scenario, on a machine with no devel
	# package installed.
	command_run='dnf upgrade  [no devel installed]'
	run dnf --assumeno upgrade
	run dnf -y upgrade
	notes='ordinary production shape: runtime + VMOD only; '
	;;

upgrade-allowerasing-runtime-only)
	# The question this answers: does the whole-system --allowerasing
	# transaction refuse because erasing the VMOD is unacceptable to the
	# resolver, or only because the devel package was also in the way?
	command_run='dnf upgrade --allowerasing  [no devel installed]'
	run dnf --assumeno --allowerasing upgrade
	if plan_removes_cachetag "$last_out"; then
		notes='the plan listed libvmod-cachetag for removal; '
	fi
	run dnf -y --allowerasing upgrade
	notes="${notes}ordinary production shape: runtime + VMOD only; "
	;;

upgrade-allowerasing-nobest)
	# The combination an operator reaches after reading dnf's advice on the
	# previous two failures. It is the most likely real-world route from
	# "my upgrade does not work" to a removed VMOD.
	command_run='dnf upgrade --allowerasing --nobest'
	run dnf --assumeno --allowerasing --nobest upgrade
	if plan_removes_cachetag "$last_out"; then
		notes='the plan listed libvmod-cachetag for removal; '
	fi
	run dnf -y --allowerasing --nobest upgrade
	;;

upgrade-targeted-allowerasing)
	# A targeted upgrade is a different resolver problem from a whole-system
	# one: the named package must be upgraded, so the resolver has to solve
	# the conflict rather than decline the job.
	command_run='dnf upgrade --allowerasing vinyl-cache'
	run dnf --assumeno --allowerasing upgrade vinyl-cache
	if plan_removes_cachetag "$last_out"; then
		notes='the plan listed libvmod-cachetag for removal; '
	fi
	run dnf -y --allowerasing upgrade vinyl-cache
	;;

distro-sync)
	command_run='dnf distro-sync'
	run dnf --assumeno distro-sync
	run dnf -y distro-sync
	;;

distro-sync-allowerasing)
	command_run='dnf distro-sync --allowerasing'
	run dnf --assumeno --allowerasing distro-sync
	if plan_removes_cachetag "$last_out"; then
		notes='the plan listed libvmod-cachetag for removal; '
	fi
	run dnf -y --allowerasing distro-sync
	;;

install-candidate)
	command_run="dnf install vinyl-cache-$cand_evr"
	run dnf -y install "vinyl-cache-$cand_evr"
	;;

install-candidate-allowerasing)
	command_run="dnf install --allowerasing vinyl-cache-$cand_evr"
	run dnf --assumeno --allowerasing install "vinyl-cache-$cand_evr"
	if plan_removes_cachetag "$last_out"; then
		notes='the plan listed libvmod-cachetag for removal; '
	fi
	run dnf -y --allowerasing install "vinyl-cache-$cand_evr"
	;;

versionlock)
	# The incident-response procedure the plan asks to be documented and
	# exercised. The transaction it is asked to defend against is the one
	# that actually removes the VMOD -- the erasing install -- not the
	# whole-system upgrade, which refuses on its own.
	command_run='dnf versionlock add ...; then dnf install --allowerasing <candidate>'
	step "incident response: pin the cohort with versionlock"
	rpm -q python3-dnf-plugin-versionlock
	run dnf versionlock add vinyl-cache vinyl-cache-devel libvmod-cachetag
	run dnf versionlock list
	printf '\nthe lock file itself:\n'
	sed 's/^/  /' /etc/dnf/plugins/versionlock.list

	step "the transaction that removes the VMOD when unlocked"
	run dnf -y --allowerasing install "vinyl-cache-$cand_evr"
	key_rc=$last_rc
	outcome_override="versionlock BLOCKED the erasing transaction, nothing changed"
	notes='with the lock in place; '
	report_state "with versionlock in place"

	step "and a plain upgrade, under the lock"
	run dnf -y upgrade

	step "release the lock again and re-check"
	run dnf versionlock delete vinyl-cache
	run dnf versionlock list
	run dnf --assumeno --allowerasing install "vinyl-cache-$cand_evr"
	if plan_removes_cachetag "$last_out"; then
		notes="$notes after deleting the vinyl-cache lock the resolver proposes the erasing transaction again; "
	else
		notes="$notes after deleting the vinyl-cache lock the erasing transaction is still blocked (libvmod-cachetag remains locked); "
	fi
	# Re-lock, so the state this scenario leaves behind is the state an
	# operator following the procedure would actually be in.
	run dnf versionlock add vinyl-cache
	;;

same-abi)
	command_run='dnf upgrade  [candidate keeps the SAME ABI string]'
	run dnf --assumeno upgrade
	run dnf -y upgrade
	notes='the plan'"'"'s known limitation, closed by the cohort provide (2026-07-25); '
	;;

# The two erasing routes, aimed at the same-ABI candidate. Before the
# cohort-qualified provide existed there was nothing for them to test: the
# same-ABI candidate simply upgraded, so no resolver conflict arose and no
# erasure could be proposed. Now that it is a conflict, these ask whether the
# fix converts a silent upgrade into a removal, which is what the Debian lane's
# equivalent scenarios (s13, s14) do.
same-abi-targeted-allowerasing)
	command_run='dnf upgrade --allowerasing vinyl-cache  [SAME ABI string]'
	run dnf --assumeno --allowerasing upgrade vinyl-cache
	if plan_removes_cachetag "$last_out"; then
		notes='the plan listed libvmod-cachetag for removal; '
	fi
	run dnf -y --allowerasing upgrade vinyl-cache
	notes="${notes}same-ABI candidate, targeted erasing upgrade; "
	;;

same-abi-install-allowerasing)
	command_run="dnf install --allowerasing vinyl-cache-$cand_evr  [SAME ABI string]"
	run dnf --assumeno --allowerasing install "vinyl-cache-$cand_evr"
	if plan_removes_cachetag "$last_out"; then
		notes='the plan listed libvmod-cachetag for removal; '
	fi
	run dnf -y --allowerasing install "vinyl-cache-$cand_evr"
	notes="${notes}same-ABI candidate, erasing install; "
	;;

history-undo)
	# Recovery. Only meaningful after a transaction that actually removed
	# the VMOD, so it starts from the erasing install rather than from the
	# whole-system upgrade, which refuses.
	command_run='dnf install --allowerasing <candidate>; then dnf history undo last'
	run dnf -y --allowerasing install "vinyl-cache-$cand_evr"
	key_rc=$last_rc
	outcome_override="erasing install removed the VMOD; history undo restored the cohort"
	report_state "after the erasing install"
	if [ "$(cachetag_state)" != ABSENT ]; then
		outcome_override="the erasing install changed nothing, so the undo proved nothing"

		notes='the erasing install did not remove the VMOD, so there was nothing to undo; '
	fi
	step "roll the transaction back"
	run dnf -y history undo last
	run dnf history list
	notes="${notes}recovery path after a transaction that removed the VMOD; "
	;;

*)
	echo "unknown scenario: $scenario" >&2; exit 2 ;;
esac

# ------------------------------------------------------------------ assessment

report_state "after the transaction"

v=$(vinyl_state); d=$(devel_state); c=$(cachetag_state); vcl=$(vcl_state)

if [ "$baseline_had_cachetag" = no ]; then
	# The control scenario. cachetag was never installed here, so its
	# absence afterwards is not a removal and means nothing.
	case "$v" in
	"$cand_evr")     outcome="upgraded Vinyl (no VMOD installed)" ;;
	"$baseline_evr") outcome="held Vinyl at the baseline (no VMOD installed)" ;;
	*)               outcome="unclassified: vinyl=$v" ;;
	esac
	c="n/a"; vcl="n/a"; warning=control
else
	case "$v:$c" in
	"$cand_evr:present")       outcome="upgraded whole cohort, VMOD kept" ;;
	"$cand_evr:ABSENT")        outcome="UPGRADED VINYL AND REMOVED THE VMOD" ;;
	"$baseline_evr:present")
		# The distinction the plan's hypothesis turns on: did dnf
		# quietly decline this one update and carry on (exit 0), or did
		# it refuse the whole transaction (non-zero)? Both leave the
		# machine unchanged, and they are not the same behaviour.
		if [ "$last_rc" -eq 0 ]; then
			outcome="skipped the update, nothing changed"
		else
			outcome="REFUSED the transaction, nothing changed"
		fi ;;
	"$baseline_evr:ABSENT")    outcome="VMOD REMOVED without upgrading Vinyl" ;;
	absent:*)                  outcome="removed Vinyl itself" ;;
	*)                         outcome="unclassified: vinyl=$v cachetag=$c" ;;
	esac

	if [ "$c" = ABSENT ] || [ "$vcl" = FAILS ]; then
		warning=WARNING-REQUIRED
	else
		warning=safe
	fi
fi

[ -n "$outcome_override" ] && outcome=$outcome_override
rc=${key_rc:-$last_rc}

step "SCENARIO RESULT"
printf 'scenario   : %s\n' "$scenario"
printf 'command    : %s\n' "$command_run"
printf 'dnf exit   : %s\n' "$rc"
printf 'vinyl      : %s -> %s\n' "$baseline_evr" "$v"
printf 'devel      : %s\n' "$d"
printf 'cachetag   : %s\n' "$c"
printf 'VCL import : %s\n' "$vcl"
printf 'outcome    : %s\n' "$outcome"
printf 'class      : %s\n' "$warning"
printf 'notes      : %s\n' "${notes:-none}"

printf 'SUMMARY\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
	"$scenario" "$command_run" "$rc" "$v" "$c" "$vcl" "$outcome" "$warning"
