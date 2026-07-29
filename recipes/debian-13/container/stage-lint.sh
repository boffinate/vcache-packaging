#!/bin/bash
#
# lintian over every produced package. Findings are reported, never suppressed
# wholesale: the only overrides are the ones checked into the recipe's
# .lintian-overrides files, each with a written justification.
#
# STEP 7 WAVE 0, asymmetry settlement (a). The gating pass runs
# `--fail-on error,warning`, which is what the generated-recipe lane has run
# since Wave A2. Before this, lintian's default `--fail-on error` meant a
# warning nobody had reviewed passed silently on the upstream-recipe VMOD while
# failing the generated one -- the recipe-generation plan's "gates identical in
# strength regardless of recipe strategy" clause violated with the AUDITED
# recipe on the weaker side.
#
# NO OVERRIDES WERE ADDED, because nothing fires. Measured on the green
# baseline 30437775658 before the change: both channels, both `.changes` files,
# zero warning-level and zero error-level tags. What the informational pass
# reports is two `I:` tags and three `P:` tags, and `--fail-on error,warning`
# does not fail on those -- info and pedantic stay visible and non-gating, which
# is what they are for. The `W: wrong-manual-section 3 != 4` recorded in the
# step-2 lint-gate note is gone; cachetag 1.0.1's man page is section 3.
#
# This changes a CHECK and not a package: no file any recipe builds from moves,
# and lintian reads packages that already exist.
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C

apt-get update -qq
apt-get install -y --no-install-recommends lintian >/dev/null

echo "===== lintian version ====="
lintian --version

rc=0
checked=0
for changes in /out/*.changes; do
	[ -e "$changes" ] || continue
	checked=$((checked + 1))
	echo
	echo "================================================================"
	echo "lintian: $(basename "$changes")"
	echo "================================================================"
	# -i explains each tag, -I adds info-level tags, --pedantic adds the
	# strictest advisory tags. Nothing is filtered out. --fail-on selects
	# which of them decide the exit status: errors and warnings gate, info
	# and pedantic are shown and do not.
	lintian -i -I --pedantic --no-tag-display-limit \
		--fail-on error,warning "$changes" || rc=$?
done

echo
echo "===== raw tag summary (no explanations, for the triage table) ====="
for changes in /out/*.changes; do
	[ -e "$changes" ] || continue
	lintian -I --pedantic --no-tag-display-limit "$changes" 2>&1 || true
done | sort | tee /out/logs/lintian-tags.txt

echo
if [ "$checked" -eq 0 ]; then
	echo "E: no .changes files in /out; nothing was linted" >&2
	exit 1
fi
echo "lintian exit status: $rc (0 = no error-level or warning-level tag)"
echo "===== stage-lint complete ====="
# The status gates the lane. Waivers live in the reviewed
# .lintian-overrides files, never here.
exit "$rc"
