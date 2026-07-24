#!/bin/bash
#
# Build the local dnf repositories the transaction matrix resolves against.
# Runs in one container; the scenario containers then mount the result
# read-only.
#
# Mounts:
#   /out   dist/el9, writable
#
# Repositories produced under /out/mismatch/repos:
#   baseline   today's built cohort: vinyl-cache, vinyl-cache-devel,
#              libvmod-cachetag (and the debug packages, which nothing installs)
#   candidate  the ABI-mismatched fixture Vinyl pair, and nothing else
#   sameabi    the same-ABI-string fixture Vinyl pair, and nothing else
#
# The candidate repositories deliberately do NOT carry a rebuilt cachetag. That
# is the incoherent-repository shape the plan is worried about: a Vinyl update
# published without the cohort it belongs to. A cohort-aware promotion would
# publish both together, which is the design conclusion the matrix is meant to
# support or refute -- not an assumption to build into the harness.

set -euo pipefail

src=/out/packages
fix=/out/mismatch/packages
repos=/out/mismatch/repos

test -d "$src"
test -d "$fix"

rm -rf "$repos"
mkdir -p "$repos"/{baseline,candidate,sameabi}

# Source packages are not part of an upgrade transaction; leaving them out keeps
# the repodata about the binary cohort.
for f in "$src"/*.rpm; do
	case $f in *.src.rpm) continue ;; esac
	cp -p "$f" "$repos/baseline/"
done

for f in "$fix"/*mismatchfixture*.rpm; do cp -p "$f" "$repos/candidate/"; done
for f in "$fix"/*sameabifixture*.rpm; do cp -p "$f" "$repos/sameabi/"; done

for r in baseline candidate sameabi; do
	printf '\n===== createrepo_c %s =====\n' "$r"
	ls -1 "$repos/$r"
	createrepo_c --quiet "$repos/$r"
done

printf '\nrepositories ready under %s\n' "$repos"
