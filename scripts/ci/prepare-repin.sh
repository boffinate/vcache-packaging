#!/bin/sh
#
# Open a branch and a pull request carrying the recorded re-pin for every
# eligible re-pin candidate the watcher observed. Publishes nothing, merges
# nothing, and produces no evidence.
#
#   scripts/ci/prepare-repin.sh REPORT_JSON
#
# REPORT_JSON is the raw `upstream-watch-report/v1` document the gate job wrote
# with `upstream_watch.py check --report`. Everything that needs a decision --
# which candidates may be prepared, what the new pin is, what the pull request
# says -- is decided by tools/repin_prepare.py, which is selftested. This script
# is the part that cannot be: git, curl and gh.
#
# Policy: docs/20260730_1812_note_auto-prepared-repin-pr.md, under the manual
# publication gate of docs/20260730_1635_note_publication-authority-decision.md.
#
# THREE THINGS IT MUST NOT DO, in order of how much they would cost:
#
#   1. Claim evidence. The pull request says "observed, not tested" and the
#      dispatched CI run is what produces evidence. Nothing here writes a
#      target manifest, a cohort manifest, or a transaction expectation --
#      repin_prepare.py refuses those paths outright.
#   2. Prepare the same candidate twice. The interlock is three-fold and any
#      one of them stops a second attempt: the branch already exists, a pull
#      request from it is open, or the watcher's issue has been CLOSED (which
#      is how the maintainer declines a candidate).
#   3. Fail quietly. A candidate that errors gets a comment on its watcher
#      issue saying what failed, and the job ends red. An unnotified failure is
#      the one forbidden outcome: the whole detect-verify-notify chain exists
#      because a finding that sits in a green run's log is a finding nobody has.
#
# CI evidence is dispatched, not triggered. A pull request opened with
# GITHUB_TOKEN does not start `pull_request` workflows -- that is GitHub's loop
# protection and it is not configurable -- but an explicit `gh workflow run` is
# exempt. So the run is dispatched against the branch and linked from the pull
# request. No new credential: GH_TOKEN stays ${{ github.token }} and this
# repository stores no secrets.

set -eu

report=${1:-}
if [ -z "$report" ] || [ ! -f "$report" ]; then
    echo "usage: prepare-repin.sh REPORT_JSON (the watcher's raw report)" >&2
    exit 2
fi

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/../.." && pwd)
work=${RUNNER_TEMP:-/tmp}/repin-prepare
rm -rf "$work"
mkdir -p "$work"

# THE WORK DIRECTORY MUST BE OUTSIDE THE CHECKOUT. Every loop iteration resets
# the tree with `git checkout --force` and `git clean -fd`, and `clean -fd`
# deletes every untracked file under the checkout. Anything this script needs
# that lives in there would be deleted between the first candidate and the
# second -- which is precisely how a downloaded artifact disappears halfway
# through a run and every candidate after the first fails on a missing input.
case "$work/" in
    "$repo"/*)
        echo "E: the work directory $work is inside the checkout at $repo." >&2
        echo "E: the per-candidate tree reset would delete it. Point RUNNER_TEMP" >&2
        echo "E: outside the workspace." >&2
        exit 2
        ;;
esac

# Same rule applied to the input: copy the report out of wherever the caller
# put it BEFORE the first tree reset, and read the copy from then on. The
# workflow already downloads the artifact to the runner temp directory; this is
# the belt to that braces, because the failure it prevents is silent for the
# first candidate and fatal for the rest.
cp "$report" "$work/watch-report.json"
report="$work/watch-report.json"

: "${GITHUB_REPOSITORY:?prepare-repin.sh needs GITHUB_REPOSITORY}"
: "${GH_TOKEN:?prepare-repin.sh needs GH_TOKEN}"
summary=${GITHUB_STEP_SUMMARY:-/dev/null}
base_ref=${GITHUB_REF_NAME:-main}
base_sha=$(git -C "$repo" rev-parse HEAD)

git -C "$repo" config user.name "github-actions[bot]"
git -C "$repo" config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# The classification, and the whole of it: every candidate the watcher found,
# each either eligible or refused with its reasons, in the step summary as well
# as on stderr. A refusal is a reported outcome, never an omission.
python3 "$repo/tools/repin_prepare.py" --repo-root "$repo" eligibility \
    --report "$report" \
    --json "$work/eligibility.json" \
    --tsv "$work/eligible.tsv" \
    --summary "$summary"

if [ ! -s "$work/eligible.tsv" ]; then
    echo "no candidate is eligible for automatic preparation; nothing to do"
    echo "No candidate was eligible for automatic preparation." >> "$summary"
    exit 0
fi

# One listing, then exact-title matching locally -- the same shape the notify
# job uses, and the same dedupe key: the issue TITLE.
gh issue list --repo "$GITHUB_REPOSITORY" --state all \
    --label upstream-watch --limit 500 \
    --json number,title,state > "$work/issues.json"

errors=0
prepared=0
skipped=0
issue_number=0

# Log only. The step summary is a table by this point, and a loose line dropped
# into the middle of one stops rendering as a table.
note() {
    echo "$1"
}

# Say what went wrong where the maintainer will find it. If there is no issue to
# comment on -- the notify job has not filed one yet, or it was filed under a
# different title -- the job still ends red and the log still carries the
# reason, which is why this never returns non-zero itself.
stop() {
    echo "FAILED: $1" >&2
    if [ "$issue_number" != 0 ]; then
        {
            printf 'Automated re-pin preparation **stopped** for `%s` -> `%s`.\n\n' \
                "$current_vmod" "$current_tag"
            printf '%s\n\n' "$1"
            printf 'Nothing was published. This candidate is left exactly where the failure\n'
            printf 'left it.\n\n'
            printf 'NOTE ON REPETITION. Nothing records that this attempt happened, by\n'
            printf 'design -- the interlock is the branch, an open pull request, and this\n'
            printf 'issue, and a failure before the push creates none of them. So the next\n'
            printf 'scheduled run will try this candidate again and comment again, until\n'
            printf 'either the cause clears or you CLOSE this issue, which declines the\n'
            printf 'candidate and stops the retries.\n\n'
            printf 'Run: %s/%s/actions/runs/%s\n' \
                "${GITHUB_SERVER_URL:-https://github.com}" "$GITHUB_REPOSITORY" \
                "${GITHUB_RUN_ID:-unknown}"
        } > "$work/stop.md"
        gh issue comment "$issue_number" --repo "$GITHUB_REPOSITORY" \
            --body-file "$work/stop.md" || echo "W: could not comment on issue $issue_number" >&2
    fi
    # Flattened: the reason is written as a paragraph for the issue comment, and
    # a newline inside a markdown table cell stops the table rendering as one.
    printf '| `%s` -> `%s` | FAILED | %s |\n' "$current_vmod" "$current_tag" \
        "$(printf '%s' "$1" | tr '\n' ' ')" >> "$summary"
    return 0
}

{
    echo "### Preparation outcomes"
    echo
    echo "| candidate | outcome | detail |"
    echo "| --- | --- | --- |"
} >> "$summary"

tab=$(printf '\t')
# Read on fd 3, not stdin: everything in the loop body -- git, gh, curl -- keeps
# its own stdin, so nothing can swallow the rest of the candidate list.
while IFS="$tab" read -r vmod tag pinned observed branch clone issue_title <&3; do
    [ -n "${vmod:-}" ] || continue
    current_vmod=$vmod
    current_tag=$tag
    issue_number=0
    printf '\n===== %s: %s -> %s =====\n' "$vmod" "$pinned" "$tag"

    # A clean tree at the base commit for every candidate: the previous one may
    # have left a branch checked out with its edits in it.
    git -C "$repo" checkout --quiet --force "$base_sha"
    git -C "$repo" clean --quiet -fd

    if ! lookup=$(python3 "$repo/tools/repin_prepare.py" issue-lookup \
        --issues "$work/issues.json" --title "$issue_title"); then
        echo "FAILED: could not resolve the watcher issue for $vmod $tag" >&2
        printf '| `%s` -> `%s` | FAILED | the watcher issue could not be resolved |\n' \
            "$vmod" "$tag" >> "$summary"
        errors=$((errors + 1))
        continue
    fi
    issue_number=$(printf '%s' "$lookup" | cut -f1)
    issue_state=$(printf '%s' "$lookup" | cut -f2)
    echo "watcher issue: #$issue_number ($issue_state)"

    # No issue at all is ABNORMAL, not a quiet case to work around: this job
    # runs after the notify job, which files one issue per upstream and tag from
    # the same observation, so a missing one means notify did not do its work or
    # the titles have drifted apart. Proceeding would link the pull request to
    # issue 0 and comment into the void -- a preparation nobody was told about,
    # which is the one outcome this chain exists to prevent.
    if [ "$issue_number" = 0 ]; then
        echo "FAILED: no watcher issue titled '$issue_title' exists." >&2
        echo "        The notify job files one per upstream and tag from the same" >&2
        echo "        observation, so this candidate has no durable record to hang a" >&2
        echo "        preparation off. Nothing is prepared for it." >&2
        printf '| `%s` -> `%s` | FAILED | no watcher issue exists to record it against |\n' \
            "$vmod" "$tag" >> "$summary"
        errors=$((errors + 1))
        continue
    fi

    # --- interlock -------------------------------------------------------
    if [ "$issue_state" = closed ]; then
        # A closed issue means the maintainer has seen this candidate and
        # handled or declined it. Preparing a branch for it now would re-raise
        # a decision that has already been made.
        note "skip: the watcher issue for $vmod $tag is closed; the maintainer has handled it"
        printf '| `%s` -> `%s` | skipped | watcher issue closed |\n' "$vmod" "$tag" >> "$summary"
        skipped=$((skipped + 1))
        issue_number=0
        continue
    fi
    if git -C "$repo" ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
        note "skip: $branch already exists; a preparation for this tag has already run"
        printf '| `%s` -> `%s` | skipped | branch `%s` exists |\n' "$vmod" "$tag" "$branch" \
            >> "$summary"
        skipped=$((skipped + 1))
        issue_number=0
        continue
    fi
    open_pr=$(gh pr list --repo "$GITHUB_REPOSITORY" --head "$branch" --state open \
        --json number --jq 'length' 2>/dev/null || echo 0)
    if [ "${open_pr:-0}" != 0 ]; then
        note "skip: a pull request from $branch is already open"
        printf '| `%s` -> `%s` | skipped | pull request open |\n' "$vmod" "$tag" >> "$summary"
        skipped=$((skipped + 1))
        issue_number=0
        continue
    fi

    # --- the plan --------------------------------------------------------
    if ! python3 "$repo/tools/repin_prepare.py" --repo-root "$repo" plan \
        --report "$report" --vmod "$vmod" --tag "$tag" \
        --json "$work/plan.json" --env "$work/plan.env"; then
        stop "The re-pin plan could not be built. See the run log for the refusal."
        errors=$((errors + 1))
        continue
    fi
    # shellcheck disable=SC1091
    . "$work/plan.env"

    # --- re-peel: the observation must still hold ------------------------
    peeled=$(git ls-remote "$clone" "refs/tags/$tag^{}" 2>/dev/null | cut -f1 | head -n1)
    if [ -z "$peeled" ]; then
        peeled=$(git ls-remote "$clone" "refs/tags/$tag" 2>/dev/null | cut -f1 | head -n1)
    fi
    if [ -z "$peeled" ]; then
        stop "Re-peeling \`$tag\` at $clone found no such tag. The tag the watcher observed is
not there now; nothing is prepared from an observation that no longer holds."
        errors=$((errors + 1))
        continue
    fi
    if [ "$peeled" != "$observed" ]; then
        stop "\`$tag\` now peels to \`$peeled\`, but the watcher observed \`$observed\`. A tag
that moved between the observation and the preparation is exactly the moved-tag
condition, and it is never re-pinned automatically. Establish what moved."
        errors=$((errors + 1))
        continue
    fi
    echo "re-peeled $tag -> $peeled (matches the observation)"

    # --- ancestry (release-automation plan 1.1(b)) -----------------------
    # `ls-remote` cannot answer reachability, so this is the first point in the
    # chain with a clone to ask. A tag whose commit is not reachable from the
    # upstream's default branch is a supply-chain-relevant anomaly, and it
    # becomes a loud stop rather than a well-evidenced build of the wrong thing.
    mirror="$work/mirror-$vmod.git"
    rm -rf "$mirror"
    if ! git clone --quiet --filter=blob:none --bare "$clone" "$mirror" 2>/dev/null; then
        # Not every host serves partial clones; the full bare clone answers the
        # same question and costs more bandwidth, which is the right trade.
        rm -rf "$mirror"
        if ! git clone --quiet --bare "$clone" "$mirror"; then
            stop "Could not clone $clone to check that \`$tag\` is reachable from the upstream's
default branch. The ancestry check is not optional, so this candidate stops here."
            errors=$((errors + 1))
            continue
        fi
    fi
    default_branch=$(git -C "$mirror" symbolic-ref --quiet --short HEAD || echo "")
    if [ -z "$default_branch" ]; then
        stop "Could not determine the default branch of $clone, so \`$tag\`'s reachability
could not be checked."
        errors=$((errors + 1))
        continue
    fi
    if ! git -C "$mirror" merge-base --is-ancestor "$peeled" "refs/heads/$default_branch"; then
        stop "\`$tag\` peels to \`$peeled\`, which is **not reachable** from the upstream's
default branch \`$default_branch\`. A release tag on a commit outside the
published history is not re-pinned automatically. Establish where it came from."
        errors=$((errors + 1))
        continue
    fi
    ancestry="\`$peeled\` is reachable from the upstream default branch \`$default_branch\`"
    echo "ancestry: $ancestry"

    # --- the archive -----------------------------------------------------
    if [ "$REPIN_ARCHIVE_METHOD" != "upstream-release" ] || [ -z "$REPIN_ARCHIVE_URL" ]; then
        stop "This row's archive is not a published download (method
\`$REPIN_ARCHIVE_METHOD\`), so its digest cannot be pinned from a fetch. Prepare
this re-pin manually."
        errors=$((errors + 1))
        continue
    fi
    rm -f "$work/archive.bin"
    if ! curl --fail --silent --show-error --location --retry 3 --max-time 600 \
        --output "$work/archive.bin" "$REPIN_ARCHIVE_URL"; then
        stop "The archive for the new pin could not be downloaded from
$REPIN_ARCHIVE_URL -- so there is nothing to compute a digest over. A missing
source is a useful signal, not something to work around."
        errors=$((errors + 1))
        continue
    fi
    digest=$(sha256sum "$work/archive.bin" | cut -d' ' -f1)
    bytes=$(wc -c < "$work/archive.bin" | tr -d ' ')
    echo "archive: $bytes bytes, sha256 $digest"

    # --- record the pin --------------------------------------------------
    if ! python3 "$repo/tools/repin_prepare.py" --repo-root "$repo" apply \
        --plan "$work/plan.json" --commit "$peeled" \
        --archive-sha256 "$digest" --archive-bytes "$bytes"; then
        stop "Recording the pin failed. The tool refuses to write a value it did not find
exactly where the plan said it would be, so the registry has probably moved."
        errors=$((errors + 1))
        continue
    fi
    if git -C "$repo" diff --quiet; then
        stop "Recording the pin produced no diff at all, which cannot be right for a re-pin.
Nothing is pushed."
        errors=$((errors + 1))
        continue
    fi

    # --- the host-safe gates, on the edited tree -------------------------
    if ! python3 "$repo/tools/release_tool.py" --repo-root "$repo" \
        --no-cachetag-cross-check validate; then
        stop "\`release_tool.py validate\` fails on the edited tree, so the recorded pin is not
even structurally coherent. Nothing is pushed."
        errors=$((errors + 1))
        continue
    fi
    if ! python3 "$repo/tools/ci_matrix.py" --repo-root "$repo" check-catalog; then
        stop "\`ci_matrix.py check-catalog\` fails on the edited tree. Nothing is pushed."
        errors=$((errors + 1))
        continue
    fi
    if ! python3 "$repo/tools/ci_matrix.py" --repo-root "$repo" validate-vmod \
        --manifest "$repo/$REPIN_MANIFEST_PATH" --id "$vmod"; then
        stop "\`ci_matrix.py validate-vmod\` fails on the edited manifest. Nothing is pushed."
        errors=$((errors + 1))
        continue
    fi

    # --- the branch ------------------------------------------------------
    if ! python3 "$repo/tools/repin_prepare.py" pr-body \
        --plan "$work/plan.json" --commit "$peeled" \
        --archive-sha256 "$digest" --archive-bytes "$bytes" \
        --ancestry "$ancestry" \
        --issue "${GITHUB_SERVER_URL:-https://github.com}/$GITHUB_REPOSITORY/issues/$issue_number" \
        --title-file "$work/title.txt" --body-file "$work/body.md"; then
        stop "Rendering the pull request description failed, so nothing was pushed. A
prepared branch without the description that states what it is -- and that it is
an observation rather than evidence -- is worse than no branch."
        errors=$((errors + 1))
        continue
    fi

    {
        printf 'repin(%s): %s -> %s, prepared automatically\n\n' "$vmod" "$pinned" "$tag"
        printf 'Recorded from an OBSERVATION, not from evidence: the watcher saw %s on\n' "$tag"
        printf '%s and this commit writes the pin it implies -- tag, peeled commit\n' "$vmod"
        printf '%s, and the sha256 of the archive as fetched. Nothing here has\n' "$peeled"
        printf 'been built or tested; CI evidence is dispatched separately and is pending.\n\n'
        printf 'No evidence file is touched. The recorded target evidence and the pinned\n'
        printf 'transaction expectations still describe the OLD pin and move with the run\n'
        printf 'that measures the new one, by a human.\n\n'
        printf 'Opened by scripts/ci/prepare-repin.sh from run %s.\n' "${GITHUB_RUN_ID:-unknown}"
    } > "$work/commit-msg.txt"

    # Guarded like everything else in the loop: a git failure here would
    # otherwise abort the whole script under `set -e`, taking every candidate
    # after this one with it and commenting on nobody's issue.
    if ! git -C "$repo" checkout --quiet -B "$branch" "$base_sha" \
        || ! git -C "$repo" add -A \
        || ! git -C "$repo" commit --quiet -F "$work/commit-msg.txt"; then
        stop "Committing the recorded pin to \`$branch\` failed, so nothing was pushed."
        errors=$((errors + 1))
        continue
    fi
    if ! git -C "$repo" push --quiet origin "$branch"; then
        stop "Pushing \`$branch\` failed. Nothing was opened."
        errors=$((errors + 1))
        continue
    fi
    echo "pushed $branch"

    pr_output=$(gh pr create --repo "$GITHUB_REPOSITORY" --base "$base_ref" --head "$branch" \
        --title "$(head -n1 "$work/title.txt")" --body-file "$work/body.md" 2>&1) || true
    printf '%s\n' "$pr_output"
    pr_url=$(printf '%s\n' "$pr_output" | grep -o 'https://[^ ]*/pull/[0-9]*' | tail -n1 || true)
    if [ -z "$pr_url" ]; then
        stop "The branch \`$branch\` is pushed, but opening the pull request failed. If the
repository does not allow GitHub Actions to create pull requests, that setting
is the cause and the branch is ready to open by hand."
        errors=$((errors + 1))
        continue
    fi
    echo "opened $pr_url"

    # --- dispatch the evidence run ---------------------------------------
    # A prepared pull request with no evidence run is the failure mode this
    # whole increment is supposed to prevent: it looks, to a reader, exactly
    # like a change nobody tested. So a failed dispatch is a candidate-level
    # ERROR -- the job ends red and both comments say plainly that CI was not
    # started and has to be started by hand.
    run_url=""
    dispatched=no
    if gh workflow run ci.yml --repo "$GITHUB_REPOSITORY" --ref "$branch"; then
        dispatched=yes
        attempt=0
        while [ "$attempt" -lt 12 ]; do
            sleep 5
            run_url=$(gh run list --repo "$GITHUB_REPOSITORY" --workflow ci.yml \
                --branch "$branch" --limit 1 --json url --jq '.[0].url' 2>/dev/null || echo "")
            if [ "$run_url" = null ]; then
                run_url=""
            fi
            if [ -n "$run_url" ]; then
                break
            fi
            attempt=$((attempt + 1))
        done
    else
        echo "FAILED: dispatching ci.yml against $branch failed" >&2
    fi

    {
        if [ "$dispatched" = no ]; then
            printf '**CI was NOT dispatched for this branch.** Dispatching `ci.yml` against\n'
            printf '`%s` failed, so there is no evidence run and none is coming\n' "$branch"
            printf 'on its own. Start `ci.yml` against this branch by hand before reading\n'
            printf 'anything here as tested.\n\n'
        elif [ -n "$run_url" ]; then
            printf 'CI evidence for this pin: %s\n\n' "$run_url"
        else
            printf '`ci.yml` was dispatched against `%s`, but the run had not\n' "$branch"
            printf 'appeared in the run list yet when this comment was written. Check the\n'
            printf 'Actions tab; if there is no run, start one by hand.\n\n'
        fi
        printf 'Dispatched rather than triggered: a pull request opened with GITHUB_TOKEN does\n'
        printf 'not start `pull_request` workflows, so the evidence run is an explicit\n'
        printf '`workflow_dispatch` against this branch. Until it is green, this pull request\n'
        printf 'carries an observation and nothing more.\n'
    } > "$work/pr-comment.md"
    gh pr comment "$pr_url" --repo "$GITHUB_REPOSITORY" --body-file "$work/pr-comment.md" \
        || echo "W: could not comment on $pr_url" >&2

    {
        printf 'A re-pin branch is prepared for review: %s\n\n' "$pr_url"
        printf 'It records `%s` at `%s` with the archive digest\n' "$tag" "$peeled"
        printf '`%s`, and **nothing else**.\n\n' "$digest"
        if [ "$dispatched" = no ]; then
            printf 'It is an observation, and **CI was not dispatched**: starting `ci.yml`\n'
            printf 'against the branch failed, so nothing has been built or tested and\n'
            printf 'nothing will be until somebody starts it by hand.\n\n'
        else
            printf 'It is an observation, not evidence: `ci.yml` was dispatched against the\n'
            printf 'branch (%s) and its result is what makes any\n' \
                "${run_url:-run not listed yet}"
            printf 'claim about this pin.\n\n'
        fi
        printf 'The pull request describes the human work that has to follow.\n\n'
        printf 'Closing this issue declines the candidate and stops any further automatic\n'
        printf 'preparation for this tag.\n'
    } > "$work/issue-comment.md"
    gh issue comment "$issue_number" --repo "$GITHUB_REPOSITORY" \
        --body-file "$work/issue-comment.md" \
        || echo "W: could not comment on issue $issue_number" >&2

    prepared=$((prepared + 1))
    if [ "$dispatched" = no ]; then
        printf '| `%s` -> `%s` | prepared, CI DISPATCH FAILED | %s |\n' \
            "$vmod" "$tag" "$pr_url" >> "$summary"
        errors=$((errors + 1))
    else
        printf '| `%s` -> `%s` | prepared | %s |\n' "$vmod" "$tag" "$pr_url" >> "$summary"
    fi
    issue_number=0
done 3< "$work/eligible.tsv"

git -C "$repo" checkout --quiet --force "$base_sha" || true

{
    echo
    echo "$prepared prepared, $skipped skipped by the interlock, $errors error(s)."
    echo "(A candidate can appear in both counts: a branch and pull request can be prepared"
    echo "and the CI dispatch still fail, which is an error because a prepared pull request"
    echo "with no evidence run reads exactly like a change nobody tested.)"
    echo
    echo "A prepared branch publishes nothing and proves nothing. Evidence comes from the"
    echo "dispatched CI run; the release itself remains a deliberate human dispatch."
} >> "$summary"

echo
echo "prepared=$prepared skipped=$skipped errors=$errors"
if [ "$errors" != 0 ]; then
    echo "E: $errors candidate(s) errored. Each one that had a watcher issue has a comment" >&2
    echo "E: on it saying what failed; a candidate with NO watcher issue is reported here" >&2
    echo "E: and in the step summary only, because there was nowhere else to put it." >&2
    exit 1
fi
exit 0
