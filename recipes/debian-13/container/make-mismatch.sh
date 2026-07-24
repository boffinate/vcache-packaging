#!/bin/bash
#
# Build one synthetic mismatched Vinyl candidate package pair by a scripted,
# metadata-level transformation of the retained baseline cohort debs.
#
# Runs inside the pinned debian:trixie buildroot. Reads the baseline packages
# from /out (the retained cohort artifacts) and writes the fixture pair into
# /out/mismatch/.
#
# Why a repack rather than a second Vinyl build: the transaction matrix tests
# the *resolver*, and the only inputs the resolver reads are the version and
# the dependency/provides relations. A second full Vinyl compile would change
# nothing the resolver can see, would take an hour, and would still not be the
# real future security release. A repack keeps the payload byte-identical to
# the audited baseline, so any behaviour difference in a transaction is
# attributable to the metadata change alone. The transformation is scripted,
# deterministic and recorded, and the fixture is a genuinely installable deb:
# every scenario in transactions.sh installs it for real.
#
# Required environment:
#   FIXTURE_VARIANT     mismatch | sameabi
#   FIXTURE_VERSION     the synthetic (higher) Debian version
#   FIXTURE_ABI         the vinyld-abi-<hash> token the fixture advertises
#   BASE_VERSION        the baseline cohort Debian version
#   BASE_ABI            the baseline strict ABI hash
#   DEB_HOST_ARCH       target architecture of the baseline debs
#   SOURCE_DATE_EPOCH   fixed timestamp, so the fixture is reproducible and the
#                       digest retained per the plan stays meaningful
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C

: "${FIXTURE_VARIANT:?}" "${FIXTURE_VERSION:?}" "${FIXTURE_ABI:?}"
: "${BASE_VERSION:?}" "${BASE_ABI:?}" "${DEB_HOST_ARCH:?}" "${SOURCE_DATE_EPOCH:?}"
export SOURCE_DATE_EPOCH

apt-get update -qq
apt-get install -y --no-install-recommends dpkg-dev python3 >/dev/null

out=/out/mismatch
mkdir -p "$out"
work=$(mktemp -d)

# Deterministic, not "now": the plan requires the fixture's digest to be
# retained, and a digest that changes every time the script runs is not
# evidence of anything.
stamp=$(date -u -d "@$SOURCE_DATE_EPOCH" +%Y-%m-%dT%H:%M:%SZ)

echo "===== synthetic fixture: variant=$FIXTURE_VARIANT version=$FIXTURE_VERSION abi=$FIXTURE_ABI ====="

for pkg in vinyl-cache vinyl-cache-dev; do
	src="/out/${pkg}_${BASE_VERSION}_${DEB_HOST_ARCH}.deb"
	[ -f "$src" ] || { echo "E: baseline deb missing: $src" >&2; exit 1; }
	base_sha=$(sha256sum "$src" | awk '{print $1}')
	echo
	echo "--- $pkg ---"
	echo "source deb:        $(basename "$src")"
	echo "source sha256:     $base_sha"

	root="$work/$pkg-$FIXTURE_VARIANT"
	dpkg-deb -R "$src" "$root"

	# The self-identifying marker. It is also the "different content" half of
	# the sameabi variant: a package whose payload differs from the baseline
	# while its advertised strict-ABI token does not.
	marker="$root/usr/share/doc/$pkg/SYNTHETIC-FIXTURE.txt"
	[ -d "$(dirname "$marker")" ] || { echo "E: no doc dir for $pkg" >&2; exit 1; }
	cat > "$marker" <<-EOF
		SYNTHETIC PACKAGING FIXTURE -- NOT A REAL VINYL CACHE BUILD

		variant:            $FIXTURE_VARIANT
		package:            $pkg
		fixture version:    $FIXTURE_VERSION
		advertised ABI:     vinyld-abi-$FIXTURE_ABI
		baseline version:   $BASE_VERSION
		baseline ABI:       vinyld-abi-$BASE_ABI
		baseline deb:       $(basename "$src")
		baseline sha256:    $base_sha
		fixture epoch:      $stamp (SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH)
		generator:          vinyl-packaging/recipes/debian-13/mismatch-fixture.sh

		This package's payload is the byte-identical payload of the baseline
		cohort deb named above, plus this file. Only the control metadata was
		rewritten: Version, the vinyld-abi-<hash> virtual provide (or, for the
		-dev package, the exact runtime version dependency), Installed-Size,
		and a banner appended to the Description.

		It exists solely to drive package-manager upgrade-transaction tests for
		the binary packaging and distribution plan. It carries no real code
		change, it is not a security update, and it must never be published to
		a user-facing repository.
	EOF

	# dpkg-gencontrol derives Installed-Size with du -k -s over everything but
	# DEBIAN/. Reproduce that rather than inventing an estimate, so the only
	# numeric change is the one the marker file genuinely causes.
	FIXTURE_INSTALLED_SIZE=$(du -k -s --exclude=./DEBIAN "$root" | awk '{print $1}')
	export FIXTURE_INSTALLED_SIZE

	python3 - "$root" <<-'PY'
		import hashlib, os, subprocess, sys

		root = sys.argv[1]
		env = os.environ
		variant = env["FIXTURE_VARIANT"]
		version = env["FIXTURE_VERSION"]
		abi = env["FIXTURE_ABI"]
		base_version = env["BASE_VERSION"]
		base_abi = env["BASE_ABI"]

		ctrl_path = os.path.join(root, "DEBIAN", "control")
		with open(ctrl_path, encoding="utf-8") as fh:
		    lines = fh.read().split("\n")

		# Split the stanza into (field, value-with-continuations) pairs so the
		# multi-line Description survives untouched.
		fields = []
		for line in lines:
		    if not line:
		        continue
		    if line[0] in " \t" and fields:
		        fields[-1][1].append(line)
		    else:
		        name, _, rest = line.partition(":")
		        fields.append([name, [rest.strip()]])

		def get(name):
		    for f in fields:
		        if f[0] == name:
		            return f
		    return None

		def set_first(name, value):
		    f = get(name)
		    if f is None:
		        raise SystemExit("missing control field: " + name)
		    f[1][0] = value

		set_first("Version", version)

		prov = get("Provides")
		if prov is not None:
		    before = prov[1][0]
		    after = before.replace("vinyld-abi-" + base_abi, "vinyld-abi-" + abi)
		    if before == after and base_abi != abi:
		        raise SystemExit("Provides did not contain the baseline ABI token")
		    prov[1][0] = after
		    print("  Provides: %s -> %s" % (before, after))

		dep = get("Depends")
		if dep is not None:
		    before = dep[1][0]
		    after = before.replace(
		        "vinyl-cache (= %s)" % base_version, "vinyl-cache (= %s)" % version
		    )
		    dep[1][0] = after
		    if before != after:
		        print("  Depends: %s -> %s" % (before, after))

		desc = get("Description")
		banner = [
		    " .",
		    " SYNTHETIC PACKAGING FIXTURE (variant: %s). Repacked from the baseline" % variant,
		    " cohort package %s with rewritten control metadata only." % base_version,
		    " Not a real Vinyl Cache build and not a security update. See",
		    " /usr/share/doc/<package>/SYNTHETIC-FIXTURE.txt.",
		]
		desc[1].extend(banner)

		# Installed-Size is a control field the resolver reports to the user, so
		# keep it truthful after adding the marker file.
		set_first("Installed-Size", env["FIXTURE_INSTALLED_SIZE"])

		out = []
		for name, values in fields:
		    out.append("%s: %s" % (name, values[0]))
		    out.extend(values[1:])
		with open(ctrl_path, "w", encoding="utf-8") as fh:
		    fh.write("\n".join(out) + "\n")

		# Keep md5sums consistent with the payload we just extended.
		md5_path = os.path.join(root, "DEBIAN", "md5sums")
		if os.path.exists(md5_path):
		    pkg = get("Package")[1][0]
		    rel = "usr/share/doc/%s/SYNTHETIC-FIXTURE.txt" % pkg
		    with open(os.path.join(root, rel), "rb") as fh:
		        digest = hashlib.md5(fh.read()).hexdigest()
		    with open(md5_path, "a", encoding="utf-8") as fh:
		        fh.write("%s  %s\n" % (digest, rel))
	PY

	# Every file dpkg-deb will archive must have a fixed mtime, or the tarballs
	# inside the deb differ run to run. The payload files keep the baseline's
	# own mtimes; only the two files this script creates or edits are pinned.
	touch -d "@$SOURCE_DATE_EPOCH" "$marker" "$root/DEBIAN/control" "$root/DEBIAN/md5sums"

	deb="$out/${pkg}_${FIXTURE_VERSION}_${DEB_HOST_ARCH}.deb"
	rm -f "$deb"
	dpkg-deb --build --root-owner-group "$root" "$deb" >/dev/null
	echo "built:             $(basename "$deb")"
	echo "fixture sha256:    $(sha256sum "$deb" | awk '{print $1}')"
	dpkg -I "$deb" | sed -n '/^ Package:/,/^ Description:/p'
done

rm -rf "$work"

# Digest manifest over every fixture in the directory, regenerated whole so it
# always matches the directory contents.
( cd "$out" && ls -1 *.deb | sort | while IFS= read -r f; do
	printf '%s  %s\n' "$(sha256sum "$f" | awk '{print $1}')" "$f"
done > SHA256SUMS )

echo
echo "===== $out/SHA256SUMS ====="
cat "$out/SHA256SUMS"
