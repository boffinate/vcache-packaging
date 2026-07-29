# Generated VMOD recipes

Debian and RPM packaging recipes for selected third-party VMODs are **generated** from the data in this directory by [`tools/vmod_recipe.py`](../../tools/vmod_recipe.py). Nothing under `recipes/vmods/` is a recipe; it is the input a recipe is rendered from.

This exists because most third-party VMODs ship no `debian/` or `rpm/` directory, and that is the normal case rather than a problem: absorbing packaging is the downstream provider's job. The design, and what was and was not adopted from [`xcir/vmod-packager`](https://github.com/xcir/vmod-packager), are in [the recipe-generation plan](../../docs/20260728_0908_plan_vmod-packager-patterns-and-recipe-generation.md).

Cachetag is **not** generated. It keeps its audited, hand-written recipe in its own repository, because that repository is controlled with the package release. Its recipe is this directory's policy reference and regression oracle: where a template here disagrees with `libvmod-cachetag/packaging/`, the difference has to be a deliberate, explained one.

## Layout

```text
recipes/vmods/
  templates/
    debian/          the Debian source recipe, as @TOKEN@ templates
    rpm/             the RPM spec, as a @TOKEN@ template
  licenses/          reviewed debian/copyright License: stanzas, one per short name
  adapters/
    autotools/       the default adapter: what is true of every conventional
                     Autotools VMOD built against an installed engine
  overlays/
    <vmod-id>/
      <vmod-id>.yml  the VMOD's registry manifest (or, before Wave A2, the
                     staged content of registry/vmods/<vmod-id>.yml)
      overlay.yml    the reviewed per-VMOD packaging data
      patches/       reviewed source patches, if the VMOD needs any
      tests/         the ported behaviour suite
```

There is no `custom/` adapter directory yet. The plan reserves one, and adding it before a selected VMOD needs it would be exactly the speculative generalisation the plan tells us not to do.

## Who owns which fact

The split matters, because a fact with two homes eventually has two values.

| Fact | Owner |
| --- | --- |
| which VMODs are in scope | `SCOPE.md` |
| source ref, peeled commit, version, archive digest, lanes | `registry/vmods/<id>.yml` |
| what is true of every VMOD built this way | `adapters/<adapter>/adapter.yml` |
| what is true of this one VMOD | `overlays/<id>/overlay.yml` |
| VRT, strict ABI, cohort id, VMOD directory, engine package versions | `registry/cohorts/` and `registry/targets/` |
| the ABI dependency expressions themselves | [`tools/metadata.py`](../../tools/metadata.py) |
| maintainer identity, Debian changelog suite | the lane pin files, passed on the command line |
| the rendered recipe | nobody: it is an output |

`tools/metadata.py` is deliberately on that list. Generated recipes take their `Depends`/`Requires` from `metadata.abi_expressions`, the same function that generates cachetag's, so there is one implementation of that policy and a generated recipe cannot drift into weakening it.

## Running the generator

```sh
python3 tools/vmod_recipe.py generate \
    --manifest recipes/vmods/overlays/dict/dict.yml \
    --overlay  recipes/vmods/overlays/dict/overlay.yml \
    --cohort   vinyl-9.0.1-ac4f719c16f4 \
    --target   debian-13-amd64 \
    --maintainer "$MAINTAINER_NAME <$MAINTAINER_EMAIL>" \
    --debian-distribution "$DEBIAN_DISTRIBUTION" \
    --out work/vmod-dict/debian-13-amd64

python3 tools/vmod_recipe.py names --manifest ... --target el9-x86_64 ...
python3 tools/vmod_recipe.py model --manifest ... --target el9-x86_64 ...
python3 tools/vmod_recipe.py selftest
```

It is host-safe and standard-library only, because it validates inputs and renders text and does nothing else. It never compiles, installs, inspects a built package, or reads a clock. Every date it emits comes from a recorded source epoch.

The output directory contains the native recipe tree plus `generation-record.json`, which carries the digest of every input — including the generator's own source — and of every rendered file, plus the expected binary and source package names. The generated tree does not have to be committed; the resulting source package must contain exactly these bytes, and the result evidence must record `recipe_sha256`.

## Adding a VMOD

1. Get the `SCOPE.md` decision first. This directory packages selected VMODs; it does not select them.
2. Verify the facts against the upstream source at the exact selected ref. Not against a survey verdict, not against another distribution's package, not against a repository landing page. The licence especially: `vmod-dict` is GPL-3.0-or-later, which nothing outside its `COPYING` says.
3. Write `overlays/<id>/overlay.yml`. Every field is required, and the generator refuses rather than guessing.
4. If its licence has no stanza in `licenses/`, write one. A licence with no reviewed stanza is not packageable, on purpose.
5. Run `tools/vmod_recipe.py selftest`, then generate for both targets and read the output.
6. Build it in the authoritative buildroots. A generated recipe that renders is not a package, and a package that compiles is not yet supportable.

## Adding a capability to the adapter

Only when a selected VMOD proves it needs one, and then only that one. The adapter's knobs today are `bootstrap`, `configure_args`, `build_time_tests` and `parallel_build`, because those are what the first two VMODs actually needed. Adding a knob is a change to a shared contract: bump `adapter.revision`, and confirm that every already-generated VMOD's recipe output is unchanged.

There is no hook mechanism and no shell in any of this data, deliberately. A VMOD that genuinely needs commands runs them from a checked-in adapter script, reviewed like a recipe and named explicitly by that VMOD — never discovered in an upstream checkout.

## Patching a VMOD's source

Some upstreams cannot be built against Vinyl Cache unchanged. `libvmod-redis` is the first: it addresses the engine by its Varnish names throughout its build system, and `m4_ifndef([VARNISH_PREREQ])` fires before anything else runs.

The overlay may therefore declare `patches:`, a list of `{file, sha256}` under `overlays/<id>/patches/`, applied in the order written. They are rendered as `debian/patches/` plus a `series` file for the 3.0 (quilt) source package, and as `PatchN:` with `%autosetup -p1` for the RPM. Both families get the same patches in the same order, from the same declaration.

Three properties make this a bounded exception rather than a hole:

- **Digested.** Generation fails if a declared file is missing, and fails if its bytes no longer hash to the recorded digest. Replacing a reviewed patch with an unreviewed one is not something that can happen quietly; updating the digest is the deliberate act that makes the new content reviewed.
- **Visible.** The patch is both an input and an output of the generation record, so it moves `recipe_sha256`, which is recorded as result evidence. A patch cannot be omitted or substituted without the evidence changing.
- **Per VMOD.** A patch belongs to one overlay and is applied to one upstream. A blanket substitution pass, a shared shim layer, or a patch directory several VMODs draw from stays forbidden — if N VMODs need the same change, that is an adapter decision made once, not N copies of a workaround.

Keep them minimal and say what they do: each patch carries a DEP-3 header describing the change and why it exists downstream rather than upstream. A patch that touches program source rather than the build system is a much larger decision than this mechanism is for.
