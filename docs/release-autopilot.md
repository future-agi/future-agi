# Release autopilot

Two independent, opt-in policies remove the routine release-PR merge and image
publication approval. Neither policy deploys a cluster. Both switches ship off.
The main-ancestry guard is unconditional: even a manually approved release must
come from reviewed `main` history.

## Switches and prerequisites

All variables below are **repository variables**, not environment variables set
in the `Production` environment. The guard runs before entering an environment.
Only the exact value `on` enables a switch.

| Variable | Unset default | Admin action / meaning |
| --- | --- | --- |
| `RELEASE_AUTOPILOT` | Off | Set `on` to approve and merge eligible release-please PRs. |
| `RELEASE_AUTO_APPROVE` | Off | Set `on` to let migration-free tag pushes use `production-auto` instead of `Production`. Independent of PR auto-merge. |
| `RELEASE_AUTOPILOT_REQUIRED_CHECKS` | `Backend Tests Pass` | Comma- or newline-separated exact check/status names. Every listed name must succeed on the current PR head SHA. An explicitly nonempty but name-free list is invalid, not an empty gate. |
| `RELEASE_AUTOPILOT_MIN_AGE_MINUTES` | `30` | Non-negative integer minutes since the PR's last update (0–999999). The exact threshold is inclusive; malformed or future timestamps stop the merge. |
| `AUTOPILOT_HOURS_UTC` | No time-of-day restriction | Optional `HH-HH`, start inclusive and end exclusive, e.g. `04-15`. Overnight windows such as `22-06` work; `00-24` is all day. Equal endpoints and malformed windows stop automation. |
| `RELEASE_AUTOPILOT_WEEKENDS` | Off | Set `on` to allow Saturday/Sunday UTC. The day is the current UTC day, including for an overnight window. |

An admin must also verify these existing settings before enabling either policy:

- `RELEASE_BOT_APP_ID` and `RELEASE_BOT_PRIVATE_KEY` are the existing release-please
  App secrets. The App installation needs Contents and Pull requests write on
  this repository. Nothing reads or prints the secret values. The App merges;
  the distinct `GITHUB_TOKEN` actor approves, because an author cannot approve
  its own PR and merges made with `GITHUB_TOKEN` do not start push workflows.
- Actions must be allowed to create and approve PRs. The workflow requests
  Contents, Checks and Statuses read, plus Pull requests and Issues write on
  `GITHUB_TOKEN`. GitHub supplies this token; no new PAT is needed.
- Create the `autopilot-approved` and `autopilot-merged` labels. Missing labels
  stop the policy before any approval. Create `release-hold` for the human stop.
- Keep `Production`'s required reviewers and allowed release tags. Verify
  `production-auto` exists, has **no reviewers**, and permits the intended tag
  refs. GitHub treats environment names case-insensitively; `Production` is the
  existing environment formerly written as `production` in this workflow.
- Keep main's required check and approval protections. The script does not
  bypass protection or enable GitHub auto-merge. Squash merging must be allowed
  for the release-please PR; dev-to-main's separate merge-commit rule is unchanged.
- Restrict release-tag creation to the release App and trusted release managers;
  protect `main` and release tags from force updates/deletion. A tag containing
  a different workflow could omit any in-file guard, so tag creation authority
  must be controlled outside this workflow. The guard verifies the tag's commit
  against the push event and rechecks it after approval, but unchanged downstream
  builders still refer to the tag. Tag authority and immutability are essential
  prerequisites, not guarantees this policy can impose.
- Land the policy on the default branch and on `main` before enabling it. GitHub
  schedules/workflow-run events use default-branch code. The image guard executes
  only policy code checked out from `main`, never policy code supplied by a tag.

No existing image-build secrets, publication settings or component selectors are
changed by this lane.

## Release PR merge policy

`release-autopilot.yml` wakes every 15 minutes, when **Backend CI** completes, or
on a dispatch from the default branch. Scheduled runs can be delayed by GitHub.
A workflow completion is only a wake-up signal; its result and artifacts are not
trusted as evidence. No PR code or artifacts are executed with a write token.

The script requires exactly one open PR targeting `main` from the same-repository
branch `release-please--branches--main`. API metadata must identify the author as
`futureagi-release-bot[bot]` (type Bot), and the title must be exactly
`chore(main): release X.Y.Z`, with a stable version. Drafts, closed/merged PRs,
unknown mergeability, a `release-hold` label, incomplete file lists and changes
outside release metadata all stop automation. The file allowlist follows the
simple release-please config: `CHANGELOG.md`, `.release-please-manifest.json`,
`version.txt`, and `deploy/helm/futureagi/Chart.yaml`; only additions/modifications
are eligible. An intentional release-config change needs a policy update.

Required check runs and legacy statuses are fetched, with pagination, from the
immutable **head SHA** endpoints. Old-SHA results do not count. Missing, queued,
in-progress, failing, cancelled, skipped and neutral required checks do not pass.
The latest attempt for each check name/App and each status context wins; multiple
producers of one required name must all succeed. Unlisted checks are advisory to
this policy; GitHub branch protection can still block the merge independently.

**End-to-end is advisory by default.** The last measured baseline failed 17 of 30
dev runs and both recent dev-to-main PRs, so it is not an honest required gate yet.
Once reliable, an admin can add its exact aggregate check name to
`RELEASE_AUTOPILOT_REQUIRED_CHECKS` and branch protection. A green backend check
is not evidence that E2E passed.

The policy re-reads the PR around each evidence collection and repeats the whole
decision after recording its audit and approval. Approval and audit writes are
idempotent per SHA. If they reset GitHub's `updated_at`, the merge waits another
configured cooldown; later runs reuse the existing approval/comment instead of
resetting the clock forever. A dismissed or changes-requested Actions review on
that head requires human resolution. The final merge uses the release App token
and supplies the exact verified head SHA to GitHub's merge API, so a concurrent
push cannot substitute another commit. The final read cannot atomically lock
labels, check reruns or settings; branch protection and tag protection remain
necessary server-side controls.

## Image publication environment

The existing component/version guard still runs verbatim both before and after
approval. New steps then fetch full history and execute the policy from `main`:

1. Resolve the requested tag to a commit, including annotated tags. Refuse it if
   it is not reachable from `origin/main`. On a push, require that it still
   resolves to the triggering event commit. After an approval wait, require the
   same commit observed by the first guard.
2. Select the highest-version **stable** tag below the requested version that is
   reachable from that commit. Exclude the current release, beta, rc, dev and
   unrelated-history tags. This is the previous stable baseline; a newer tag
   pushed while approval waits cannot become the baseline.
3. Reuse the callout check's added-Python-migration rule, explicitly covering
   `futureagi/*/migrations/*.py`, nested `futureagi/ee/**/migrations/*.py` and
   `ee/**/migrations/*.py`. Exclude only `__init__.py`. Treat renames into these
   directories as additions. Existing migration edits are not *new files*.
4. Choose `production-auto` only when `RELEASE_AUTO_APPROVE=on`, the event is an
   exact tag push, and the count is known to be zero. Otherwise choose
   `Production`. Missing baseline/history/diff means **unknown**, never zero.
   An ancestry failure stops the run altogether, rather than offering approval.
5. Repeat the evidence check after entering the selected environment. If an
   automatic decision is no longer safe, stop; a fresh run is needed to select
   human review. Manual approval never becomes less restrictive in-place.

The policy applies to the supported stable, beta and rc tag-push events; their
existing component/channel restrictions remain unchanged. The hours/weekend
switches limit PR merges, not the image run already triggered by a tag. **Every manual
`workflow_dispatch`, including a bump-only retry, still requires Production
review.** A job summary records the chosen environment, reason, tag SHA,
baseline and migration count. The downstream deployment PR gets an exact line:

```text
Migrations: 0 new files
```

Positive counts retain human approval. An unavailable baseline/diff produces
`Migrations: unknown new files`; downstream automation must treat that as manual.
Already-open bump PRs are not edited on retry, preserving the existing workflow's
behaviour; missing metadata on an older PR must also mean manual. This count is
source evidence from this repository, not proof of the deployed database state
or evidence about independently changed external EE code. The deployment-side
pending-migration check remains mandatory; migrations are never run by this bot.

## Hold, audit and rollout

- To hold a release before merge, add `release-hold` to the release PR. Remove it
  when ready; its update restarts the normal cooldown. To stop the entire PR
  policy, unset `RELEASE_AUTOPILOT` or set it to `off`.
- After merge/tagging, `release-hold` cannot undo the release. Set
  `RELEASE_AUTO_APPROVE=off` for future runs, cancel any already-running release
  that must stop, and retain the deployment repo's separate hold and rollout
  controls. A variable change does not cancel jobs already publishing images.
- The bot leaves a SHA-bound policy comment, an approval review,
  `autopilot-approved`, and a decision summary. A successful merge adds
  `autopilot-merged` and a completion comment linked to the run. An attempted
  approval is not represented as a successful merge. Search the labels to audit
  approvals and completed merges independently. GitHub cannot transact a merge
  and its subsequent audit label together: a post-merge API failure leaves the
  pre-merge audit/review, but an operator must repair the success annotation.
- First merge with both variables unset. Verify labels, App permissions,
  environment protections, default-branch/main availability, required check
  names, and immutable-tag rules. Then enable PR merging during an explicit
  weekday window; verify that the App merge triggers release-please tagging.
  Enable `RELEASE_AUTO_APPROVE` separately after observing its manual-path
  summaries and the deployment safety net. No admin setting is changed by code.

## Verification and unchanged workflow sections

Synthetic tests run with `python3 scripts/test_release_autopilot.py` (stdlib only),
including Git histories, every merge predicate, current-SHA status handling,
transaction rechecks, idempotent audit/approval, and environment decisions. Run
workload tests only in E2B or CI, not on the shared development host. The existing
`Release script checks` job discovers `scripts/test_*.py`; its path filter needs
this new workflow/script/test added for future script-only changes.

The `release-images.yml` diff is deliberately limited:

- Header/approval comments describe the optional review path.
- Guard outputs and new post-guard steps expose ancestry, environment and count.
- `approve.environment` consumes the guard's decision, with new post-guard steps
  revalidating it after the wait. Both original inline guard scripts are unchanged.
- The bump PR body adds only the machine-readable migration line.

All jobs from `build` onward, apart from that body line, are byte-identical to the
base. Image builds, version/channel validation, digest/size checks, SDK/worker
releases, EE/docs dispatches, aliases, retries and bump-PR creation semantics are
not refactored.

Synthetic tests cannot prove GitHub environment review behaviour, App identity
permissions, branch-protection enforcement, schedule delivery, real check names
or SHA attribution, the release-please merge-to-tag trigger, tag immutability,
image publication or downstream deployment. These need CI/admin verification;
no release workflow is dispatched as part of local verification.
