# trunk-watch state

Machine-written CI bookkeeping for `.github/workflows/trunk-early-warning.yml`.
Not packaging history, which is why this is an orphan branch: it shares no
commit with `main` and nothing merges it.

`trunk-watch-state.json` is `upstream-watch-state/v1`. `refs` is the last-seen
commit of every watched ref, where "last seen" means **tested**, not merely
observed. `tags` is observation memory: first-seen commits and poison flags for
the pinned rows' stable-family tags, and the announced tag set per fleet
upstream; it advances on every run, tested or not, because a tag observation is
a fact about the remote, not a claim about testing. The `trunk_engine_run_id`
field is reserved for the run that produced a reusable trunk engine artifact,
and is unfilled until the sibling repository's harness can accept a prebuilt
prefix.

Do not edit by hand. Deleting the branch is safe but is a deliberate act: the
watcher fails open and the next run tests everything, fleet upstreams reseed
silently, and the poisoned-tag memory is forgotten -- which is the one
documented way, short of a re-pin, to clear a poison marker.
