# Step-10 gate decisions

Date: 2026-07-25

The maintainer resolved the questions that were blocking step 10 (pre-release):

1. **Maintainer identity: `Boffinate <noreply@boffinate.com>`.** The address is on a maintainer-controlled domain but deliberately does not accept mail; the support channel is each package's advertised issue tracker (`Homepage`/`Vcs-*` on Debian, `URL` on RPM). Both lane drivers (`recipes/debian-13/build.sh`, `recipes/el9/cohort.env`) now carry this identity, replacing the deliberately-undeliverable placeholders and their publish-blocking comments.

2. **`SECURITY.md` omitted** from `libvmod-cachetag` for now — no security operating-model document until there is machinery worth documenting. See `../libvmod-cachetag/docs/20260725_1559_note_step-2-identity-decided-security-md-omitted.md`.

3. **EL9 keeps `--with-unwind`.** The EPEL requirement for the runtime is accepted.

4. **Cohort-qualified provide: yes.** Both lanes add a cohort-scoped virtual provide alongside the exact-ABI provides before first publication, as recommended by the step-9 transaction analyses in both lanes.

5. **Repository renamed `vinyl-packaging` → `vcache-packaging`** before first publication, because the repo will eventually cover Varnish Cache module builds as well as Vinyl (cf. the upstream `wip_vcacheapi` work in varnish-modules and vinyl-cache issue 4537). Publishes to `https://github.com/boffinate/vcache-packaging`.

6. **Step 10 proceeds as: CI clean-room builds first** (Debian 13 amd64 sbuild, EL9 x86_64 Mock — laptop-built artifacts are never published), then the draft release, then a real experimental pre-release is allowed, on the understanding that test releases remain deletable.
