# Vendored upstream material

Third-party source material, committed verbatim and recorded here. Nothing in `upstream/` is our work, and nothing in it is release-ready until it has been audited and updated deliberately.

## pkg-vinyl-cache

| | |
| --- | --- |
| Path | `upstream/pkg-vinyl-cache/` |
| Source | `https://code.vinyl-cache.org/vinyl-cache/pkg-vinyl-cache` |
| Commit | `27c91305023b4c4dae09f903644774fb9dbd8fcb` |
| Commit subject | Fedora: Remove trailing BUILDROOT from version |
| Commit date | 2026-04-10 16:52:15 +0200 |
| Date vendored | 2026-07-24 |
| Vendored as | working tree at that commit, `.git` removed; 56 entries (42 regular files, 14 symlinks) |
| Upstream tree | `aa31e4100541165c777c6bf5234648f9a8b3fb6e` |

The vendored content is verified, not asserted: the Git tree object for `upstream/pkg-vinyl-cache/` in this repository hashes to `aa31e4100541165c777c6bf5234648f9a8b3fb6e`, which is exactly the tree of upstream commit `27c91305023b4c4dae09f903644774fb9dbd8fcb`. Content, file modes, and the 14 symlinks upstream uses to share systemd and logrotate files across distro directories are all identical. Re-check at any time with:

```sh
git write-tree --prefix=upstream/pkg-vinyl-cache
```

This is the same commit the [binary packaging and distribution plan](../../libvmod-cachetag/docs/20260724_1526_plan_binary-packaging-and-distribution.md) inspected, under "Vinyl packages are part of the primary dependency chain". It is vendored rather than submoduled so that the exact bytes we audited are in the repository, reviewable in a diff as we modernise them.

### Audit verdict, from the plan

> The project `pkg-vinyl-cache` repository was inspected at commit `27c91305023b4c4dae09f903644774fb9dbd8fcb`. It contains useful Debian and RPM ABI-provider generation, but the Debian recipes use debhelper 9-era conventions, the Arch recipe declares only x86_64, and the Alpine recipe retains version and checksum placeholders. Treat it as source material to audit and update for Vinyl 9, not as a release-ready packaging base. Its existence does not change the official documentation's statement that Vinyl 9 binary packages are not yet available.

In short: **source material to audit and update for Vinyl 9, not a release-ready base.**

### Rules for this directory

- Do not modernise these recipes in place. The Vinyl 9 recipes are new work and belong outside `upstream/`, so that a reviewer can diff what we changed against what upstream shipped.
- If a change to the vendored tree ever becomes unavoidable, record it in this file with the reason. As of the vendoring date there are none: the tree is exactly the upstream commit.
- Re-vendoring at a newer commit means updating the commit id, subject, date, and file count above in the same change.

### Note on the vendored `.gitignore`

`upstream/pkg-vinyl-cache/.gitignore` is upstream's own and ignores `/sources` and `/build`, which are its download and build output directories. That rule would have excluded upstream's tracked `sources/.placeholder` from our repository, leaving the vendored tree quietly incomplete, so that one file was added with `git add -f`. The vendored `.gitignore` itself is unmodified, and it still does the right thing for any build output produced under this directory.
