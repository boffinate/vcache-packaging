#!/bin/bash
#
# lintian over every produced package. Findings are reported, never suppressed
# wholesale: the only overrides are the four checked into
# debian/vinyl-cache.lintian-overrides, each with a written justification.
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C

apt-get update -qq
apt-get install -y --no-install-recommends lintian >/dev/null

echo "===== lintian version ====="
lintian --version

rc=0
for changes in /out/*.changes; do
	[ -e "$changes" ] || continue
	echo
	echo "================================================================"
	echo "lintian: $(basename "$changes")"
	echo "================================================================"
	# -i explains each tag, -I adds info-level tags, --pedantic adds the
	# strictest advisory tags. Nothing is filtered out.
	lintian -i -I --pedantic --no-tag-display-limit "$changes" || rc=$?
done

echo
echo "===== raw tag summary (no explanations, for the triage table) ====="
for changes in /out/*.changes; do
	[ -e "$changes" ] || continue
	lintian -I --pedantic --no-tag-display-limit "$changes" 2>&1 || true
done | sort | tee /out/logs/lintian-tags.txt

echo
echo "lintian exit status: $rc (0 = no error-level tag)"
echo "===== stage-lint complete ====="
exit 0
