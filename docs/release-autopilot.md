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
unknown mergeability, a `release-hold` label (case-insensitive), malformed label
data, incomplete file lists and changes outside release metadata all stop
automation. The file allowlist follows the simple release-please config:
`CHANGELOG.md`, `.release-please-manifest.json`, `version.txt`, and
`deploy/helm/futureagi/Chart.yaml`; only additions/modifications are eligible.

Paths alone are not approval evidence. The script reads those files **as data**
at the API-reported immutable base and head commit SHAs, using Git trees and
blobs rather than executing a checkout or trusting PR diff patches. Complete,
untruncated trees prove file presence/absence and regular `100644` modes; symlinks,
executable files, submodules and non-directory parents stop automation. Blobs must
be valid nonempty UTF-8 without NULs, at most 2 MB each, with matching size and Git
object hash. The changed-file list must agree with this evidence.

- The manifest must contain exactly the single `.` package, whose stable version
  must advance from the base and equal the PR title. Duplicate JSON keys, extra
  packages, malformed content and inconsistent versions require a person.
- Both base and head charts must parse as safe, duplicate-free YAML without
  aliases. Only the top-level `version` and `appVersion` scalar values may change;
  every other byte must stay identical, including `kubeVersion`, dependencies,
  annotations and comments. Versions must match the corresponding manifest:
  `X.Y.Z` and `vX.Y.Z`. Unsupported layouts stop automation rather than guessing.
- `version.txt`, when present, must contain the corresponding manifest version,
  with at most one trailing newline. Its initial creation is allowed; absence on
  both sides is supported because the current repository has no version file.
- Changelog prose may change. Missing base/head manifest or chart evidence is not
  eligible, even if a PR claims to touch only the changelog.

An intentional release-config or metadata-layout change needs a policy update.
The chart parser is pinned to PyYAML 6.0.3; the image-environment guard remains
stdlib-only and does not parse PR metadata.

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
decision, including contents and case-insensitive holds, after recording its audit
and approval. Policy-v2 approval and audit writes are idempotent per head/base SHA
pair; older policy approvals are not reused as v2 evidence. If the writes reset
GitHub's `updated_at`, the merge waits another
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
   pushed while approval waits cannot become the baseline. The baseline must have
   **shipped**: its `release-images` tag-push run succeeded, or that run finished
   and its `approve` and `backend / publish` jobs both succeeded on some attempt.
   The second case covers a run that failed on a side job, such as the Helm chart
   install check, after the images were published. If the previous tag's run is
   still in progress or waiting for approval, was rejected, or failed before the
   backend image was published, the count is unknown and the release keeps
   human review. Each of the two GitHub reads (the tag's runs, then a run's
   jobs) is tried up to three times, 5 and then 10 seconds apart, when the
   request fails or times out. A read that still fails, or a malformed run or
   job listing, means unknown. A dispatch retry of the previous tag does not
   count, because only tag-push runs are read.
3. Count every added, edited, deleted or renamed migration or schema file since
   that baseline: `futureagi/*/migrations/*.py` and `*.sql`, nested
   `futureagi/ee/**/migrations/`, `ee/**/migrations/`, and the ClickHouse schema
   sources: every `.sql` file under `futureagi/tracer/services/clickhouse/`
   (the live `v2/schema/` directory, `v2/observed_catalog/schema.sql`, the legacy
   `schema/` and `oss_cdc_migrations/`) and the files that apply it (`schema.py`,
   `v2/apply_schema.py`, `v2/apply_schema_rewriter.py`, `v2/schema_topology.py`).
   Query builders and other ClickHouse readers do not count, and neither do
   `__init__.py` files. Hosted pods never apply ClickHouse schema at start, and
   the deployment's `migrate --check` covers Django only, so this count is the
   only automatic stop for a ClickHouse schema change.
4. Choose `production-auto` only when `RELEASE_AUTO_APPROVE=on`, the event is an
   exact tag push by the release App, this is the run's first attempt, and the
   count is known to be zero. "By the release App" means both `GITHUB_ACTOR` and
   `GITHUB_TRIGGERING_ACTOR` are `futureagi-release-bot[bot]`, and "first attempt"
   means `GITHUB_RUN_ATTEMPT` is exactly `1`. GitHub keeps `GITHUB_ACTOR` from the
   first attempt on a re-run, so these two extra variables are what show that a
   person started it. Otherwise choose `Production`. Missing baseline/history/diff
   means **unknown**, never zero. An ancestry failure stops the run altogether,
   rather than offering approval.
5. Repeat the evidence check after entering the selected environment. If an
   automatic decision is no longer safe, stop and write the next step to the job
   summary: use **Re-run all jobs**, which repeats the guard and selects
   `Production` review. Manual approval never becomes less restrictive in-place.

**Re-runs.** A person's **Re-run all jobs** on a release run, old or new, repeats
the guard and now always selects `Production` review, including a retry of the
newest release after a transient guard failure. Re-running only the approve job
of an automatic run stops at the recheck in step 5. Because no re-run can return
to `production-auto`, a GitHub API failure during the guard or the recheck would
cost a Production review; the retries in step 2 make that less likely, and a
read that fails three times still leads to **Re-run all jobs** and review.
Re-running failed build jobs after approve succeeded reuses the earlier guard
and approval and does not ask for review again, as the `release-images.yml`
header says; that route can still rebuild an older release and move `latest`
and the minor alias back, and closing it needs a check in the publish step of
`build-image-multiarch.yml`.

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

- To hold a release before merge, add `release-hold` to the release PR. Displayed
  spellings such as `Release-Hold` and `RELEASE-HOLD` also stop approval and merge.
  Remove it when ready; its update restarts the normal cooldown. To stop the entire PR
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
- Before `RELEASE_AUTO_APPROVE=on`, add a tag ruleset on `v*` so only the release
  App (and named release managers) can create, update or delete release tags,
  and limit the `production-auto` environment to `v*` tags. The in-code actor
  check is defence in depth; the ruleset is the control that matters.
- Do not cut beta/rc tags from dev until this change has reached main: the policy
  runs from main, and both release-images steps stop with a clear error if the
  script is missing there. Keep the script's arguments and outputs backward
  compatible until a change reaches main.
- The merge also requires the `migrations-callout` workflow to have passed on the
  release head (identified by its workflow file, because its job is named
  `check` like other checks), and the release PR to be current with main or main
  to have been quiet for the cooldown. Only runs from the release PR itself
  count: the run's head branch must be `release-please--branches--main` in
  `future-agi/future-agi`. Runs from a fork or another branch at the same commit
  are ignored before the newest run is chosen, so another PR can neither pass
  nor block this gate. GitHub allows one open PR per head branch and base, and
  the callout workflow runs only for PRs to `main`.
- First merge with both variables unset. Verify labels, App permissions,
  environment protections, default-branch/main availability, required check
  names, and immutable-tag rules. Then enable PR merging during an explicit
  weekday window; verify that the App merge triggers release-please tagging.
  Enable `RELEASE_AUTO_APPROVE` separately after observing its manual-path
  summaries and the deployment safety net. No admin setting is changed by code.

## Verification and unchanged workflow sections

Synthetic tests run with `python3 scripts/test_release_autopilot.py` after
installing `PyYAML==6.0.3`, including Git histories, every merge predicate,
case-insensitive holds before/after approval, content/mode/version rejection,
immutable Git API evidence, current-SHA status handling, transaction rechecks,
idempotent audit/approval, and environment decisions. Run workload tests only in
E2B or CI, not on the shared development host.

**CI wiring:** the `Release script checks` workflow
(`.github/workflows/worker-release-checks.yml`) installs `PyYAML==6.0.3` and runs
every `scripts/test_*.py` when `release-images.yml`, `release-autopilot.yml`, this
script or its tests change. Its `paths` filter does not list
`.github/workflows/build-image-multiarch.yml` or
`futureagi/tracer/services/clickhouse/**`. A rename of that workflow's `publish`
job (one of the two jobs that prove a release shipped), or a move of a ClickHouse
schema applier or of `v2/schema/`, is therefore caught only when a listed file
next changes. Adding those two paths to the filter closes the gap.

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
