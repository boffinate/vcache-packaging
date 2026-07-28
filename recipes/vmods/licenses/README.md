# Debian `debian/copyright` licence stanzas

One file per Debian short licence name, holding the complete `License:` stanza that `debian/copyright` needs, already in the machine-readable copyright format's continuation style (leading space, `.` for a blank line).

The generator looks up `license.debian_short_name` from a VMOD's overlay in this directory and refuses to render when the file is missing. That is deliberate and is one half of the recipe-generation plan's rule against "unresolved or non-machine-readable licenses": a VMOD under a licence nobody has written a reviewed stanza for cannot be packaged until somebody writes one, rather than being packaged with a placeholder.

The short names are Debian's, from the [copyright-format 1.0 specification](https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/), not SPDX identifiers. The overlay carries both: `license.expression` is the SPDX expression RPM's `License:` field takes, `license.debian_short_name` is the name used here and in `debian/copyright`. They are different vocabularies and conflating them produces a `License:` field that is wrong on one of the two targets.

Where Debian ships the full licence text in `/usr/share/common-licenses`, the stanza references it after the summary, as Debian Policy §12.5 requires. Where it does not, the stanza must carry the full text.

| File | SPDX expression it serves |
| --- | --- |
| `GPL-3+.debian` | `GPL-3.0-or-later` |
