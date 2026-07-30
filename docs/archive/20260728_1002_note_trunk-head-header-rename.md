# Trunk-HEAD lane fails on the vinyld internal header rename

Date: 2026-07-28

Run investigated: <https://github.com/boffinate/vcache-packaging/actions/runs/30344460501>

## Symptom

The first scheduled run of `trunk-vmod-ci.yml` (run `30344460501`) failed building libvmod-cachetag against Vinyl trunk HEAD `655c988a2f079ee458bc64f55f4548862946fe3d`. This is the lane's intended early-warning function firing on its first run, not a workflow defect: the workflow, the harness invocation, and the documented script were all correct.

## Cause

Vinyl commit `6d36364cc164cbfeca3102a8345357765026f823` (Nils Goroll, 2026-07-08, "Un-brand the vinyld internal header file", Ref #4537) renamed the installed VMOD-facing header `cache/cache_vinyld.h` to `cache/cache_int.h`. It is a 100% rename plus the matching `nobase_pkginclude_HEADERS` install-list change in `bin/vinyld/Makefile.am`, so the old include path simply no longer exists in the installed prefix.

cachetag includes `cache/cache_vinyld.h` in three translation units — `src/vmod_cachetag.c:19`, `src/vmod_cachetag_index.c:17`, and `src/vmod_cachetag_purgemap.c:17` — and all three fail to compile against any Vinyl at or after that commit.

## Consequences for pinning

The pinned trunk lanes (`VINYL_GIT_COMMIT=25761f850`, 2026-05-20) predate the rename and are unaffected until re-pin. The constraint this note exists to record: the next Vinyl trunk re-pin must not cross `6d36364cc1` unless a cachetag source fix — switching the include, or a configure-time probe that accepts either header — has landed in libvmod-cachetag first. That fix belongs in the libvmod-cachetag repository, not here.

## Cosmetic observation

Unrelated to the failure: the harness's "Neither git nor vmod_vcs_version.txt found" warning comes from the tar copy excluding `.git` and `vmod_vcs_version.txt`. Every harness run prints it, and the VMOD just gets a `NOGIT` version string.
