#!/bin/sh
#
# EL9 lane driver: build the coordinated Vinyl Cache + cachetag package cohort
# for an EL9-compatible distribution, then prove it with an installed-package
# smoke test in a fresh container.
#
# Everything is built inside almalinux:9 containers. Nothing is installed on the
# host and nothing is built on it; the host contributes only the Docker daemon,
# the pinned source checkouts, and this script.
#
# Usage:
#   build.sh                       deps source vinyl cachetag report lint, then smoke
#   build.sh --stages "vinyl lint" run only those container stages, no smoke
#   build.sh --smoke-only          re-run only the installed-package smoke
#   build.sh --list-files          tolerate unpackaged files and dump the buildroot
#
# Environment:
#   VINYL_CACHE_SRC   pinned Vinyl checkout        (default ../../../vinyl-cache)
#   CACHETAG_SRC      cachetag checkout            (default ../../../libvmod-cachetag)
#   EL9_IMAGE         build image                  (default from cohort.env)

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../.." && pwd)
workspace=$(CDPATH= cd -- "$repo/.." && pwd)

. "$here/cohort.env"

vinyl_src=${VINYL_CACHE_SRC:-$workspace/vinyl-cache}
cachetag_src=${CACHETAG_SRC:-$workspace/libvmod-cachetag}
image=${EL9_IMAGE:-almalinux:9}
out=$repo/dist/el9

stages="deps source vinyl cachetag report lint"
run_smoke=yes
list_files=

while [ $# -gt 0 ]; do
	case $1 in
	# Every container is fresh, so the deps stage is never optional: it is
	# what puts git, gcc and rpmbuild in the container in the first place.
	--stages)   stages="deps $2"; run_smoke=; shift 2 ;;
	--smoke-only) stages=; shift ;;
	--list-files) list_files=1; stages="deps source vinyl"; run_smoke=; shift ;;
	-h|--help)  sed -n '2,25p' "$0"; exit 0 ;;
	*) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
	esac
done

for d in "$vinyl_src" "$cachetag_src"; do
	[ -d "$d" ] || { printf 'missing checkout: %s\n' "$d" >&2; exit 2; }
done

mkdir -p "$out"

printf '\n########## EL9 lane ##########\n'
printf 'image          : %s\n' "$image"
printf 'vinyl source   : %s @ %s\n' "$vinyl_src" "$VINYL_GIT_COMMIT"
printf 'cachetag source: %s (%s)\n' "$cachetag_src" "$CACHETAG_TARBALL"
printf 'output         : %s\n' "$out"
printf 'stages         : %s\n' "${stages:-<none>}"

docker image inspect "$image" >/dev/null 2>&1 || docker pull "$image"
docker image inspect "$image" --format \
	'image id       : {{.Id}}
image digest   : {{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}<none>{{end}}
image arch     : {{.Architecture}}' | tee "$out/image.txt"

if [ -n "$stages" ]; then
	docker run --rm \
		-v "$here:/recipes:ro" \
		-v "$vinyl_src:/vinyl-src:ro" \
		-v "$cachetag_src:/cachetag:ro" \
		-v "$out:/out" \
		-e "VINYL_UNPACKAGED_OK=${list_files:-}" \
		-w /out \
		"$image" \
		bash /recipes/container/build.sh $stages
fi

if [ -n "$run_smoke" ]; then
	printf '\n########## installed-package smoke, fresh container ##########\n'
	# A brand new container: nothing from the build lane is present except the
	# built RPMs themselves, which is the point.
	smoke_status=0
	docker run --rm \
		-v "$here:/recipes:ro" \
		-v "$out:/out:ro" \
		-w /tmp \
		"$image" \
		bash /recipes/smoke/smoke.sh > "$out/logs/smoke.log" 2>&1 ||
		smoke_status=$?
	cat "$out/logs/smoke.log"
	[ "$smoke_status" -eq 0 ] || {
		printf '\nsmoke test FAILED (exit %s)\n' "$smoke_status" >&2
		exit "$smoke_status"
	}
fi

printf '\n########## EL9 lane done ##########\n'
