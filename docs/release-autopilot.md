# Release autopilot

Two independent, opt-in policies remove the routine release-PR merge and image
publication approval. Neither policy deploys a cluster. Both switches ship off.
They are repository variables, so any member with the Write role can turn
them on or off (see "Who can change the switches"). The main-ancestry guard is unconditional: even a manually approved release must
come from reviewed `main` history.

Four `future-agi/deployment` files this document cites are on open PRs, not
on deployment `main` (read-only contents reads, 12 Nov):
`us/gcp/terraform/deploy_github.tf` on #434, `docs/auto-deploy.md` and
`scripts/deploy/migration_check_job.py` on #437 (stacked on #435), and
`docs/bot-pr-autopilot.md` on #438. What they describe does not run until
those PRs merge and are applied or turned on.

## Switches and prerequisites

All variables below are **repository variables**, not environment variables set
in the `Production` environment. The guard runs before entering an environment.
Only the exact value `on` enables a switch.

| Variable | Unset default | Value / meaning |
| --- | --- | --- |
| `RELEASE_AUTOPILOT` | Off | Set `on` to approve and merge eligible release-please PRs. |
| `RELEASE_AUTO_APPROVE` | Off | Set `on` to let migration-free tag pushes use `production-auto` instead of `Production`. Independent of PR auto-merge. |
| `RELEASE_AUTOPILOT_REQUIRED_CHECKS` | `Backend Tests Pass`, `E2E Tests Pass` | Comma- or newline-separated exact check/status names, added to that fixed minimum (they can never remove one). Every name must succeed on the current PR head SHA. An explicitly nonempty but name-free list is invalid, not an empty gate. A name that no workflow reports, for example a misspelling, makes every release wait with the "no result" reason below. |
| `RELEASE_AUTOPILOT_MIN_AGE_MINUTES` | `30` | Non-negative integer minutes since the PR's last update (0–999999). The exact threshold is inclusive; malformed or future timestamps stop the merge. |
| `AUTOPILOT_HOURS_UTC` | No time-of-day restriction | Optional `HH-HH`, start inclusive and end exclusive, e.g. `04-15`. Overnight windows such as `22-06` work; `00-24` is all day. Equal endpoints and malformed windows stop automation. The low-risk PR autopilot (`docs/pr-autopilot.md`) reads the same repository variable, so a change applies to both. That autopilot accepts only a same-day window on weekdays, so an overnight window set here stops it. |
| `RELEASE_AUTOPILOT_WEEKENDS` | Off | Set `on` to allow Saturday/Sunday UTC. The day is the current UTC day, including for an overnight window. |

**Who can change the switches.** Any member with the Write role can create,
change or delete repository variables, in the UI or by REST (GitHub's
documentation, not tested). So every variable in the table is an operating
switch, not an owner-only gate. One writer can turn either policy on, set
`RELEASE_AUTOPILOT_MIN_AGE_MINUTES` to `0`, widen the hours window, allow
weekends, or undo a pause set with these variables. This allows the
following:

- With `RELEASE_AUTO_APPROVE=on`, the next migration-free tag that the
  release App pushes on main publishes images in `production-auto`, without
  the Production reviewers. This needs no other owner setup: `production-auto`
  exists and has no reviewers. Before this PR, every publication waited for
  Production review.
- With `RELEASE_AUTOPILOT=on`, once the owner has added the approver key and
  the labels, the next eligible release PR is approved and merged. One writer
  can already approve and merge that PR by hand, so this adds little.
- What ships is still reviewed `main` code. The tag must be on main and its
  release must add no migration files, and the PR policy merges only the four
  release-metadata files.

The `check` job of `release-autopilot.yml` and the `guard` job of
`release-images.yml` name no environment, so they read the repository value.
The `merge` job and the `approve` recheck name an environment, so a value set
on that environment takes precedence there. GitHub's pages disagree on
whether a writer can edit an environment variable, and the scratch-repository
test in the `deploy_github.tf` header of `future-agi/deployment` decides it.
Until that test shows a writer cannot, an environment copy is not an
owner-only stop. A switch that only reviewed changes can flip would be a
value in a file on main, read from the main checkout both workflows already
use. That needs a code change and is not part of this PR.

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
  (read-only check, 11 Nov); turn it off there too, and add a repository
  ruleset on each one's `main` with `require_last_push_approval: true`. On
  11 Nov neither had a workflow-token approval (default branch and dev), nor
  did docs, and their jobs that open PRs use the App token. futureagi-sdk is
  different. The key custody below needs the setting off there too, and its
  `release-please.yml` approves the generated version PR with the workflow
  token (`release_guard.py approve`). With the setting off, that step fails on
  every run while a version PR is open, and the PR is never queued for merge.
  Leaving the setting on there is not an option, because it reopens the path
  the custody closes. So that workflow changes first, in a reviewed PR: it
  approves with a dedicated approver App whose key sits in a main-only
  environment, or it accepts a person's approval and drops the workflow-token
  approval step (`docs/auto-deploy.md`, Administrator setup step 1, in
  `future-agi/deployment`).
- **Last-push approval.** Add a repository ruleset on `main` and `dev` with
  `require_last_push_approval: true`. Do not edit organisation ruleset
  2289447, which covers other repositories. Without it, a writer can push to
  someone else's PR branch and then approve that PR. A branch workflow can
  still push onto another person's PR as a bot, and the writer's own approval
  then probably counts (inferred, not tested). The deployment repository's
  scratch-repository test covers two workflow-token pushes: a direct push, and
  a workflow that merges another branch into the PR branch. Until it shows
  that the writer's approval does not count after either, this setting is not
  met. A push with an App token is expected to let the writer's approval
  count, so for App keys the key custody below is the control.
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
    branch rules, completes the release-bot key custody below and finishes
    the whole one-time baseline in the `deploy_github.tf` header, so that
    its dismissals never remove an approval this App made. Then the owner
    confirms on a throwaway PR into `dev` that GitHub refuses an
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
  that header is the later text. The custody is not limited to this key. No
  credential that a branch workflow can read may have Contents, Pull
  requests, Administration, Actions or Environments write on
  `future-agi/deployment`, or on
  a branch or tag that a key environment admits, such as this repository's
  `main` once it holds the key. That covers personal access tokens (classic
  and fine-grained), OAuth tokens copied from a tool, App keys and deploy
  keys. Those documents name eight secrets that look like GitHub credentials
  and that no document classifies, `ORG_READ_TOKEN` and `EE_REPO_READ_TOKEN`
  here among them, and three checks: fine-grained Active tokens, the
  organisation's restriction on classic tokens, and each App installation's
  repositories. The one-time baseline in the `deploy_github.tf` header runs
  here too, on every unmerged PR, open or closed: bot approvals are
  dismissed, and each PR whose final head a bot pushed is reviewed in full
  by a person who did not author, push to or approve it, or closed. On
  12 Nov that flagged eleven open PRs into `dev` (fixibot and the release
  bot) and eight fork heads pushed by `cursor[bot]` or
  `github-actions[bot]`; the header lists them. The header moves the key in
  six steps, in future-agi, deployment, docs, agent-learning-kit and
  futureagi-sdk:
  1. review rules on every admitted and fixed-ref branch (Actions approvals
     off and the last-push ruleset);
  2. the environments with their rules and no key, the `v*` tag ruleset,
     the tag sweep, and each key job naming its environment. The sweep
     deletes each `v*` tag whose commit is not on `main`, or cuts it again
     under a name that no key environment admits. On 12 Nov that was
     `v1.1.4-dev`, `v1.1.5-dev`, `v1.31.1-pr2381-ci.0419d696f` and
     `v1.31.1-pr2400.4feedeacf5`;
  3. delete the organisation secret and every repository copy. Generate the
     new private key first and hold it outside GitHub, because GitHub
     refuses to delete an App's only key, then delete every old key. Finish
     the credential and Apps checks;
  4. at least one hour after the last old key was deleted, dismiss bot
     approvals and flag bot-pushed heads, the baseline's first two checks,
     on the PRs listed after that hour. No release-bot push from step 3 to
     step 6 is exempt from the flag;
  5. the one-time read: a person who authored, pushed or approved none of
     the commits that touched them reads the key jobs' workflow files and
     scripts once, in full, at each branch the jobs run from and at each
     `v*` tag that stays, and records each tip SHA. In deployment this step
     is the history check's tree read and first range. Then the header's
     re-listing: every `v*` tag again, and each must point at a commit on
     `main`. Tags are compared with step 2's listing, which holds only the
     tags kept after the sweep. A moved tag is put back at its step-2
     commit; that tag push starts `release-images.yml` on the tag with
     every component, and its runs are cancelled and deleted, not approved
     in Production. An administrator who pushes as themselves, not through
     the release App, does not get `RELEASE_AUTO_APPROVE`'s
     `production-auto` (`scripts/release_autopilot.py:832-833`). Any other
     tag is deleted unless its commit is on `main` and its `.github` and
     listed paths match main's recorded tip, or are read. Then the runs of
     the last 30 days on refs that a key or approver environment admits.
     Each run is compared only on its own workflow file, what that file
     reaches through `uses: ./` (at the base, and at the run's own version where
     the workflow file differs) and the paths the one-time read lists for
     it; those files must match what was read, or their versions are read,
     or the run is deleted. The run listing is sliced so that each slice
     returns all its runs, because the endpoint stops at 1,000 without an
     error, and it keeps `pull_request_target` runs, which run main's
     workflow file whatever branch they report. The merged-PR runs,
     `pull_request` runs created at or after their PR was merged, have the
     branch it merged into as their ref and are deleted;
  6. the new key goes into the environments.

  Release jobs that use the key, the release autopilot's `merge` job
  included, stop working from step 3 to step 6, which includes that hour.
  The dismissals come after the hour because the old key can post a new
  approval until it is deleted, and an installation token minted from it
  can for up to an hour after that (GitHub documents a one-hour token
  lifetime and does not list key deletion among the ways a token ends;
  documented by omission, not tested). The rest of the baseline runs last
  before the deployment apply.
  The header lists the files to read and the listing check for modules
  and bytecode that could shadow the ones these scripts import. Where this
  text and the header differ, follow the header.
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
  below names its environment before the organisation secret is deleted
  (steps 2 and 3 above). The other
  remedy, a separate App for deployment bumps under the same custody, is
  described in those deployment documents. The separate App covers only
  `future-agi/deployment`. This repository still needs the move, because the
  shared App can approve here. Do it before any approver key is added. A job
  names one environment, so this repository needs three holders:
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

  A job that reads the key must run code only from refs that need a
  person's review. It never checks out and runs a ref that a dispatcher
  chooses. Three jobs run code from another branch at a fixed ref, so the
  custody also depends on that branch:
  - `sdk-sync.yml` runs `sdkgen/*.py` from futureagi-sdk `dev`. Its
    `sdk-base` input accepts only `dev`, and the first step checks it before
    any token or checkout. A dispatch runs the workflow file of the ref it
    names, and `release-bot` admits `main`. So this check must reach `main`
    with or before the change that makes `sdk-sync.yml` name `release-bot`,
    and before that environment gets a key copy, never after. The one
    dispatch so far (run 41805312254, 4 Nov)
    used `feat/sdk-docs-reference`; that value now fails the run. To sync a
    feature branch, run `sdkgen/sync_contract.py` and `sdkgen/generate.py` on
    that branch locally. The job also reads the contract
    (`api_contracts/openapi/swagger.json`) only from the release tag. It
    checks out `refs/tags/<version>`, not the bare version: for a bare name,
    actions/checkout takes a branch of that name before the tag, and any
    writer can create a branch named `v1.59.0`. A step then checks, before
    any token is minted, that `refs/tags/<version>` names a commit and that
    the checkout is that commit, detached. The SDK checkout names
    `refs/heads/dev`, and a step checks that it is on that branch at the
    fetched commit. A `workflow_run` start also requires the release-images
    run to come from this repository. The contract is data, not code, but
    sdkgen writes its strings into the generated SDK source, so its origin
    matters. Optionally, a branch ruleset that blocks creating branches
    named `v[0-9]*` removes look-alike branches altogether. Each sync pull
    request waits for a person's approval: no futureagi-sdk workflow
    approves it, and the job only arms auto-merge. futureagi-sdk `dev`
    needs one approval from the organisation ruleset today. But Actions
    approvals are on there and last-push approval is off (read-only check,
    12 Nov), so one writer can still merge into it alone. Turn Actions approvals off and add a
    last-push ruleset on futureagi-sdk `dev` too, after the
    `release-please.yml` change described under "Actions approvals off".
  - `refresh-disposable-domains.yml` runs `scripts/generate_disposable_domains.py`
    from this repository's `dev`, which the last-push ruleset above covers.
  - `bump-deployment` in `release-images.yml` checks out deployment `main`
    with a minted release-bot token persisted (contents and pull-request
    write on `future-agi/deployment`), then runs `python3 -I -m pip` and
    that branch's `tests/test-release-digest-bump.py` (also with `-I`, see
    "Python calls in key-holding jobs" below). Deployment `main` needs the
    same review: Actions approvals off and a last-push ruleset, which its
    `deploy_github.tf` header already requires.

  Other readers are docs `changelog-sync.yml`, agent-learning-kit
  `publish-pypi.yml` (`release-pr`, `deployment-bump`) and futureagi-sdk
  `promote.yml` (on `dev`), `release-please.yml` and `publish.yml` job `docs`
  (on `main` once futureagi-sdk PR #50 merges); each needs an environment of
  the same kind in its repository.

  The App merges the release PR, because a merge made with the workflow token
  does not start push workflows, so release-please would not tag the release.
  Its installation needs Contents and Pull requests write on this repository.
  Comments and labels stay on the workflow token. Nothing reads or prints a
  secret value.
- **ee migrations.** This is not a gate on `RELEASE_AUTOPILOT` or
  `RELEASE_AUTO_APPROVE`. The bump's `Migrations: 0 new files` counts
  future-agi only, not the `future-agi/ee` repository that the hosted backend
  image is built from. The only automatic stop for an ee migration is the
  deployment pipeline's migration check. That check exists only once
  `future-agi/deployment` PRs #435 and #437 (stacked on #435) are on
  deployment `main` and the pipeline is turned on through the Administrator
  setup in that repository's `docs/auto-deploy.md`. Until then, the person who
  deploys must not read `Migrations: 0 new files` as covering ee, and checks
  for unapplied migrations by hand in both regions. The EU apply document
  (`eu/gcp/APPLY_UPDATES.md`) has no such step today. See "What neither count
  sees" below.
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
- **Worker release ref.** `release-worker` passes the repository variable
  `OMEGA_WORKER_RELEASE_REF` (today `main`) to
  `scripts/release-error-feed-worker.py`. The script resolves it to a commit
  of `future-agi/omega-error-feed-worker` and dispatches that repository's
  image build, and the deployment bump pins the resulting digest. So whoever
  can change the variable chooses the code of the next Error Feed worker
  image. Any member with the Write role can change repository variables
  (GitHub's documentation, not tested; the `deploy_github.tf` header in
  `future-agi/deployment`). Set the same variable, with value `main`, on the
  `production-auto` environment, which `release-worker` names: an
  environment variable takes precedence over a repository variable of the
  same name. GitHub's pages disagree on whether a Write-role member can edit
  an environment variable; the scratch-repository test in that header records
  it. If a writer can, the environment copy does not help, and only a code
  change closes the gap: read the ref from a fixed value instead of a
  variable, or have the worker repository's `release-image.yml` refuse a
  commit that is not on its `main`. That workflow builds whatever commit it
  is sent today.
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
  `strict: false` today (12 Nov). The low-risk PR autopilot does not need a
  strict rule on dev either (`docs/pr-autopilot.md`). Any strict rule added
  for another reason must target dev only, not the organisation ruleset that
  also covers main. If main does become strict and GitHub reports
  the release PR as `behind`, the check step stops before approving, with the
  reason "main requires an up-to-date branch". GitHub may instead report
  `blocked` until the PR has its approval; then the first run approves and
  defers the merge at its recheck, and later runs wait. Either way the release
  waits, with green runs. Updating the release branch does not release it: the
  update is a person's push, so the autopilot leaves that release to a person,
  who merges it by hand (see "Who pushed" below). Remove the rule instead.
- **Release branch ruleset.** Add a repository ruleset on
  `release-please--branches--main` that restricts creations and updates, with
  only the release App on its bypass list. Do not restrict deletion: GitHub
  deletes the branch after each merge (`delete_branch_on_merge` is on), and
  the deletion is recorded under the person or App that merged. The branch has
  no rule today (read-only check, 11 Nov), so any writer can push to it. Until
  the ruleset exists, the autopilot's push check below is the only stop.
- Add a tag ruleset on `v*` (which also covers beta and rc tags). Only
  administrators and the release App (bypass list) can create, update or
  delete `v*` tags. Beta and rc tags are then cut by an administrator or by the
  App, and only at a commit on `main`, never from `dev`. A run on a tag uses
  the tag's own workflow files, and `production-auto` and `release-bot` admit
  `v*` tags with the key, so a beta tag at a `dev` commit would run `dev`'s
  workflow files with the key. Step 1 of "Image publication environment"
  below refuses any tag, prereleases included, whose commit is not on `main`;
  the ruleset and the sweep cover tags that never reach that step. A preview tag at a PR head uses
  a name that no key environment admits. A ruleset does not remove tags that
  exist already, so the tag sweep above runs after it is active.
  future-agi had no beta or rc tag on 11 Nov (read-only tag list). In
  all 63 push runs of `release-images.yml` (28 Jul to 3 Nov), people pushed
  two stable tags (`v1.29.0` and `v1.38.1`) and the release App pushed the
  rest; whether those people are administrators was not checked. Also protect
  `main` from force updates and deletion. This ruleset is part of
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

`release-autopilot.yml` wakes every 15 minutes, when **Backend CI** or **E2E CI**
completes, or on a dispatch from the default branch. These two workflows produce
the two required checks. Scheduled runs can be delayed by GitHub.
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
The PR must have exactly one commit. Each of the 15 release PRs merged from
4 Oct to 3 Nov had one (read-only check, 11 Nov).

**Who pushed.** Who opened the PR is not enough: a writer's commit on the
release branch could set any higher version. The script reads the branch's
activity feed (`GET /repos/future-agi/future-agi/activity`, newest first) and
requires all of the following:

- The newest row is a push of the PR's head commit. Until the feed shows it,
  the release waits.
- The branch's most recent `branch_creation` row is among the newest 100 rows.
- That row and every later row have the actor `futureagi-release-bot[bot]`, of
  type Bot.

Rows before that creation belong to the previous release PR and do not count.
The branch name is reused for every release, and the person who merged the
previous PR shows up as the one who deleted the branch. A person's push,
including **Update branch**, makes the autopilot leave that release to a
person. GitHub shows the **Update branch** button on a release PR only when
main requires up-to-date branches or the repository setting "Always suggest
updating pull request branches" is on. Both were off on 12 Nov (read-only
check); keep the setting off (`docs/pr-autopilot.md`, settings table). This holds even after release-please force-pushes over it, because a
force-push does not start a new branch lifetime. The endpoint pages by cursor
and ignores page numbers, so the script reads one page. A lifetime longer
than 100 rows also waits for a person. On 11 Nov the live feed held 29
branch lifetimes since 17 Sep, of 2 to 4 rows each, all created and pushed by
the release App. Replaying the rule on it allows each of the last 15 release
PRs at its merged head. A missing or null actor, a row for another ref and a
malformed response also wait. The read uses the workflow token's Contents read
permission. GitHub's permission tables for Apps ("Permissions required for
GitHub Apps", section Contents) list `GET /repos/{owner}/{repo}/activity` as
read access for installation tokens, and the workflow token is one. No run
has made this read live yet; a refused read fails the run red.

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

**End-to-end is required.** Since 12 Nov 2026, branch protection on main
requires both "Backend Tests Pass" and "E2E Tests Pass", and dev also requires
"Python Lint Pass". So the policy always requires both, whatever
`RELEASE_AUTOPILOT_REQUIRED_CHECKS` says; that variable can only add names. A
green backend check is not evidence that E2E passed.

**Every release PR runs the full E2E suite.** release-please changes
`deploy/helm/futureagi/Chart.yaml`, which matches `deploy/**` in the `any` path
filter of `e2e-ci.yml`, so `e2e (distributed)` runs on every release head. On
the five release heads from v1.55.0 to v1.58.0 (27 Oct to 3 Nov) it ran for 44
to 67 minutes (read-only check, 12 Nov). A reviewer's read-only count found it
ran on all 16 release heads from #3256 to #3720, and "E2E Tests Pass" failed
on the final head of 3 of them. The suite starts again after each
release-please force-push. So a release PR waits about an hour for E2E, and
then the cooldown, before it can merge.

The suite also runs futureagi-sdk's live tests. The SDK ref is the
futureagi-sdk branch named like the PR's head branch, if one exists, else the
base branch (`main`). futureagi-sdk has had its own `release-please--branches--main`
since 4 Nov, and does not delete it after a merge. So release heads now test
against that SDK branch, not futureagi-sdk `main`. This is read from the
resolver code and the branch list (12 Nov); no release head has run since. On v1.57.0 and v1.58.0
(2 and 3 Nov) that branch did not exist yet. The suite used futureagi-sdk
`main`, which then lacked `sdkgen/local_api_key.py`, and "E2E Tests Pass"
failed for a reason no re-run could clear. Dev's `e2e-ci.yml` keeps the same
order but skips a ref that lacks the live-test files.

**When a required check fails, the release waits.** The autopilot does not
retry, comment or fail. Its run stays green and the step summary says
"required check did not succeed on this head", with the check's name. While a
check is queued or running, it says "required checks have not finished on this
head". When a required name has no check run or status on the head at all, it
says "required checks have no result on this head yet", with the name. That
happens while the workflow runs, because an aggregate check such as "E2E Tests
Pass" is created only when the jobs it needs finish. It also happens during a
re-run, because the latest-attempt list hides the old red result until the new
one is created. A name that no workflow reports, such as a misspelled entry in
`RELEASE_AUTOPILOT_REQUIRED_CHECKS`, gives the same reason and never clears.
If it stays after the head's workflows have finished, check the name. A person
then does one of these:

- re-runs the failed jobs; the re-run's completion wakes the autopilot,
  and once it passes, a later poll merges;
- fixes the cause, for example in futureagi-sdk, and re-runs;
- or an administrator merges by hand. Main's protection does not bind
  administrators; the release App cannot merge past a red required check.

**Update branch** is not a remedy. It is a person's push, so the autopilot
then leaves that release to a person (see "Who pushed").

The policy re-reads the PR around each evidence collection and repeats the whole
decision, including contents, the push check and case-insensitive holds, after
recording its audit and approval. If the PR changes between those reads, for
example while GitHub recomputes mergeability after a push to main, the run
fails red and the next poll decides again. Policy-v2 approval and audit writes
are idempotent per head/base SHA pair; older policy approvals are not reused as
v2 evidence. If the writes reset
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
   it is not reachable from `refs/remotes/origin/main`. Prerelease tags too:
   a tag at a `dev` commit would run `dev`'s workflow files with the key. The
   check reads the remote-tracking ref by its full name. Git reads a short name such as
   `origin/main` as a tag first, and any writer can push a tag named
   `origin/main`. On a push, require that the tag still resolves to the
   triggering event commit. After an approval wait, require the same commit
   observed by the first guard.
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
   `futureagi/ee/**/migrations/`, `ee/**/migrations/` (a top-level `ee/` folder in
   this repository; none exists today, and it is not the `future-agi/ee`
   repository), and the ClickHouse schema
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
   seed. So once that pipeline is on, a seed-only release deploys automatically
   and its eval content stays stale until someone runs the seeder.
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
review.** A job summary records the chosen environment, reason, tag SHA, the
previous release and the count since it. That count only picks the
environment; the bump PR carries its own count, described next.

**The bump PR's migration lines.** The `bump-deployment` job counts again,
from the release deployment `main` runs, and writes two lines into the
deployment bump commit and the bump PR body:

```text
Migrations: 0 new files

Migration baseline: v1.58.0
```

- **Baseline.** Before it rewrites any tag, the step "Count migrations since
  the release deployment main runs" reads the 12 image tags in the job's
  `COMMON_PATHS` list from both `eu/gcp/deployment/values.yaml` and
  `us/gcp/deployment/values.yaml` of the deployment checkout. If all 24 are
  the same plain `vX.Y.Z`, that is the baseline. Otherwise the baseline is
  `unknown`. Only those tags are read. None is printed unless all of them
  agree. The backend and collector have no tag there; they are pinned by
  digest.
- **Count.** `release_autopilot.py migrations-since --from <baseline> --to
  <version>` counts migration and schema files from the baseline tag to the
  release tag, with the rules in step 3 above. It prints the count or
  `unknown`. The count is `unknown` when the baseline is unknown, either tag is
  missing from this repository, the baseline is not lower than the release, or
  the baseline is not an ancestor of the release. The job's checkout of this
  repository fetches all history and tags but no file contents
  (`fetch-depth: 0`, `filter: blob:none`). The count reads only file names
  from trees.
- **Commit and PR body.** The count is the commit message's second paragraph
  and the baseline is the third. The PR body carries the same two lines. The
  `Open bump PR` step reads both from its `env`, never by expression expansion
  inside the script. It writes `unknown` for anything that is not a plain
  count or a plain `vX.Y.Z` tag, never `0`.
- **The guard's count is not used here.** The guard counts since the previous
  shipped release, and that count only picks the approval environment. The
  bump's count starts at what deployment `main` runs. So a release whose bump
  never opened, was closed or has not merged yet is counted in the next bump.

The deployment repository's bot autopilot (`docs/bot-pr-autopilot.md` in
`future-agi/deployment`) reads the head commit message of a release-bump PR.
It allows the PR only if exactly one whole line equals
`Migrations: 0 new files`; otherwise it waits at `release-has-migrations`. It
also requires exactly one whole `Migration baseline: vX.Y.Z` line, at least
one changed `*.image.tag` in every changed values file, and that every
changed `*.image.tag` was that version before the bump; otherwise it waits at
`migration-baseline-mismatch`. A tag set by hand on deployment `main` makes
the baseline `unknown`, so that bump waits for a person. Sandbox-pin PRs are
unaffected. The commit is used rather than the PR body because the autopilot
already ties the head commit to the release-bot key; anyone with write access
can edit a PR body. How it treats several open bumps is described there.

On real tags, the count from each release to the next, v1.47.1 to v1.58.0, is
4, 1, 2, 5, 2, 6, 0, 1, 0, 1, 3 and 2 (read-only, 11 Nov). From v1.55.0 to
v1.58.0 in one step it is 6.

- **Older releases.** A tag push runs the workflow file at the tag. Bump PRs
  from releases cut before this change carry no lines and therefore wait for
  a person. Every bump PR waits until a release is cut from a tag that
  contains this change.
- **Retries.** A `components=bump` dispatch from `main` (or from any ref that
  contains this change) regenerates the bump branch from deployment `main`,
  reads the baseline from it again and counts again. It force-pushes a new
  commit with both lines. A dispatch from an older release tag runs that
  tag's workflow and writes no lines. An already-open PR's body is not
  edited, as before.
- **For the `Production` approver.** A release with a positive or unknown
  count still publishes its images once approved, but its bump PR then waits.
  If the release changes ClickHouse schema, apply the schema first, then merge
  the bump PR by hand. Django migrations are applied by the person who deploys.
  The deployment pipeline's `migrate --check`, which would also stop them at
  deploy time, is not on deployment `main` yet (see the ee section below).
  Once it is, the operator applies them with the procedure in
  `future-agi/deployment`'s `docs/auto-deploy.md`, "Applying migrations after
  a failed check".

This count is source evidence from this repository, not proof of the deployed
database state or evidence about independently changed external EE code. A
deploy-time pending-migration check is still needed, and it is not on deployment
`main` yet; migrations are never run by this bot.

**What neither count sees: the `future-agi/ee` repository.** The hosted
backend image is `futureagi/future-agi-ee`. ee's `release-image.yml` builds it
from ee `main` when `dispatch-ee` sends the `platform-release` event. ee has its
own Django app with migrations (`control_plane/migrations/`), installed when
`CLOUD_DEPLOYMENT` names a cloud region (`futureagi/tfc/settings/settings.py`).
Neither the guard's count nor the bump's count includes them. So a release
with only an ee migration can use `production-auto`, and its bump says
`Migrations: 0 new files`. The bump job cannot count them reliably:

- `dispatch-ee` sends only the version. ee builds whatever its `main` is when
  its run starts.
- The image records no source commit. `Dockerfile.ee` sets only the
  `base.name` and `source` labels.
- ee pushes its git tag after the image, and keeps an existing tag when a
  re-dispatch rebuilds the image. So the tag can be missing at bump time, or
  older than the image.
- The bump job's token covers `future-agi/deployment` only.

Writing `unknown` on every bump would send every release to a person, so the
bump does not do that.

**Today nothing automated stops an ee-only migration.** The deployment
pipeline's migration check would, but it is not on deployment `main`. It
arrives with `future-agi/deployment` PR #437, which is open, targets PR #435's
branch (`feat/deploy-pr-checks`) rather than `main`, and ships off. On 12 Nov
`scripts/deploy/migration_check_job.py` was absent from deployment `main` and
from `feat/deploy-pr-checks`, and present on `feat/deploy-pipeline` (read-only
contents reads). By that branch's `docs/auto-deploy.md`, the check runs only
when the repository variable `AUTO_DEPLOY` is `apply`; `plan` runs no
migration Job (read from the branch, not run). Turning the pipeline on has
its own Administrator setup in that document. Until #435 and #437 are on
deployment `main` and the pipeline is on:

- `Migrations: 0 new files` covers future-agi only.
- The person who deploys is the only stop for an ee migration. The US runbook
  (`docs/us-gcp-deploy-new-images-runbook.md` in `future-agi/deployment`,
  section 4) has that person list unapplied migrations with `python manage.py
  showmigrations`, which includes ee's app. `eu/gcp/APPLY_UPDATES.md` has no
  such step (read-only, 12 Nov).

Once both PRs are on `main` and the pipeline is on, it runs
`python manage.py migrate --check --noinput` before any upgrade, from the
candidate backend image with the backend's environment (read from the branch's
code, not run). It sees every installed Django app, ee's included, and stops
the first region at `migration-check-failed` (pending migrations exit 1, so
the line reads `migration-check-failed; exit 1`). A check that never ran or
did not finish, or whose Job the cluster API did not return three times in a
row (`migration-check-did-not-run`, `migration-check-did-not-finish`,
`migration-check-timeout` or `migration-check-read-failed`), is an
infrastructure failure, not a sign of
migrations; a person re-runs the failed job without the migrate procedure
(`docs/auto-deploy.md`, "Applying migrations after a failed check"). A
third-party app's migrations from a dependency bump are in the same position. What that check will not
cover:

- It will not stop the bump PR from merging without a person. After such a
  merge, every later automatic deploy, including config PRs and sandbox pins,
  stops at the same check until a person applies the migrations in both
  regions.
- Schema outside Django, such as ClickHouse tables. ee has none today. Its
  SQL under `internal/scripts/` is operator scripts, which hosted pods do not
  run.

ee's own workflow detects new ee migrations (`has_migrations`), but writes the
result only into ee's release notes. Counting them here needs an ee change
first: a label on the image that identifies its migration files, which the
bump could compare between the pinned and the new backend digest.

## Hold, audit and rollout

- To hold a release before merge, add `release-hold` to the release PR. Displayed
  spellings such as `Release-Hold` and `RELEASE-HOLD` also stop approval and merge.
  Remove it when ready; its update restarts the normal cooldown. Any writer can
  also remove the label. To stop the entire PR policy, unset `RELEASE_AUTOPILOT`
  or set it to `off`.
- After merge/tagging, `release-hold` cannot undo the release. Set
  `RELEASE_AUTO_APPROVE=off` for future runs, cancel any already-running release
  that must stop, and retain the deployment repo's separate hold and rollout
  controls. A variable change does not cancel jobs already publishing images.
- Neither variable is an owner-only stop: any writer can set it back to `on`
  (see "Who can change the switches"). The stops that do not depend on a
  variable are the `release-hold` label, for the PR policy only, and
  cancelling the running `release-images.yml` run, for publication.
- The bot leaves a SHA-bound policy comment, an approval review,
  `autopilot-approved`, and a decision summary. A successful merge adds
  `autopilot-merged` and a completion comment linked to the run. An attempted
  approval is not represented as a successful merge. Search the labels to audit
  approvals and completed merges independently. GitHub cannot transact a merge
  and its subsequent audit label together: a post-merge API failure leaves the
  pre-merge audit/review, but an operator must repair the success annotation.
- Before `RELEASE_AUTO_APPROVE=on`, add a tag ruleset on `v*` so only
  administrators and the release App (bypass list) can create, update or
  delete `v*` tags (see the prerequisites), and set the `production-auto`
  environment to "Selected branches and tags" with `main` and `v*` tags (see
  the prerequisites for why `main`). Check again that every `v*` tag points
  at a commit on main, as the tag sweep left it. The
  in-code actor check is defence in
  depth; the ruleset is the control that matters. Also complete the approver
  App, environment, Actions-approval and release-branch settings above, so
  that no writer can approve a change into main without a person.
- Cut beta and rc tags only at a commit on main, never from dev (see the tag
  ruleset under the prerequisites), and only after this change has reached
  main: the policy runs from main, and both release-images steps stop with a
  clear error if the script is missing there. Keep the script's arguments and outputs backward
  compatible until a change reaches main.
- The merge also requires the `migrations-callout` workflow's `check` job to have
  passed on the release head (the run is identified by its workflow file,
  because its job is named `check` like other checks), and the release PR to be
  current with main or main to have been quiet for the cooldown. A behind PR merges on checks that ran
  against an older main; the tag-time migration count still selects Production
  review for what main added. If GitHub reports the PR as `behind` (main
  requires up-to-date branches), the run waits: before approving when the
  check step sees it, or at the recheck after approving when GitHub reported
  `blocked` first. If GitHub still refuses the merge (HTTP 405 or 422), the
  run records the refused
  head in a PR comment, keeps the approval and ends green; the next eligible run
  retries once, and a second refusal of the same head fails the run.
- **Which callout run counts.** The run's head branch must be
  `release-please--branches--main` in `future-agi/future-agi`, and the run
  must list this PR, with base `main`, among its pull requests. Runs from a
  fork or another branch at the same commit are ignored. No field of a run
  names the PR that started it: `display_title` is that PR's title, which its
  author sets and need not be unique (#549 and #550 share one). GitHub lists
  every open PR from a branch on each of its runs (seen on PRs #549 and #550:
  each run of their branch lists both). So a
  run from any PR whose head is the release branch counts, not only runs of
  the release PR itself. The callout workflow (from dev, 10 Nov) runs for PRs
  into `dev` and `main`. Its `self-test` job runs on every PR. Its `check` job
  runs only for a `release-please--` head into `main`; on a PR into dev it is
  skipped and the run still ends green (run 43330391546, for PR #3872 into
  dev, shows that shape). The gate therefore decides on
  the `check` job, not on the run. It reads each run's jobs, newest run first,
  and passes over a completed run whose `check` job was skipped. The first
  other run decides: the run and its `check` job must both have completed
  with success. A run still queued or running, a run with no `check` job, or
  a malformed job list makes the release wait, so an older pass never counts
  over a newer run that has not finished. GitHub allows one open PR per head
  branch and base.
- **A writer can make the callout gate pass.** The skipped `check` job is
  the only thing that sets another PR's run apart, and it does so only for
  PRs into dev, which run dev's copy of the workflow. A writer can create a
  branch, change its copy of `migrations-callout-check.yml` so that it runs
  for PRs into that branch and its `check` job always succeeds, then open a
  PR from the release branch into it. A PR run uses the workflow file in its
  merge preview. Here that is the writer's copy, because the release branch
  changes only release metadata, not that file. That run has the same
  workflow path, head commit, head branch and repository, and it lists the
  release PR with base `main`. Its `check` job succeeds and, as the newest
  run, it decides the gate, even if the release PR's own check failed. The
  autopilot can then merge a release that adds migrations while its notes
  lack the callout. This was read from the code and GitHub's documented
  behaviour, not tested; only the PR listing was seen on real runs. It gives
  the writer little: the callout check is not a required check on main, and
  one writer can already approve and merge the release PR by hand. The
  writer could also add the callout by editing the release PR body. The
  tag-time migration count still selects Production review for any
  migration or schema file in this repository that it counts. The gate does
  not refuse when another PR from the release branch is open, because the
  writer can close that PR after its run.
- **What the callout check reads.** On a PR event the `check` job's script
  (`.github/scripts/migrations_callout.py`) reads the run's own merge preview:
  `GITHUB_SHA`, the `refs/pull/N/merge` commit that the checkout fetched. It
  fails unless that is a merge with two parents whose second parent is the PR
  head. The release range starts at the newest stable tag reachable from the
  merge's first parent (main's tip when GitHub built the preview), so both
  ends come from one snapshot. It does not read the payload's
  `merge_commit_sha`. GitHub computes the preview after it creates the PR, so
  an `opened` payload often carries null (GitHub's recorded example payloads
  and third-party reports; not observed in this repository), and a re-run
  reuses the payload. Migration-free release PRs #3636 and #3594 each had
  one callout run, from `opened`, and no later one. The checkout logs of release PR runs (#3636,
  #3720, and the force-push runs on #3629 and #3585) all show the run's head
  commit as the preview's second parent.
- The callout script change ships in the same PR as `release-autopilot.yml`.
  It must reach main no later than that workflow, and before
  `RELEASE_AUTOPILOT` is turned on. Promotions merge all of dev, so both
  arrive together unless one is reverted or picked alone. Before
  `RELEASE_AUTOPILOT` is turned on, on the first release PR after that
  promotion, check the callout run from the `opened` event:
  1. Its `check` job log must contain the line
     `Added migrations: N (<base> -> <head>)`. The script prints it
     (`.github/scripts/migrations_callout.py:128`) after it has read
     `GITHUB_SHA` as this PR's merge preview and before it reads the notes,
     so the line shows that the read worked on an `opened` payload. The job
     must not fail with an error that starts "GITHUB_SHA is not".
  2. If N is 0, the job passes with no edit to the PR.
  3. If N is greater than 0, the job is expected to fail with "Release notes
     lack 'Contains database migrations'": release-please's body has no
     callout, and nothing in this repository adds one. This is the expected
     case for the first release after the promotion that brings this change
     to `main`. On 13 Nov `dev` (local ref 02c5d006f) and this change's head
     8268ca779 each added 32 migration files since v1.58.0, and `main`
     (local ref 24cc65768) added none:
     `git diff --name-only --diff-filter=A v1.58.0 <ref> -- 'futureagi/*/migrations/*.py'`,
     without `__init__.py`. Release PRs #3720, #3705 and #3644 each failed
     their `opened` run and passed on the same head after a person edited
     the body (run history read in review round eight; the #3720 log shows
     this error). A person adds the callout after the version heading
     and inside the `---` delimiters; the `check` job of the `edited` run
     must then pass.

  If a release PR was already open when the promotion merged, the promotion
  only updates it, and no `opened` run follows. Check parts 1 to 3 on the
  first callout run created after the promotion merged, on the updated
  head: release-please's push and body edit start runs there (on 5 and
  15 Oct, two runs in the same second after a promotion updated an open
  release PR; the event types are inferred from the timeline). If no such
  run exists, close the release PR and reopen it at once, which starts a
  `reopened` run (`.github/workflows/migrations-callout-check.yml:9`). Do
  not push to the release branch: a person's push leaves the release to a
  person (see "Who pushed"). Ignore that PR's earlier `opened` run, which
  ran main's previous workflow and prints no `Added migrations:` line. The
  script reads `GITHUB_SHA` the same way on every PR event
  (`pr_merge_preview`, `.github/scripts/migrations_callout.py:63-87`, called
  at line 102 whatever the event's action), so check part 1 once more
  on the next release PR's `opened` run. The switch need not wait for that:
  a failed read fails `check`, the callout gate then does not pass
  (`callout_passed`, `scripts/release_autopilot.py:337-357`), and the
  release waits for a person.
- First merge with both variables unset. Verify labels, App permissions, the
  approver App live check, environment protections, default-branch/main
  availability, required check names, and immutable-tag rules. Then enable PR merging during an explicit
  weekday window; verify that the App merge triggers release-please tagging.
  Enable `RELEASE_AUTO_APPROVE` separately after observing its manual-path
  summaries and the deployment safety net. No admin setting is changed by code.
- On the first release PR after `RELEASE_AUTOPILOT` is on, which may not be
  the first after the promotion, read the autopilot's run summary. This is
  also the first live merge attempt: there is no report-only mode. The
  gate's two reads, a run's `pull_requests` and its jobs, have not yet run
  with the workflow token. If either comes back empty, the release waits
  with a green run and nothing is posted on the PR.
  - Pass: once the callout `check` job is green on the head and the
    required checks and the cooldown have passed, the summary gives a
    reason that the code reaches only after the callout gate
    (`scripts/release_autopilot.py:378-396`), such as "migrations-callout
    passed and the release PR is current with main" or "main moved
    recently; waiting for the release PR to catch up". A merge by the
    autopilot also shows it.
  - Fail: the summary keeps giving "the migrations-callout workflow has not
    passed on this head" with `check` green. Set `RELEASE_AUTOPILOT` off
    and report it.

## Python calls in key-holding jobs

`release-images.yml`, `release-autopilot.yml` and `sdk-sync.yml` hold the
release-bot key, the approver App key or a contents-write token. The
`deploy_github.tf` header in `future-agi/deployment` requires isolated mode
in every job that holds the deploy identity, the release-bot key, a token
minted from that key or an approver App's token. This change goes further
and covers every Python call in the `run` steps of these three workflows:
each uses `python3 -I` (or `python -I`), which is isolated mode. It ignores `PYTHON*` variables and the user site directory, and
it puts neither the current directory nor the script's directory on
`sys.path`. Without it, `python3 -m pip` imports a `pip.py` file or a `pip/`
folder from the current directory, and `python3 scripts/x.py` imports a
`json.py` placed beside the script, while the job holds the key or token.
With it, Python imports only the standard library and installed packages.

The scripts these calls run import only the standard library and PyYAML, so
they needed no change. Two limits remain:

- `sdkgen/generate.py` on futureagi-sdk `dev` puts its own repository root at
  the front of `sys.path` to import `sdkgen.generator`. For that one call, a
  top-level module file on futureagi-sdk `dev`, such as `typing.py`, still
  shadows the standard library. Such a file needs a merge into `dev`, which
  the custody above already requires a person to review. Closing it needs a
  change in futureagi-sdk, for example adding the root at the end of
  `sys.path` instead.
- `-I` also hides packages installed with `pip install --user`. The bump
  job's verify step therefore no longer has a `--user` fallback; PyYAML comes
  from the job's earlier install.

Python calls left without `-I`, and why:

- `worker-release-checks.yml` runs `python3 -m unittest` from `scripts/`. It
  holds only a read token, and unittest needs the current directory on
  `sys.path` to find the test modules.
- The backend build's `verify-command` in `release-images.yml` runs
  `python -c` inside the built image, not on the runner.
- `release-update-manifest.yml` runs `python3 -` in the "Validate proof
  before signing" step of its contents-write `publish` job. That code imports
  `deploy.update` and `scripts` from the job's `refs/tags/<version>` checkout,
  so plain `-I` would break it. It needs `python3 -I` plus an explicit
  `sys.path` entry for the checkout, appended after the standard library so a
  top-level module file cannot shadow it. Its `generate` job also runs
  `python3 scripts/generate_update_manifest.py`, but that job holds only a
  read token. That workflow is outside this change.
- `refresh-disposable-domains.yml` reads the release-bot key and runs
  `python3 scripts/generate_disposable_domains.py`. The script imports only
  the standard library, so `-I` alone is enough. That workflow is outside this
  change, but the header's rule covers it, so it needs its own reviewed
  change before the deployment apply.
- `build-image-multiarch.yml` runs `python3 scripts/image_size_budget.py` from
  the release checkout in its `build` job, and `python3 -c` in its `publish`
  job. Both jobs hold Docker Hub credentials, and `build` also has a
  `packages: write` token for the ghcr build cache. Neither holds the
  release-bot key, an App token or a contents-write token, so the rule above
  does not cover them, and they stay as they are.
- `helm-release.yml` runs `python -m pip` only in its read-only `package`
  job.

## Verification and unchanged workflow sections

Synthetic tests run with `python3 scripts/test_release_autopilot.py` after
installing `PyYAML==6.0.3`, including Git histories, every merge predicate,
case-insensitive holds before/after approval, content/mode/version rejection,
immutable Git API evidence, current-SHA status handling, transaction rechecks,
idempotent audit/approval, approver App identity, refused merges, environment
decisions, the release-branch push check, the reason given for a red or running
required check, the workflow's job split, wake-up triggers and action pins,
the callout workflow's triggers and `check` job, which callout run decides,
`sdk-sync.yml`'s base and version checks (its real first step runs under
`bash -e`), and the bump job itself. Its count, bump, verify and `Open bump PR`
steps run in workflow order on synthetic values files shaped like the
producer's: only the 12 `COMMON_PATHS` tags, the three digests and the
repositories the step checks. The count runs real `git` over a blob-less clone
of a synthetic history whose origin is deleted, so no file contents can be
fetched. The steps use real `yq` when it is on `PATH` (GitHub's Ubuntu runners
have it) and also a stub that accepts only the job's expressions; both must
write the same files and lines. Only the last step's `git` and `gh` are stubs.
The ancestry tests also plant tags named `origin/main`, `origin/dev` and
`refs/remotes/origin/main`, on an old main commit and on an unreviewed one.
The `sdk-sync.yml` tests run its two checkout checks on scratch repositories
(a same-named branch, another commit, a missing tag and a tag of a tree are
refused) and evaluate its job condition on the shapes of real runs.
The Python isolation tests scan every `run` step of the three key-holding
workflows and require `-I` on each Python call, against a fixed list of
calls. The scan reads calls after `VAR=value`, `env`, `timeout`, `exec`,
`sudo`, `time`, `nice`, `command` and `nohup`, and versioned names such as
`python3.12`. Any other word that starts with `python` fails the test, unless
it is on a listed line that names Python without running it. Only one line is
listed: sdk-sync's `git diff --quiet -- python typescript`. A form the scan
cannot read, such as `xargs python3` or `/usr/bin/python3`, therefore fails
instead of being skipped. Every `run` step must also use bash, because a
`shell: python` step names no interpreter. They plant `pip.py`, a `pip/` package, `uuid.py`, `typing.py` and the
modules the scripts import in the job's working directory, at the top of a
checkout and beside its scripts. Each of this repository's scripts then runs
as its workflow calls it, with `-I`, and imports none of them; without `-I`,
the same call imports one. The bump job's real steps also run with these
planted modules, and every Python call they make must use `-I`.
Run workload tests only in E2B or CI, not on the shared development host.

**CI wiring:** the `Release script checks` workflow
(`.github/workflows/worker-release-checks.yml`) installs `PyYAML==6.0.3` and,
from `scripts/`, runs three test modules by name: `test_release_autopilot`,
`test_release_error_feed_worker` and `test_verify_platform_image_tags`. It runs
when `release-images.yml`, `release-autopilot.yml`, `sdk-sync.yml`,
`migrations-callout-check.yml`, the three scripts, their tests,
`scripts/image_size_budget.py` or this document change. This document is
listed because a test checks that it quotes the autopilot's wait reasons, and
the callout workflow because a test pins its triggers and `check` job. The
callout script's own tests run in that workflow's `self-test` job.
`scripts/image_size_budget.py` is listed because the isolation test runs it
as `size-report` does. Its own tests,
`deploy/tests/test_image_size_budget.py`, run in `images-ci.yml`. A test
checks that every script the isolation test runs is in the list. Other `scripts/test_*.py` files run in their own
workflows with their own tools: `python-lint.yml` runs the lint-gate test with
ruff, `test-integrity.yml` runs its own self-test, and `standalone-ci.yml` runs
the install smoke test. A test guards this list. The `paths` filter does not list
`.github/workflows/build-image-multiarch.yml`, `backend-ci.yml`, `e2e-ci.yml`,
`futureagi/tracer/services/clickhouse/**` or the two grouping schema files. A
rename of that workflow's `publish` job (one of the two jobs that prove a release
shipped), a rename of "Backend CI", "E2E CI" or their required jobs (the
autopilot's wake-up signals), or a move of a ClickHouse schema applier, of
`v2/schema/` or of a grouping schema file, is therefore caught only when a
listed file next changes.
Adding those paths to the filter closes the gap.

The `release-images.yml` diff is deliberately limited:

- Header/approval comments describe the optional review path.
- Guard outputs and new post-guard steps expose ancestry and environment.
- `approve.environment` consumes the guard's decision, with new post-guard steps
  revalidating it after the wait. Both original inline guard scripts are unchanged.
- In `bump-deployment`, every action is pinned to a commit SHA with its version
  in a comment, this repository's checkout fetches history and tags without
  file contents, a new step reads the baseline and counts, and `Open bump PR`
  writes the two lines from its `env`.
- Every Python call runs with `-I`, and the verify step's `pip install --user`
  fallback is gone (see "Python calls in key-holding jobs").

All jobs from `build` to `size-report` are byte-identical to the base, except
the `python3 -I` calls in `release-worker` and `size-report`. The
steps of `bump-deployment` that the deployment repository's
`tests/test-release-digest-bump.py` runs (the step whose name starts with
"Bump platform tags" and the step `catalog-images`) and its `COMMON_PATHS`
list are unchanged. Image builds, version/channel validation, digest/size
checks, SDK/worker releases, EE/docs dispatches, aliases, retries and bump-PR
creation semantics are not refactored.

Synthetic tests cannot prove GitHub environment review behaviour, App identity
permissions, branch-protection enforcement, schedule delivery, real check names
or SHA attribution, the release-please merge-to-tag trigger, tag immutability,
image publication or downstream deployment. These need CI/admin verification;
no release workflow is dispatched as part of local verification.
