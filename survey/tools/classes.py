"""Failure-class taxonomy shared by sweep.py and report.py.

Every result class maps to one category, and the categories drive both the
report verdicts and resume behaviour:

- pass / fail — settled outcomes of the build+load smoke.
- unbuildable — configure demands a daemon source tree; can never work from
  the installed dev package as currently written.
- blocked — a third-party dependency is missing from the lane images;
  says something about Debian 13, not about 9.x compatibility.
- dead — the repository could not be provided to the sweep at all.
- artifact — the harness failed, not the VMOD (timeout, docker error,
  pin mismatch). Never publishable as a result; the sweep re-runs these.

Python 3 standard library only, per the repository tooling rule.
"""

CATEGORY_BY_CLASS = {
    "pass": "pass",
    "bootstrap-failed": "fail",
    "configure-failed-api-detect": "fail",
    "configure-failed-other": "fail",
    "compile-failed": "fail",
    "link-failed": "fail",
    "no-vmod-built": "fail",
    "load-failed": "fail",
    "configure-failed-needs-source-tree": "unbuildable",
    "configure-failed-missing-dep": "blocked",
    "clone-missing": "dead",
    "timeout": "artifact",
    "copy-failed": "artifact",
    "pin-mismatch": "artifact",
}

# Categories a resumed sweep re-runs instead of honouring: transient harness
# trouble must not bake into the matrix, and re-checking a missing clone is
# nearly free.
RETRY_CATEGORIES = ("artifact", "dead")


def category(cls):
    """Map a result class to its verdict category."""
    if cls is None:
        return None
    if cls.startswith("harness-error"):
        return "artifact"
    # An unknown class is treated as harness trouble, never as a verdict.
    return CATEGORY_BY_CLASS.get(cls, "artifact")
