#!/usr/bin/env bash
# scripts/check-package-version-ordering.sh
#
# Prove the published VMOD version scheme uses the native package-manager
# comparators. This is deliberately container-only: host-safe selftests cover
# rendering, while Debian and RPM retain authority over their ordering rules.
set -euo pipefail

# Synthetic comparator fixtures, deliberately unrelated to catalog pins.
docker run --rm debian:13 bash -ec '
  dpkg --compare-versions "6.5-1~example42.3.7.1" lt "6.5-1~example42.3.7.2"
  dpkg --compare-versions "6.5-1~example42.3.7.2" lt "6.5-1~example42.3.8.1"
'

docker run --rm almalinux:10 bash -ec '
  test "$(rpm --eval '\''%[v"6.5-1.example42.3.7.1.el10" < v"6.5-1.example42.3.7.2.el10" ? 1 : 0]'\'')" = 1
  test "$(rpm --eval '\''%[v"6.5-1.example42.3.7.2.el10" < v"6.5-1.example42.3.8.1.el10" ? 1 : 0]'\'')" = 1
'
