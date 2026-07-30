# trunk-watch state

Machine-written CI bookkeeping for `.github/workflows/trunk-early-warning.yml`.
Not packaging history, which is why this is an orphan branch: it shares no
commit with `main` and nothing merges it.

`trunk-watch-state.json` is `upstream-watch-state/v1`: the last-seen commit of
every watched ref, where "last seen" means **tested**, not merely observed. The
`trunk_engine_run_id` field is reserved for the run that produced a reusable
trunk engine artifact, and is unfilled until the sibling repository's harness
can accept a prebuilt prefix.

Do not edit by hand. Deleting the branch is safe: the watcher fails open, and
the next run tests everything.
