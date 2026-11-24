# Disabled VMODs

These complete catalog manifests are parked outside `vmods/`, so they do not enter compatibility, trunk, or package selections. Their upstream is hosted on GNU infrastructure that blocks GitHub Actions and most users; access cannot be restored reliably. The pins remain here for restoration if upstream hosting becomes broadly reachable again. Move each manifest back to `vmods/` and restore its schema modeline to `../schemas/vmod.schema.json` when re-enabling it.

The upstream Varnish overlay proof is suspended because basicauth was its only module. Its workflow is parked at `docs/disabled-vmods/upstream-varnish-overlay.yml`; the probe script exits while `vmods/basicauth.yml` is absent. Restore the basicauth manifest and move the workflow back to `.github/workflows/upstream-varnish-overlay.yml` to re-enable the proof.

Restoring promoted modules changes the published package cohort, so bump every affected engine package revision before publishing.
