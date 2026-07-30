# Release-lane SIGTERM smoke failure: 9.0.1 stops slowly, not never

Date: 2026-07-28

Run investigated: <https://github.com/boffinate/vcache-packaging/actions/runs/30350753493>

## Symptom

Both release-lane package targets failed the same single smoke step, `FAIL: vinyld did not exit on SIGTERM`, with 18 of 19 checks passing. The step timings carry the signature: the failing release smoke steps ran 63s (Debian) and 50s (EL9) against 39s and 30s for the passing trunk lanes — the difference being the exhausted 10s and 20s SIGTERM wait windows. Everything before the stop step behaved identically on both tracks.

## Mechanism

Vinyl 9.0.1 lacks main commit `7de492b0e8` "Shut down pools when stopping" (Nils Goroll, 2026-04-14, upstream #4441). Without it, stopping the child does not shut the worker pools down, so an idle worker sits in `Pool_Work_Thread`'s hardcoded 60-second condition-variable wait holding its cached VCL reference. `VCL_Shutdown` spins on `vcl->busy` with no timeout, so the orderly stop cannot finish until every idle worker wakes and drops its reference — up to a minute after the last request.

Upstream's own test suite never sees this because the worker wait uses a one-second timeout when running under vtc mode; only real deployments and package smoke tests meet the 60-second path. And the stop is slow, not hung: the management process's watchdog (cli_timeout x 10 x 0.1s) sends SIGQUIT at 60 seconds, so the worst case is roughly 61 seconds before the child is gone one way or the other. The smoke scripts' 10s and 20s waits encoded trunk's fast-stop behavior as if it were universal.

The changelog entry for `7de492b0e8` describes exactly this: stopping the cache process now shuts down the thread pools so that a stop no longer has to wait for idle workers. The fix is not on the 9.0 branch: `git log vinyl-cache-9.0.1..origin/9.0` contains only `ed2282fb52` (vtest2 advance) and `7c7336ab80` (the DESTDIR fix), so no 9.0.x release available today contains it. This is the third instance of the fixed-on-main-missing-from-9.0.1 pattern, after the DESTDIR-less state-directory install and the vmod_math VPATH bug.

## Fix chosen

Both smoke vinyld invocations now pass `-p debug=+vclrel` ("Rapid VCL release", `include/tbl/debug_bits.h`, present in both 9.0.1 and trunk): workers release their cached VCL reference after every task, `vcl->busy` is already zero when the stop begins, and SIGTERM completes within the existing wait windows. On the trunk pin, which shuts pools down properly, the flag is no-op-equivalent for the smoke's purposes. The comments mark it removable when the release track reaches a Vinyl containing `7de492b0e8` (9.0.2 if backported), in the same style as the DESTDIR workaround markers.

Rejected alternative: raising the smoke waits to 75 seconds or more. That adds a minute of dead time to every release-lane run and does not even test an orderly stop — at 60 seconds the mgt watchdog SIGQUITs the child, so the "pass" would be the timeout race resolving in the test's favor, not the daemon exiting cleanly on SIGTERM.

## Operational implication

This is worth an upstream backport request independent of our lanes: a real 9.0.1 deployment's `systemctl stop vinyl-cache` takes up to a minute on an idle or lightly loaded instance, for the same reason. Until a release contains `7de492b0e8`, that is inherent to 9.0.1, and the packaged unit inherits it.
