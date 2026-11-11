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

An admin must also make these settings before enabling either policy. Code
changes none of them.

- **Labels.** Create the labels `autopilot-approved`, `autopilot-merged` and
  `release-hold`; none of them exists yet (read-only check, 11 Nov). A missing
  `autopilot-approved` or `autopilot-merged` label stops the policy before any
  approval. `release-hold` is the human stop.
- **Actions approvals off.** Turn off "Allow GitHub Actions to create and
  approve pull requests" on `future-agi/future-agi`. While it is on, any
  writer's branch workflow can approve that writer's own PR as
  `github-actions[bot]`, so main and dev are not review-gated. The autopilot
  never approves with the workflow token. Turning the setting off stops
  `frontend-auto-approve-hotfix.yml`, which approves with that token. `ee` and
  `agent-learning-kit` also produce deployed images and had the setting on
  (read-only check, 11 Nov); turn it off there too. Their workflows were not
  checked for anything that relies on it.
- **Last-push approval.** Add a repository ruleset on `main` and `dev` with
  `require_last_push_approval: true`. Do not edit organisation ruleset
  2289447, which covers other repositories. Without it, a writer can push to
  someone else's PR branch and then approve that PR. A branch workflow can
  still push onto another person's PR as a bot (workflow token or App token),
  and the writer's own approval then probably counts (inferred, not tested).
  Test that on a scratch repository before relying on this rule; until the
  test shows that it does not count, this setting is not met.
- **Approver App.** The release PR is approved by a dedicated GitHub App, not
  by the workflow token:
  - The owner creates an App used only for autopilot approvals, distinct from
    the release-bot App (a PR's author cannot approve it), and installs it on
    `future-agi/future-agi` only. The low-risk PR autopilot
    (`docs/pr-autopilot.md`) uses the same App. Reusing the deployment
    repository's approver App would let either repository's key approve in
    the other.
  - **Order.** The key is added last. While one writer can still get a PR
    approved without another person, that writer can land a job that names
    `release-autopilot` on `main`, run it and read the key, and a leaked key
    outlives the later settings. So the owner first turns Actions approvals
    off, adds the last-push ruleset, creates the environments with their
    branch rules and completes the release-bot key custody below. Then the
    owner confirms on a throwaway PR into `dev` that GitHub refuses an
    approval by a branch workflow's `GITHUB_TOKEN`, and only then adds the
    approver variable and key. If the key was stored anywhere a branch
    workflow could read before that, generate a new private key, delete the
    old one and store only the new one. `docs/pr-autopilot.md` (Approver App
    and owner settings) numbers these steps.
  - GitHub counts a required approval only from a reviewer with write access.
    A GitHub community report and an independent reproduction say that an App
    installed with only "Pull requests: write" gives approvals that do not
    count, and that the same review counts once the installation also has
    "Contents: write". Install the App with Pull requests: Read and write and
    Contents: Read and write. This is not verified live.
  - The workflow mints the token with only `permission-pull-requests: write`.
    Counting follows the installation's access, not the token's, so adding
    `permission-contents: read` to the token would not make the approval count
    (read access is not write access). The token is used only to post the
    approval.
  - With Contents: write the App key could push to unprotected branches and
    merge PRs. Keep the key only in the main-only environment below, and keep
    the App off every ruleset bypass list.
  - **Live check before `RELEASE_AUTOPILOT=on`.** Mint a token exactly as the
    workflow does. Approve a throwaway PR opened by the release-bot App, or the
    next release PR, with it. `gh pr view <number> --json reviewDecision,mergeStateStatus` must show
    `APPROVED` and `CLEAN`. Do not rely on the merge button: organisation
    admins bypass the ruleset. If `reviewDecision` stays `REVIEW_REQUIRED`,
    grant the installation Contents: write and read the same PR again. If it
    still does not count, the approver mint in `release-autopilot.yml` must
    also request `permission-contents: write`, which is a code change. An
    approval that does not count makes the merge fail with HTTP 405; the
    second refusal of the same head fails the run.
- **Environment `release-autopilot`.** Create it on `future-agi/future-agi`
  with a deployment branch rule that allows only `main`, and with no required
  reviewers or wait timer. Add the variable `AUTOPILOT_APPROVER_CLIENT_ID`
  (the App's client ID) and the secret `AUTOPILOT_APPROVER_PRIVATE_KEY`, plus
  copies of `RELEASE_BOT_APP_ID` and `RELEASE_BOT_PRIVATE_KEY` as environment
  secrets, in the order above. Create the branch rule before adding any
  secret: GitHub creates a missing environment, with no rules, the first time
  a job names it. Only the
  `merge` job names this environment, and it runs only when the `check` job
  finds an eligible release PR, so idle polls read no key and record no
  deployment. Until the copies are added, the job still reads the
  organisation secrets.
- **Environment `pr-autopilot`.** The low-risk PR autopilot
  (`low-risk-pr-autopilot.yml`, see `docs/pr-autopilot.md`) uses the same
  approver variable and secret in its approving job, from an environment named
  `pr-autopilot` whose deployment branch rule allows only `dev`.
- **Release-bot App key custody.** `RELEASE_BOT_APP_ID` and
  `RELEASE_BOT_PRIVATE_KEY` are organisation secrets today, shared with
  deployment, future-agi, docs, agent-learning-kit and ee, and readable by
  any writer's branch workflow there, which could then push or approve as the
  release bot, here and in `future-agi/deployment`. futureagi-sdk holds a
  repository copy, and internal-tools has repository secrets with both names.
  This custody is also a precondition of the deployment repository's deploy
  identity, so it is done before that repository's `deploy_github.tf` is
  applied. The text below matches the header of
  `us/gcp/terraform/deploy_github.tf` and `docs/auto-deploy.md`
  (Administrator setup step 1) in `future-agi/deployment`; where they differ,
  that header is the later text.
  Hold the key only in environment secrets whose deployment branch policy
  admits only refs that one writer cannot create or merge into alone, and
  delete the organisation secret and the repository copies. "Protected
  branches only" is not enough: `fix/nightly-dev-27-05` is protected here with
  one approval and no status checks, and any writer can push a tag at their
  own commit until a tag ruleset exists. A run started by a tag uses the
  workflow files at that commit. Use "Selected branches and tags", make each
  admitted branch need a person's review (Actions approvals off and last-push
  approval on, with the same untested bot-push caveat), and add the tag
  ruleset below before an environment that admits tags gets the key. A job
  that names an environment still reads organisation secrets, so every job
  below names its environment first, and the organisation secret is deleted
  last. Then generate a new private key for the release-bot App and delete the
  old ones, because any branch workflow could read the old key. The other
  remedy, a separate App for deployment bumps under the same custody, is
  described in those deployment documents. A job names one environment, so
  this repository needs three holders:
  - `production-auto`, for `release-worker` in `release-images.yml`, which
    already names it. Restrict `production-auto` (see "`Production` and
    `production-auto`" below) **before** adding the copy; it has no branch
    rule today.
  - `release-autopilot`, for this workflow's `merge` job (above).
  - a third environment, called `release-bot` in these documents, on
    "Selected branches and tags" with `main` and `v*` tags, for
    `bump-deployment`, `dispatch-ee` and `dispatch-docs` in
    `release-images.yml`, plus `release-please.yml`, `sdk-sync.yml` and
    `refresh-disposable-domains.yml`. These jobs name no environment yet (a
    workflow change), so the organisation secrets must stay until they do.

  Other readers are docs `changelog-sync.yml`, agent-learning-kit
  `publish-pypi.yml` (`release-pr`, `deployment-bump`) and futureagi-sdk
  `promote.yml` (on `dev`) and `release-please.yml`; each needs an environment
  of the same kind in its repository.

  The App merges the release PR, because a merge made with the workflow token
  does not start push workflows, so release-please would not tag the release.
  Its installation needs Contents and Pull requests write on this repository.
  Comments and labels stay on the workflow token. Nothing reads or prints a
  secret value.
- **`Production` and `production-auto`.** Keep `Production`'s required
  reviewers and allowed release tags. Verify that `production-auto` exists and
  has **no reviewers**. Set `production-auto` to "Selected branches and tags"
  with `main` and `v*` tags. `main` is needed because `release-worker` runs in
  `production-auto` for the `components=bump` retry, which the guard allows
  from any ref, and the dispatch form defaults to `main`. With a tags-only rule
  GitHub would refuse `release-worker` on a dispatch from `main`, and the bump
  would be skipped. This does not loosen approval: the guard selects
  `production-auto` for `approve` only on an exact tag push by the release App,
  and every dispatch gets `Production` review. GitHub treats environment names
  case-insensitively; `Production` is the existing environment formerly
  written as `production` in this workflow.
- The workflow token gets Contents, Checks, Statuses, Actions and Pull requests
  read in the `check` job, and Pull requests and Issues write in the `merge`
  job, for comments and labels. GitHub supplies this token; no PAT is needed.
- Keep main's required check and approval protections. The script does not
  bypass protection or enable GitHub auto-merge. Squash merging must be allowed
  for the release-please PR; dev-to-main's separate merge-commit rule is unchanged.
- main may require status checks, but must **not** require branches to be up to
  date before merging. release-please does not rebuild the release PR for hidden
  commit types (for example `test:`), so the autopilot merges a release PR that
  is behind main once main has been quiet for the cooldown. Live main has
  `strict: false` today (11 Nov). A strict rule for dev, which the low-risk PR
  autopilot asks for, must go in a dev-only ruleset, not in the organisation
  ruleset that also covers main. If main does become strict and GitHub reports
  the release PR as `behind`, the check step stops before approving, with the
  reason "main requires an up-to-date branch". GitHub may instead report
  `blocked` until the PR has its approval; then the first run approves and
  defers the merge at its recheck, and later runs wait. Either way the release
  waits, with green runs, until a person updates the release branch.
- Restrict release-tag creation to the release App and trusted release managers
  with a tag ruleset on `v*` (which also covers beta and rc tags); protect
  `main` and release tags from force updates/deletion. This ruleset is part of
  the key custody above, so it is added before any environment that admits
  tags gets a key copy. A tag containing
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
The workflow has two jobs. `check` decides with a read-only token and no
secrets. `merge` runs only when `check` found an eligible PR. It names the
`release-autopilot` environment, mints the approver App and release App
tokens, repeats the whole decision and then approves and merges. All actions
are pinned to commit SHAs, with the version in a comment.

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
resetting the clock forever. The autopilot's own reviews are those by the
approver App's login, which the workflow passes in as `<app-slug>[bot]`. The
autopilot never dismisses its own approval. A dismissed or changes-requested
review by that App on the current head therefore waits for a person. Reviews
by any other login, including `github-actions[bot]`, are neither reused nor a
stop. A missing or invalid approver token or login, or a login equal to the
release bot's or `github-actions[bot]`, stops the merge job before any request.
An approval that GitHub records under another login stops the run before the
merge. The final merge uses the release App token and supplies the exact
verified head SHA to GitHub's merge API, so a concurrent push cannot substitute
another commit. The final read cannot atomically lock
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
   Two Python files outside that directory also count, because they hold
   operator-provisioned ClickHouse schema: `futureagi/tracer/services/grouping/feature_store.py`
   (`grouping_feature_ddl()`, the Error Feed grouping tables) and
   `futureagi/tracer/management/commands/provision_grouping_features.py`, which
   applies it. `feature_store.py` also holds queries, so any edit to it selects
   `Production`. Query builders and other ClickHouse readers do not count, and
   neither do `__init__.py` files. Hosted pods never apply ClickHouse schema at
   start, and the deployment's `migrate --check` covers Django only. For the
   files listed here, this count is the only automatic stop. ClickHouse DDL
   added anywhere else is not counted until it is added to the list.
   System-eval seeds (`seed_system_evals`) and operator backfill commands do not
   count either. Hosted pods never run them and the deployment pipeline runs no
   seed, so a seed-only release deploys automatically and its eval content stays
   stale until someone runs the seeder.
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
cost a Production review; the retries in step 2 make that less likely. A read
that fails three times in the guard selects `Production` directly. In the
recheck of an automatic run it stops the approve job, and only **Re-run all
jobs**, which then selects `Production`, moves the release on.
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
baseline and migration count.

**The bump PR's migration line.** The `bump-deployment` job writes the count
into the deployment bump commit, as the commit message's second paragraph, and
into the bump PR body. Both carry exactly one line:

```text
Migrations: 0 new files
```

The number is a non-negative integer, or the word `unknown` when the baseline
or diff is unavailable. The step reads the count from its `env`, not by
expression expansion inside the script, and writes `unknown` for anything that
is not a plain count, never `0`.

The deployment repository's bot autopilot (`docs/bot-pr-autopilot.md` in
`future-agi/deployment`) reads the head commit message of a release-bump PR.
It allows the PR only if exactly one whole line equals
`Migrations: 0 new files`. Any other count, `unknown`, a missing line or
duplicate lines give the waiting reason `release-has-migrations`. That reason
is not a pause, so an earlier autopilot approval is withdrawn. Sandbox-pin PRs
are unaffected. The commit is used rather than the PR body because the
autopilot already ties the head commit to the release-bot key; anyone with
write access can edit a PR body.

- **Older releases.** A tag push runs the workflow file at the tag. Bump PRs
  from releases cut before this change carry no line and therefore wait for a
  person. Every bump PR waits until a release is cut from a tag that contains
  this change.
- **Retries.** A `components=bump` dispatch from `main` (or from any ref that
  contains this change) regenerates the bump branch from deployment `main` and
  force-pushes a new commit, so its commit carries the line with the count the
  retry's guard computed. A dispatch from an older release tag runs that tag's
  workflow and writes no line. An already-open PR's body is
  not edited, as before.
- **For the `Production` approver.** A release with a positive or unknown count
  still publishes its images once approved, but its bump PR then waits. If the
  release changes ClickHouse schema, apply the schema first, then merge the
  bump PR by hand. Django migrations are also stopped at deploy time by the
  deployment's `migrate --check`; the operator then applies them with the
  procedure in `future-agi/deployment`'s `docs/auto-deploy.md`, "Applying
  migrations after a failed check".
- **Older bumps.** While an older release's bump PR that does not clear
  migrations is open, the deployment autopilot keeps it open and newer bumps
  wait at `older-release-has-migrations`. A hold label does not release them;
  a person merges or closes the older PR. The count is taken against the
  previous shipped release, not against the version deployment `main` pins.
- **Cost.** A release whose baseline is unknown but which changes no schema
  also needs a hand merge. About 6 of the last 39 releases had an unknown
  baseline (review measurement, 11 Nov). Holding the bump PR keeps deployment
  `main` deployable for other changes.

This count is source evidence from this repository, not proof of the deployed
database state or evidence about independently changed external EE code. The
deployment-side pending-migration check remains mandatory; migrations are never
run by this bot.

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
  and set the `production-auto` environment to "Selected branches and tags"
  with `main` and `v*` tags (see the prerequisites for why `main`). The
  in-code actor check is defence in
  depth; the ruleset is the control that matters. Also complete the approver
  App, environment and Actions-approval settings above, so that no writer can
  approve a change into main without a person.
- Do not cut beta/rc tags from dev until this change has reached main: the policy
  runs from main, and both release-images steps stop with a clear error if the
  script is missing there. Keep the script's arguments and outputs backward
  compatible until a change reaches main.
- The merge also requires the `migrations-callout` workflow to have passed on the
  release head (identified by its workflow file, because its job is named
  `check` like other checks), and the release PR to be current with main or main
  to have been quiet for the cooldown. A behind PR merges on checks that ran
  against an older main; the tag-time migration count still selects Production
  review for what main added. If GitHub reports the PR as `behind` (main
  requires up-to-date branches), the run waits: before approving when the
  check step sees it, or at the recheck after approving when GitHub reported
  `blocked` first. If GitHub still refuses the merge (HTTP 405 or 422), the
  run records the refused
  head in a PR comment, keeps the approval and ends green; the next eligible run
  retries once, and a second refusal of the same head fails the run. Only runs
  from the release PR itself count: the run's head branch must be
  `release-please--branches--main` in
  `future-agi/future-agi`. Runs from a fork or another branch at the same commit
  are ignored before the newest run is chosen, so another PR can neither pass
  nor block this gate. GitHub allows one open PR per head branch and base, and
  the callout workflow runs only for PRs to `main`.
- First merge with both variables unset. Verify labels, App permissions, the
  approver App live check, environment protections, default-branch/main
  availability, required check names, and immutable-tag rules. Then enable PR merging during an explicit
  weekday window; verify that the App merge triggers release-please tagging.
  Enable `RELEASE_AUTO_APPROVE` separately after observing its manual-path
  summaries and the deployment safety net. No admin setting is changed by code.

## Verification and unchanged workflow sections

Synthetic tests run with `python3 scripts/test_release_autopilot.py` after
installing `PyYAML==6.0.3`, including Git histories, every merge predicate,
case-insensitive holds before/after approval, content/mode/version rejection,
immutable Git API evidence, current-SHA status handling, transaction rechecks,
idempotent audit/approval, approver App identity, refused merges, environment
decisions, the workflow's job split and action pins, and the bump step itself:
its script runs against stub `git` and `gh` commands to check the commit
message and PR body it would write. Run workload tests only in E2B or CI, not
on the shared development host.

**CI wiring:** the `Release script checks` workflow
(`.github/workflows/worker-release-checks.yml`) installs `PyYAML==6.0.3` and runs
every `scripts/test_*.py` when `release-images.yml`, `release-autopilot.yml`, this
script or its tests change. Its `paths` filter does not list
`.github/workflows/build-image-multiarch.yml`,
`futureagi/tracer/services/clickhouse/**` or the two grouping schema files. A
rename of that workflow's `publish` job (one of the two jobs that prove a release
shipped), or a move of a ClickHouse schema applier, of `v2/schema/` or of a
grouping schema file, is therefore caught only when a listed file next changes.
Adding those paths to the filter closes the gap.

The `release-images.yml` diff is deliberately limited:

- Header/approval comments describe the optional review path.
- Guard outputs and new post-guard steps expose ancestry, environment and count.
- `approve.environment` consumes the guard's decision, with new post-guard steps
  revalidating it after the wait. Both original inline guard scripts are unchanged.
- The `Open bump PR` step reads the count from its `env`, writes `unknown` for
  anything that is not a plain count, and adds the migration line to the bump
  commit message (second paragraph) and to the PR body.

All jobs from `build` onward, apart from that step, are byte-identical to the
base. Image builds, version/channel validation, digest/size checks, SDK/worker
releases, EE/docs dispatches, aliases, retries and bump-PR creation semantics are
not refactored.

Synthetic tests cannot prove GitHub environment review behaviour, App identity
permissions, branch-protection enforcement, schedule delivery, real check names
or SHA attribution, the release-please merge-to-tag trigger, tag immutability,
image publication or downstream deployment. These need CI/admin verification;
no release workflow is dispatched as part of local verification.
