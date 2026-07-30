# Step 1 bring-up: two root causes behind the first all-red package run

Date: 2026-07-28

Runs investigated: release lanes <https://github.com/boffinate/vcache-packaging/actions/runs/30344401137>, re-pinned nightly <https://github.com/boffinate/vcache-packaging/actions/runs/30344422290>

## Symptom

The first execution of the release lanes and the first nightly on the re-pinned cachetag v1.0.0 source turned all four package lanes red — Debian and EL9, both tracks in the runs that build both. Everything ahead of the package builds was green: the registry selftest, the new cachetag tag verification, and the source-archive jobs all passed. The roadmap's step 1 predicted exactly this shape of failure: the release-tarball path had never executed, so its failures are bring-up path defects, not regressions.

Two independent root causes account for every red lane.

## Root cause 1: the 9.0.1 tarball's DESTDIR-less state-directory install, plus a latent Debian statedir varnish-ism

The upstream vinyl-cache 9.0.1 release tarball predates upstream commit `4196617a18` "Fix DESTDIR builds". Its `Makefile.am` install-data-local rule is `$(install_sh) -d -m 0755 "${VINYL_STATE_DIR}"` with no `$(DESTDIR)`, so any unprivileged `make install DESTDIR=...` tries to mkdir the real `/var` path and dies. In pbuilder that surfaced as `mkdir: cannot create directory '/var/lib/lib': Permission denied`; in Mock the same rule killed `%make_install` for `vinyl-cache-9.0.1-1.el9.src.rpm`. The 9.0 branch received the fix as `7c7336ab80` only after the 9.0.1 tag, and the trunk pin `25761f850` contains it, which is why the trunk lanes never hit this.

The Debian error message also exposed a latent bug of its own: `/var/lib/lib`. Vinyl's `configure.ac` defaults `VINYL_STATE_DIR` to `${localstatedir}/lib/vinyl-cache`, so the `--localstatedir=/var/lib` inherited from the older packaging layout — a varnish-ism the EL9 spec already documents as a trap — compiled `/var/lib/lib/vinyl-cache` into every Debian build to date, while `vinyl-cache.dirs`, `vinyl-cache.tmpfiles`, and `postinst` all provision `/var/lib/vinyl-cache`.

Both are fixed recipe-side, with no re-pin: the Debian rules now pass `--localstatedir=/var`, and both lanes append a deliberately relative `VINYL_STATE_DIR=var/lib/vinyl-cache` make override to the install step. On the DESTDIR-fixed trunk the override is a no-op relocation to the identical `$(DESTDIR)/var/lib/vinyl-cache`; on the 9.0.1 tarball the directory lands harmlessly inside the build tree, and the packaged directory comes from `vinyl-cache.dirs` and the spec's `%files` `%dir` entry as before. The override is removable when the release track moves to 9.0.2, which contains the fix.

## Root cause 2: the cachetag v1.0.0 archive cannot build its own packages

The cachetag public-release history rewrite that forced the v1.0.0 re-pin also dropped `docs/vmod_cachetag.rst` from `EXTRA_DIST`, so the file is absent from the v1.0.0 dist archive. But `packaging/README.md:131` documents it as present in the release tarball, and both packaging recipes reference it: the Debian recipe in `libvmod-cachetag.docs` and the RPM spec as `%doc docs/vmod_cachetag.rst`. The v1.0.0 archive can therefore never build the packages that its own packaging recipes describe.

This is not fixable from this repository. The fix is a cachetag source change and a new release (1.0.1), followed by a full re-pin here — tag, peeled commit, archive digest, source-date epoch, and evidence — pending a maintainer decision in the sibling repository. The uncomfortable but accurate status: the v1.0.0 pin that just landed is proven unbuildable, while the identity-verification machinery around it worked exactly as designed — the tag verification, commit assertion, and digest gate all passed, because the source really is what was pinned; it is the pinned source itself that is defective.

## Observability defect found on the way: Mock logs vanished on failure

Diagnosing the EL9 lanes was needlessly hard because `container-mock.sh` copied Mock's `build.log` into the uploaded log directory only on the success path, after both builds. Under `set -euo pipefail` a failed mock build kills the script before the copy, so the job uploaded no `build.log` at all, and the tee'd Mock stdout is a progress summary that contains none of the rpmbuild output. This run's EL9 failures were only diagnosable because the Debian lanes hit the same DESTDIR wall and printed the error. The copy is now an EXIT trap that runs on success and failure alike, captures `root.log` beside `build.log`, and tolerates logs that do not exist yet; the success-path copy is gone so there is exactly one mechanism.
