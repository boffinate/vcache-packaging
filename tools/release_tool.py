#!/usr/bin/env python3
"""Vinyl cohort registry tool.

Subcommands:

  validate      schema-check every manifest under registry/ and cross-check the
                cohort identity, cachetag version, and target wiring
  cohort-id     print the canonical cohort-input blob and the derived cohort id
                for a cohort manifest
  metadata      print generated native package version metadata for one target
  selftest      run the tooling's own tests

Examples:

  python3 tools/release_tool.py validate
  python3 tools/release_tool.py validate --require-releasable
  python3 tools/release_tool.py cohort-id --cohort vinyl-9.0.0-000000000000
  python3 tools/release_tool.py metadata \\
      --cohort vinyl-9.0.0-000000000000 --target debian-13-amd64
  python3 tools/release_tool.py metadata --distro-native debian-13-amd64 --format shell
  python3 tools/release_tool.py selftest

Manifests record cachetag's version, which is cross-checked against AC_INIT in a
libvmod-cachetag checkout. That checkout lives in its own repository: pass
--cachetag-src PATH or set CACHETAG_SRC, or let it default to the sibling
../libvmod-cachetag.

That cross-check is source-coupled: it needs a VMOD's repository to be
reachable. Validation is therefore split. Everything else -- schemas, cohort
identity digests, target wiring, placeholder policy -- is structural and runs
with --no-cachetag-cross-check in the global CI gate, which must not fail
because one VMOD's source is unavailable. The cross-check runs inside the
cachetag CI invocation, after its checkout, via
`tools/ci_matrix.py validate-vmod --source-dir`.

Standard library only. This tool is pure Python and is safe to run on the host;
it never builds or tests any package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import manifest  # noqa: E402
import metadata as metadata_mod  # noqa: E402
import yaml_subset  # noqa: E402


def _cachetag_src(args, repo_root: Path) -> Path:
    """Resolve the cachetag checkout: --cachetag-src, then CACHETAG_SRC, then sibling."""
    explicit = getattr(args, "cachetag_src", None)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return manifest.default_cachetag_src(repo_root)


def _cross_check(args) -> bool:
    """Whether to cross-check cachetag.version against a cachetag checkout.

    The cross-check is the only thing tying a manifest to a real cachetag
    release, so it stays on by default and a missing checkout stays a hard
    error. --no-cachetag-cross-check turns it off for the one caller that must
    not depend on any VMOD's source: the global structural validation gate,
    which validates the registry schemas and identities for every VMOD and
    therefore cannot be allowed to fail because one VMOD's repository is
    unreachable. The check itself does not disappear -- it runs inside the
    cachetag CI invocation, after that VMOD's checkout.
    """
    return not getattr(args, "no_cachetag_cross_check", False)


def _expected_version(args, repo_root: Path):
    if not _cross_check(args):
        return None
    return manifest.configure_ac_version(_cachetag_src(args, repo_root), repo_root=repo_root)


def _cohort_path(root: Path, cohort_id: str) -> Path:
    candidate = Path(cohort_id)
    if candidate.suffix == ".yml" and candidate.exists():
        return candidate
    return root / "registry" / "cohorts" / f"{cohort_id}.yml"


def cmd_validate(args) -> int:
    root = Path(args.repo_root).resolve()
    cross_check = _cross_check(args)
    cachetag_src = _cachetag_src(args, root) if cross_check else None
    checked, errors = manifest.validate_registry_tree(
        repo_root=root,
        only_cohort=args.cohort,
        require_releasable=args.require_releasable,
        cachetag_src=cachetag_src,
        cross_check_cachetag=cross_check,
    )
    for path in checked:
        print(f"checked  {path}")
    if errors:
        print("")
        for error in errors:
            print(f"ERROR    {error}")
        print(f"\n{len(errors)} error(s) in {len(checked)} manifest(s)")
        return 1
    mode = "releasable" if args.require_releasable else "schema"
    if cross_check:
        version = manifest.configure_ac_version(cachetag_src, repo_root=root)
        print(
            f"\nOK: {len(checked)} manifest(s) valid ({mode} mode), "
            f"cachetag version {version} from {cachetag_src}"
        )
    else:
        print(
            f"\nOK: {len(checked)} manifest(s) structurally valid ({mode} mode). "
            "The cachetag configure.ac cross-check was SKIPPED "
            "(--no-cachetag-cross-check); it runs in the cachetag CI invocation "
            "after that VMOD's checkout."
        )
    return 0


def cmd_cohort_id(args) -> int:
    root = Path(args.repo_root).resolve()
    path = _cohort_path(root, args.cohort)
    data = manifest.load_cohort(path)
    errors = manifest.validate_cohort(
        data, str(path), _expected_version(args, root), repo_root=root
    )
    blob = manifest.cohort_input_blob(data)
    print("canonical cohort-input blob:")
    print("---8<---")
    sys.stdout.write(blob.decode("utf-8"))
    print("--->8---")
    print(f"blob sha256      {hashlib.sha256(blob).hexdigest()}")
    print(f"input-id         {manifest.cohort_input_id(data)}")
    print(f"derived cohort   {manifest.cohort_identifier(data)}")
    print(f"recorded cohort  {data['cohort']}")
    print(f"status           {data['status']}")
    if data["status"] == "template":
        print("note             template manifest: the derived id has no release meaning")
    if errors:
        print("")
        for error in errors:
            print(f"ERROR    {error}")
        return 1
    return 0


def cmd_metadata(args) -> int:
    root = Path(args.repo_root).resolve()
    if args.distro_native:
        target_path = root / "registry" / "distro-native" / f"{args.distro_native}.yml"
        target = manifest.load_target(target_path)
        errors = manifest.validate_target(
            target,
            str(target_path),
            distro_native=True,
            expected_version=_expected_version(args, root),
        )
        cohort = None
    else:
        if not args.cohort or not args.target:
            print("error: --cohort and --target are required unless --distro-native is used", file=sys.stderr)
            return 2
        cohort_path = _cohort_path(root, args.cohort)
        cohort = manifest.load_cohort(cohort_path)
        target_path = root / "registry" / "targets" / cohort["cohort"] / f"{args.target}.yml"
        target = manifest.load_target(target_path)
        errors = manifest.validate_cohort(
            cohort, str(cohort_path), _expected_version(args, root), repo_root=root
        )
        errors += manifest.validate_target(
            target,
            str(target_path),
            cohort=cohort,
            cohort_status=cohort["status"],
            repo_root=root,
        )
    if errors:
        for error in errors:
            print(f"ERROR    {error}", file=sys.stderr)
        return 1
    generated = metadata_mod.target_metadata(target, cohort, vmod=args.vmod)
    if target["status"] == "template" and not args.allow_template:
        print(
            "error: {} is a template manifest; its generated metadata is not releasable. "
            "Pass --allow-template to print it anyway.".format(target_path),
            file=sys.stderr,
        )
        return 1
    if args.format == "json":
        print(json.dumps(generated, indent=2, sort_keys=True))
    else:
        sys.stdout.write(metadata_mod.as_shell(generated))
    return 0


def cmd_release_notes(args) -> int:
    # Upstream release-note references, straight from the validated cohort
    # manifest. Two renderings of the same field so generated release content
    # cannot diverge from the machine-readable record: `json` for
    # release-manifest.json, `body` for the release-body text (one
    # "<title>: <url>" line per reference). An absent field renders as an
    # empty array / no output -- deliberately not an error, because a trunk
    # snapshot has no upstream release statement.
    root = Path(args.repo_root).resolve()
    path = _cohort_path(root, args.cohort)
    data = manifest.load_cohort(path)
    errors = manifest.validate_cohort(
        data, str(path), _expected_version(args, root), repo_root=root
    )
    if errors:
        for error in errors:
            print(f"ERROR    {error}", file=sys.stderr)
        return 1
    notes = data["vinyl"].get("release_notes", [])
    if args.format == "json":
        print(json.dumps(notes, indent=2, sort_keys=True))
    else:
        for entry in notes:
            print(f"{entry['title']}: {entry['url']}")
    return 0


def cmd_recorded_evidence(args) -> int:
    """What the registry RECORDS for one cohort and target, per VMOD.

    `metadata` generates: it computes the names and versions a build should
    produce. This reports: it prints the package identity and artifact digests
    already written down as the evidence of record. The two answer opposite
    questions, and the identity cross-check needs the second -- "does what this
    run built match what we published" cannot be asked of a generator.

    Read-only, and deliberately without a comparison of its own. Whoever is
    checking supplies the observed side; this supplies exactly the recorded one,
    so there is one place that knows how the evidence is shaped.
    """
    root = Path(args.repo_root).resolve()
    cohort_path = _cohort_path(root, args.cohort)
    cohort = manifest.load_cohort(cohort_path)
    target_path = root / "registry" / "targets" / cohort["cohort"] / f"{args.target}.yml"
    target = manifest.load_target(target_path)
    errors = manifest.validate_cohort(
        cohort, str(cohort_path), _expected_version(args, root), repo_root=root
    )
    errors += manifest.validate_target(
        target,
        str(target_path),
        cohort=cohort,
        cohort_status=cohort["status"],
        repo_root=root,
    )
    if errors:
        for error in errors:
            print(f"ERROR    {error}", file=sys.stderr)
        return 1

    vmods = {}
    for vmod_id, entry in sorted(target["vmods"].items()):
        if args.vmod and vmod_id != args.vmod:
            continue
        vmods[vmod_id] = {
            "evidence": entry["evidence"],
            "package": {
                "upstream_version": entry["package"]["upstream_version"],
                "revision": entry["package"]["revision"],
                "source_name": entry["package"]["source_name"],
                "binary_name": entry["package"]["binary_name"],
            },
            # Recorded artifacts, keyed by filename. A `pending` entry has an
            # empty list, which is the honest answer and not an error: a build
            # that has not happened recorded no digests.
            "artifacts": {a["filename"]: a["sha256"] for a in entry["artifacts"]},
        }
    if args.vmod and not vmods:
        print(
            f"ERROR    {target_path} records no evidence for VMOD {args.vmod!r}",
            file=sys.stderr,
        )
        return 1

    report = {
        "schema": "cohort-recorded-evidence/v1",
        "cohort": cohort["cohort"],
        "target": target["target"]["id"],
        "package_format": target["target"]["package_format"],
        "status": target["status"],
        "vinyl_packages": dict(target["vinyl_packages"]),
        "vmods": vmods,
    }
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        # `<sha256>  <filename>`: the sha256sum(1) format, so the output can be
        # fed straight to `sha256sum -c` beside the built files.
        for vmod_id in sorted(vmods):
            for filename in sorted(vmods[vmod_id]["artifacts"]):
                print(f"{vmods[vmod_id]['artifacts'][filename]}  {filename}")
    return 0


def cmd_selftest(args) -> int:
    import selftest

    root = Path(args.repo_root).resolve()
    # The self-tests run in the global CI job, which has no cachetag checkout.
    # They therefore resolve the checkout themselves and report the
    # source-coupled tests as skipped when it is absent, rather than failing on
    # a dependency the global job deliberately does not have.
    return selftest.main(repo_root=root, cachetag_src=_cachetag_src(args, root))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_tool.py",
        description="Validate the Vinyl cohort registry and generate native package metadata.",
    )
    parser.add_argument(
        "--repo-root",
        default=str(manifest.REPO_ROOT),
        help="repository root (default: the checkout containing this script)",
    )
    parser.add_argument(
        "--cachetag-src",
        default=None,
        help=(
            "path to a libvmod-cachetag checkout, whose configure.ac holds the "
            "authoritative cachetag version (default: $CACHETAG_SRC, else the "
            "sibling ../libvmod-cachetag)"
        ),
    )
    parser.add_argument(
        "--no-cachetag-cross-check",
        action="store_true",
        help=(
            "skip the cachetag configure.ac version cross-check, which needs a "
            "libvmod-cachetag checkout. For the global structural validation gate, "
            "which must not depend on any VMOD's source; the cross-check itself runs "
            "in the cachetag CI invocation after its checkout"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate every manifest under registry/")
    p_validate.add_argument("--cohort", help="validate only this cohort id")
    p_validate.add_argument(
        "--require-releasable",
        action="store_true",
        help="additionally require the manifests to be release-ready (rejects templates)",
    )
    p_validate.set_defaults(func=cmd_validate)

    p_cohort = sub.add_parser("cohort-id", help="show the canonical input blob and derived cohort id")
    p_cohort.add_argument("--cohort", required=True, help="cohort id or path to a cohort manifest")
    p_cohort.set_defaults(func=cmd_cohort_id)

    p_meta = sub.add_parser("metadata", help="generate native package version metadata")
    p_meta.add_argument("--cohort", help="cohort id")
    p_meta.add_argument("--target", help="target id, for example debian-13-amd64")
    p_meta.add_argument("--distro-native", help="distro-native target id instead of a cohort target")
    p_meta.add_argument(
        "--vmod",
        default="cachetag",
        help=(
            "which entry of the target's vmods map to generate for (default: cachetag, "
            "which is what every existing caller asks about)"
        ),
    )
    p_meta.add_argument("--format", choices=["json", "shell"], default="json")
    p_meta.add_argument(
        "--allow-template",
        action="store_true",
        help="print metadata for a template manifest (never valid for a real release)",
    )
    p_meta.set_defaults(func=cmd_metadata)

    p_notes = sub.add_parser(
        "release-notes",
        help="print a cohort's upstream release-note references (empty when absent)",
    )
    p_notes.add_argument("--cohort", required=True, help="cohort id or path to a cohort manifest")
    p_notes.add_argument("--format", choices=["json", "body"], default="json")
    p_notes.set_defaults(func=cmd_release_notes)

    p_rec = sub.add_parser(
        "recorded-evidence",
        help="print the package identity and artifact digests the registry RECORDS",
    )
    p_rec.add_argument("--cohort", required=True, help="cohort id or path to a cohort manifest")
    p_rec.add_argument("--target", required=True, help="target id, for example debian-13-amd64")
    p_rec.add_argument(
        "--vmod",
        default="",
        help="one entry of the target's vmods map (default: every entry)",
    )
    p_rec.add_argument(
        "--format",
        choices=["json", "sha256sums"],
        default="json",
        help="sha256sums emits `<digest>  <filename>` lines, for `sha256sum -c`",
    )
    p_rec.set_defaults(func=cmd_recorded_evidence)

    p_self = sub.add_parser("selftest", help="run the release tooling tests")
    p_self.set_defaults(func=cmd_selftest)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except yaml_subset.ManifestSyntaxError as exc:
        print(f"ERROR    {exc}", file=sys.stderr)
        return 1
    except (manifest.ValidationError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR    {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
