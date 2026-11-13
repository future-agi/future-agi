# Low-risk PR autopilot

The autopilot can approve and merge a narrowly allowlisted PR into `dev` after
CI and a separate, failing-on-findings AI review gate pass. It approves as a
dedicated approver GitHub App, never with the workflow token. It ships **off**.
Nothing is pushed, enabled, merged or deployed merely by adding these files.

The policy follows the [Anthropic code-review tiers](https://claude.dev/blog/solving-code-review-at-anthropic):
low-risk changes from trusted authors can rely on automation; medium needs one
person; high needs two. Sensitive paths always need people, and an automated
approval does not fill a human-review slot. The path lists and 200-line/10-file
limits below are this repository's conservative choices, not thresholds quoted
from Anthropic.

## Risk tiers

The controller reads GitHub's complete, paginated changed-file list and its
addition/deletion counts. It does not import, install, check out or execute PR
code to classify risk. It checks both sides of a rename and checks file totals
against the PR totals. Missing, inconsistent or truncated evidence fails closed.

| Tier | Rule | Result when the feature is on |
| --- | --- | --- |
| HIGH | Any sensitive path: `.github/**`, CODEOWNERS, `ee/**` at any depth, migrations, auth/accounts/SAML, permissions/RBAC/IAM, billing/payment, settings, secret/credential handling, Dockerfiles, dependencies/lockfiles, deployment/infrastructure, agent instructions, or the autopilot's own files | `autopilot-high`, an explanation, and two human reviews needed; never bot-approved or merged |
| LOW | Every path is explicitly allowed, at most 200 added **plus** deleted lines, at most 10 files, and only additions/modifications | `autopilot-low`; eligible for further gates, not an approval |
| MEDIUM | Every other known change, including larger diffs, file removals, renames, harness/configuration changes and unknown paths | `autopilot-medium`, an explanation, and one human review needed; never bot-approved or merged |

HIGH rules take precedence, even within a test or documentation directory.
Matching is case-insensitive for sensitive paths and splits security-related
path tokens on separators and camelCase boundaries, then rejoins adjacent words
so `api_key`, `api-key`, `apiKey`, and `apikey` all remain HIGH. Explicit rules
also cover model-hub/agentcc API-key modules, socket workspace authorization and
gateway access groups. These exclusions apply inside every LOW test root too.
The LOW allowlist is case-sensitive. Glob `*` cannot cross a `/`; only a whole
`**` segment matches zero or more directories. A `test_`-prefixed directory never
makes `conftest.py`, `helpers.py` or `__init__.py` a named test file.
An unrecognized path is never LOW. No file deletion or rename
is LOW, which is stronger than just forbidding deleted test files. Deleting a
test *inside* a modified file still needs the AI gate to detect the weakened
coverage; metadata alone cannot judge that.

The initial LOW candidates reflect existing repository paths:

- Markdown in `docs/` and root `README.md`, except sensitive/agent-policy files;
- JSON payloads in `frontend/src/locales/langs/`, not localization JavaScript;
- named Python test files in `futureagi/tests/` and the tracer, model_hub and
  simulate test roots, subject to the HIGH exclusions;
- `e2e/flows/**/*.spec.ts`, excluding sensitive paths.

Arbitrary scripts, fixtures, `conftest.py`, MDX, JSX “copy changes”, and test
harnesses are not allowlisted. A path-only classifier cannot establish that
those changes are harmless. The authoritative lists and rationale are in
`.github/pr-autopilot.yml`. That file uses JSON, a valid YAML subset, so the
controller needs only Python's standard library.

**Why CI is always HIGH:** workflows control tokens, secrets, required checks
and the code that declares a PR safe. Auto-approving a change to those controls
would let a PR change its own admission rules. The same rule covers this
controller, its tests, configuration and operating documentation. Changes to
those files take the human path.

## Switches and administrative prerequisites

No repository settings are changed by the workflow installation, with one
exception: if the owner has not created the environment `pr-autopilot`, GitHub
creates it, with no rules, the first time the `approve-and-merge` job runs.

| Variable, secret, label or setting | Default | Administrative action |
| --- | --- | --- |
| Repository variable `PR_AUTOPILOT` | Unset/off; only exact `on` enables automation | Set to `on` only after the rollout checks below; unset or use `off` to stop new runs |
| Policy list `trusted_authors` in `.github/pr-autopilot.yml` | Empty: nobody is trusted | A person adds a login through a reviewed PR into `dev` that changes this list. The file is HIGH, so the autopilot never merges such a PR, and only `dev`'s copy is used. Exact GitHub logins; no wildcards |
| Repository variable `PR_AUTOPILOT_TRUSTED_AUTHORS` | Unset: use the policy list as it is | Optional. A comma-separated list of logins that can only narrow the policy list: a login that is not in the policy list is not trusted, and a list that names no policy login trusts nobody. It cannot add an author |
| Repository variable `AUTOPILOT_HOURS_UTC` | Unset: any day/time, but only when the feature is on | Optionally set `04-15` or another zero-padded `HH-HH` window; Monday–Friday, UTC, inclusive start/exclusive end; `00-24` means all weekday hours. The release autopilot (`docs/release-autopilot.md`) reads the same repository variable, so a change applies to both. It accepts overnight windows such as `22-06`, which stop this autopilot |
| Repository secret `ANTHROPIC_API_KEY` | Absent: skip the AI job and never auto-merge, even if an older review passed | Provision an API key for the approved Anthropic account, with budget/usage monitoring; do not put it in the policy or PR |
| Existing repository variable `DEV_CDN_AUTO_DEPLOY` | Unset: preserve the existing enabled-on-push behavior | Exact `false` prevents the post-merge dev CDN dispatch; all other values preserve its existing behavior |
| PR label `autopilot-hold` | Absent | Add to stop automation for one PR; remove only when the PR may be reconsidered |
| Labels `autopilot-low`, `autopilot-medium`, `autopilot-high` | Created by the first enabled sweep or run | No setup needed. The sweep creates missing labels once before it dispatches classification runs, and a run that finds a label just created by another run continues. The controller replaces only its own current tier label |
| Labels `autopilot-approved`, `autopilot-merged` | Absent | Created/applied by the controller for audit; retain them after merge and revert |
| Built-in `GITHUB_TOKEN` and Actions permissions | Provided by GitHub; explicit least-privilege job permissions | Allow the workflow's stated read/write scopes, including `statuses: write` in the prepare job (it posts the reservation status) and `statuses: read` in the `approve-and-merge` job. No Administration permission or protection-bypass role is needed or granted. The workflow token never approves, so turn **off** “Allow GitHub Actions to create and approve pull requests” (see [Approver App and owner settings](#approver-app-and-owner-settings)). An organisation-level “off” overrides the repository setting |
| Approver App, environment `pr-autopilot`, variable `AUTOPILOT_APPROVER_CLIENT_ID`, secret `AUTOPILOT_APPROVER_PRIVATE_KEY` | Not created | The owner creates them as described in [Approver App and owner settings](#approver-app-and-owner-settings). Without them the token step in the `approve-and-merge` job fails. The review outcome is already recorded by then. Nothing is approved or merged, and the bot posts one note per head on the PR |
| Actions event policy for `pull_request_target` | None today: in GitHub's 9 Nov 2026 brownout, all 57 `pull_request_target` runs in this repository ended in `startup_failure` | From 2 Dec 2026 GitHub blocks `pull_request_target` in public repositories unless an Actions event policy, at repository or organisation level, allows it. The policy must allow it for `.github/workflows/low-risk-pr-autopilot.yml`. If the policy for `admission.yml` and `labeler.yml` names workflow files, it must name this file too. It must be in place before this workflow reaches `main` and before 2 Dec 2026; the 16 Nov 16:00–18:00 UTC brownout applies as well. Without it, every PR event into `dev` creates a run that fails at startup, even with `PR_AUTOPILOT` off, and only the sweep classifies and dispatches PRs (see below) |
| `dev` protection / active org and repository branch rules | Read, never changed by this code | No new ruleset is needed for required checks. `dev` does not require up-to-date branches today (classic protection, strict off), and the autopilot does not need it to: it checks itself that the PR head contains the live `dev` tip (item 1 of [Trust boundary and merge decision](#trust-boundary-and-merge-decision)). So people's PRs into `dev` keep merging without an "Update branch" and a CI re-run after each merge. Do not add a strict rule for the autopilot's sake. A strict rule covers every PR into `dev`: after each merge, every other open PR would need an update and a new CI run, including the E2E job (66 to 89 minutes on `dev`) for PRs that touch the app. An active `pull_request` rule must dismiss stale approvals on push; the controller refuses without it. dev's organisation rule has it on today (read-only check, 12 Nov). Also require approval of the most recent push in a repository ruleset (owner step 3 below); the controller does not check this one. Merge queues and required-workflow rules on `dev` stop the autopilot |
| Repository setting "Always suggest updating pull request branches" (`allow_update_branch`) | Off (read-only check, 12 Nov 2026) | An owner choice; leave it off unless the owner decides otherwise. GitHub shows the "Update branch" button only when the base branch requires up-to-date branches or this setting is on. Neither holds today, so PRs into `dev` have no such button: on 12 Nov `viewerCanUpdateBranch` was false, for an admin viewer, on all 40 most recently updated open PRs into `dev`. The autopilot does not need the button. When a LOW PR's head does not contain the live `dev` tip, the bot posts a behind-dev note, and the author merges `dev` into the branch locally and pushes. Turning the setting on changes two things, because it applies to the whole repository. First, release PRs into `main`, which sit behind `main` by design, get the button, and one click by a person hands that release to a person (`docs/release-autopilot.md`, "Who pushed"). Second, every writer sees the button on LOW PRs into `dev`, and one click by anyone not on the trusted-author list sends that PR to a person for good (see below) |
| Required status checks | Policy minimum: `Backend Tests Pass`, `E2E Tests Pass` and `Python Lint Pass`, unioned with all active classic/ruleset requirements | Keep required checks meaningful and green on the PR head; missing, neutral, pending, stale or failing checks cannot satisfy the bot, and neither can a check whose only runs were skipped. Skipped runs are ignored and the newest run that did not skip decides. Before a check is made required on `dev`, its job must use `if: always()` and fail when a job it needs failed (see below) |
| Merge commits | The policy's only merge method | Keep merge commits enabled; this matches nine of the ten recent first-parent `dev` commits inspected at `a75af77f6` |
| Workflow on the default branch | No PR-event runs and no sweep until this workflow is on `main` | PR events (`pull_request_target`) and the schedule both run `main`'s copy of this workflow. Land it on `dev` through human review and include it in the normal `dev` → `main` release; do not change the default branch. `main`'s copy checks out `dev`'s controller and policy, so a controller change on `dev` applies to those runs at once. A workflow change on `dev` (step names, environment variables, triggers) reaches them only with the next `dev` → `main` release. The classify, label and sweep paths therefore must keep working with the previous release's workflow copy; only dispatch runs on `dev` use `dev`'s copy |

**Trusted authors at installation: none.** Repository membership, write access,
CODEOWNERS, PR text and earlier bot approvals do not confer trust. The only
source of trust is the `trusted_authors` list in `.github/pr-autopilot.yml` on
`dev`. A person adds an author by reviewing a PR into `dev` that adds the
login there. The file is HIGH, so the autopilot never merges that PR. Login
comparison is case-insensitive; a malformed entry in the list or the variable
stops automation. Start with a small, explicitly approved list. A fork, even
from a trusted author, is never eligible.

Any member with the Write role can create, change or delete repository
variables, in the UI or by REST (GitHub's documentation, not tested). So no
repository variable is an owner-only control. `PR_AUTOPILOT_TRUSTED_AUTHORS`
can therefore only narrow the policy list; it never adds a login. Deleting the
variable restores the whole policy list, so a lasting removal of an author is
a reviewed policy PR, and `autopilot-hold` stops one PR. Any writer can also
set `PR_AUTOPILOT` to `on` or widen `AUTOPILOT_HOURS_UTC`. Neither is an
owner-only gate; they decide when the autopilot runs, not whose PRs it takes.
Moving the list to a secret would not help, because the Write role can
change Actions secrets too. GitHub's pages disagree on whether it can change
an environment variable.

Trust covers **who pushed**, not only who opened the PR. Before it reserves a
review, before it posts a behind-dev note, and again before approval and
merge, the controller reads the branch's push history (`GET /repos/{owner}/{repo}/activity?ref=refs/heads/<branch>`,
one page of up to 100 rows). Every row must name a trusted author. A push by
anyone else, including an "Update branch" click by someone not on the list,
sends the PR to a person for good; the author has to merge `dev` into the
branch and push it themselves. An empty history, a full page that may hide
older pushes, or a page whose oldest row is not the branch's
`branch_creation` row (so earlier pushes are missing) also goes to a person.
On 2026-11-11 the oldest row of all 19 sampled PR branches in this
repository was `branch_creation`. If the newest push in the feed is not yet
the PR head, the run waits. If the endpoint does not answer, the run stops
and approves nothing; a run on a branch behind `dev` posts no note and waits
for the next sweep.

An invalid or overnight working-hours window denies automation. Use one
same-day UTC window; there is no local-time/DST conversion or implicit fallback.

A sweep runs about every 15 minutes, subject to GitHub's scheduler delay. It
skips drafts, forks and PRs whose head repository no longer exists, and
dispatches a run on `dev` for these PRs, in this order:

1. Every trusted author's `autopilot-low` PR without `autopilot-hold`. These
   are not capped, so the backfill never delays a merge. A PR that waits on a
   person, the owner or the author at its current head is dispatched only
   on one sweep in four, about once an hour. It waits when the bot's
   standing escalation, merge-blocked note, approver note or behind-dev note
   names its current head, or when the autopilot's approval of that head
   was dismissed (item 2 of [Trust boundary and merge
   decision](#trust-boundary-and-merge-decision)). A push ends
   the wait at once, because the note then names an older head. So does an
   escalation marked "No longer applies" or an approver note the bot marked
   the same way after a later approval. Parked PRs are staggered by PR
   number. The sweep reads each trusted LOW PR's comments and reviews for
   this. If a read fails, the PR is dispatched as usual.
2. PRs to classify, at most 25 per sweep:
   - a PR with no tier label yet (the one-time backfill, so PRs opened before
     enablement get classified);
   - a labelled PR whose current head differs from the head its last tier
     comment records. The sweep reads the comments of at most 50 labelled PRs
     per sweep. This keeps classification working if GitHub blocks this
     workflow's `pull_request_target` runs: a PR trimmed from medium to low is
     still re-tiered, only later. The controller's own label and comment
     writes do not change a head, so they cannot re-trigger this. Forks are
     never dispatched, so without PR-event runs a fork PR gets no tier label.

Both capped lists start at an offset that moves each sweep, so PRs whose runs
keep failing cannot hold every slot. The sweep pauses one second between
dispatches, as GitHub asks for mutating requests. A failed dispatch does not
stop the rest, except a 403 or 429 (GitHub's secondary rate limits): then the
sweep stops dispatching until the next sweep. Last, unless GitHub throttled
it, the sweep recovers autopilot merges whose run ended before their merged
record (see [After merge and audit recovery](#after-merge-and-audit-recovery)).
The sweep fails and names the PRs it could not dispatch, whose tier comment it
could not read, or whose merge it could not recover.

Parked PRs are still dispatched hourly, not skipped. An escalation can stop
applying without a new head (for example a run that read as deleted reads
again), a person can supply what a branch rule needs, and the owner can fix
the approver App. Skipping such PRs would strand them. The notes only slow
the sweep: a writer who edits or deletes one changes when the PR is
dispatched, never what a run checks. A head that is behind `dev` stays
behind until it changes, so the hourly dispatch and the run that a push
starts are enough for it. An escalation of a behind head is not checked
again until the head changes or contains the `dev` tip (item 1 of [Trust
boundary and merge decision](#trust-boundary-and-merge-decision)). A PR that waits with no note (pending CI, a review
to retry after the branch changes) is still dispatched on every sweep,
because nothing tells the sweep when that wait ends. A dispatch on `dev`
with input `pr_number` re-evaluates a PR at once; it cannot reset the review
budget.

Branch protection on `dev` and `main` requires "Backend Tests Pass" and
"E2E Tests Pass" since 12 Nov 2026. `dev` also requires "Python Lint Pass"
since the same day (read-only check, 12 Nov, 08:15 UTC; all three from the
GitHub Actions app, strict off). All three are in the policy's minimum
required checks, so the autopilot keeps requiring them even if a branch rule
is later relaxed. The controller also unions the branch's own required
checks, so a check an administrator adds later is respected too.

- "E2E Tests Pass" runs with `if: always()` and concludes success when the
  path filter skips the stack jobs, so a PR that does not touch the app does
  not wait on the suite.
- "Python Lint Pass" comes from `python-lint.yml`, which runs on every PR
  (opened, synchronize, reopened, edited) and passes when no Python lint path
  changed. A PR whose last run predates that workflow has no such check, so
  the autopilot waits until a push or a title or body edit runs it. The
  workflow has no push trigger, so a merge needs no dispatch of it.
- When a head carries several runs of one check, the newest run that did
  not skip wins. On PR 3780 on 12 Nov a lint run cancelled by its
  concurrency group reported failure and a newer run reported success; the
  autopilot counts the success. A newer failed, cancelled or unfinished run
  still overrules an older success.
- Skipped runs are ignored, because a summary job skipped by its own `if:`
  still adds a check run. GitHub's documentation says a skipped required
  check counts as passing; this was not tested here, because no required
  check skips today. "Python Types Pass" (`python-types.yml`, not required today)
  skips on an ordinary title or body edit. On PR 3973 on 13 Nov its head
  carried a success and, 10 seconds later, a skipped run. A head whose only
  runs of a required check were skipped waits, with no note on the PR.
- So a skip must never stand in for a failed dependency. Before a check is
  made required on `dev`, two things must hold:
  - For the autopilot: its job uses `if: always()` and fails when a job it
    needs failed. Without `if: always()`, a failed dependency skips the
    summary job, and an older success on the same head would count.
  - For a person's merge, which GitHub's own rule gates: the job reports a
    result, never a skip, on every event its workflow runs on. GitHub
    documents that a skipped job reports success. If a newer skipped run of
    a check also outranks an older failure for that rule (inferred, not
    tested), a skip after a failure would let a person merge over it.

  The summary jobs of the three required checks meet both: each uses
  `if: always()` with no other condition (`backend-ci.yml:623`,
  `e2e-ci.yml:570`, `python-lint.yml:99`). "Python Types Pass" meets only
  the first. `python-types.yml:148` skips its summary on an ordinary title
  or body edit, so a failed type check followed by such an edit leaves a
  newer skipped run on the same head; #4012, #4014 and #4026 had that
  shape on 13 Nov (review round eight). Keep it optional until
  `python-types.yml` runs its `changes` job and summary on those edits as
  well, or otherwise reports a result on every event. The policy file's
  `_checks_reason` and the header of `python-types.yml` (lines 9-11) still
  give only the first condition.

The label instructions describe the medium/high human policy; this workflow
does not replace repository rules that enforce the number of human
approvals.

## Approver App and owner settings

Approvals come from a dedicated approver GitHub App, never from the workflow
token. This matches the bot autopilot in `future-agi/deployment` (#438). The
release autopilot in this repository (`release-autopilot.yml`) uses the same
design with its own environment. Today "Allow GitHub Actions to create and
approve pull requests" is on for this repository, and `main` and `dev` are
under organisation ruleset 2289447 (one approval, no code-owner review, no
last-push approval). While that setting is on, any writer's branch workflow can
approve that writer's own PR as `github-actions[bot]`, so neither branch is
review-gated, with or without this autopilot.

How the workflow uses the App:

- Only the `approve-and-merge` job names the environment `pr-autopilot`.
  That job runs only in `workflow_dispatch` runs on `dev`, and only after the
  `review-gate-and-merge` job finished and recorded the outcome: after a pass
  published in the same run, or for a reused pass. The environment holds the
  App's client ID as the variable `AUTOPILOT_APPROVER_CLIENT_ID` and its
  private key as the secret `AUTOPILOT_APPROVER_PRIVATE_KEY`. Its deployment
  branch rule allows only `dev`, so a copy of the workflow on another branch,
  and a `pull_request_target` run (which runs on `main`), cannot read the key.
- The job with the outcome markers names no environment. An environment that
  refuses a job (for example a branch rule that does not allow `dev`) or
  holds it (required reviewers, a wait timer) therefore cannot stop a verdict
  from being recorded. A refused `approve-and-merge` job does not start,
  approves nothing and fails the run. A job that never starts cannot post a
  note, so only the failed run shows it, and the sweep dispatches the PR
  again on every sweep, each run failing the same way, until the owner fixes
  the environment.
- A step in that job mints a token for this repository only, with only "Pull
  requests: write" (`actions/create-github-app-token`, pinned by commit). The
  step has `continue-on-error`, so a missing variable, secret or installation
  does not skip the merge step. Only the merge step gets the token
  (`APPROVER_TOKEN`) and the App's login, `<slug>[bot]` (`APPROVER_LOGIN`).
  The token is used only to post the approval and, when the PR's head moved
  while it was posted, to withdraw it (item 6 of [Trust boundary and merge
  decision](#trust-boundary-and-merge-decision)). Comments, labels, the
  merge and dispatches use the workflow token.
- If the token or login is missing, or the login is `github-actions[bot]`,
  the controller adds no label or audit comment and approves nothing. It
  posts one note per head with the workflow token, and the run fails. The
  note says that the approver App could not approve this head and why, that
  nothing was merged, what
  the owner must check, and that a person may approve or merge the PR. A
  refusal of the approval (HTTP 403 or 422) or an approval that GitHub
  records under another login posts the same note. A server error does not.
  Once the App approves the head, the bot marks the note "No longer
  applies". While the note names the PR's head, the sweep dispatches the PR
  about once an hour. Because the token step continues on error, the
  controller cannot tell a token step that failed on a GitHub server error
  from a missing key. Either way the PR gets the note and waits about an
  hour, while a server error on the approval itself posts no note.
- The controller's own-approval lookup and the dismissed-approval hold read
  only reviews by that login with type `Bot`. The prepare job does not hold
  the App's identity, so it waits when any bot's autopilot approval of the
  head was dismissed. That can only make a run wait; the merge step rechecks
  with the exact login.
- GitHub records each run of the `approve-and-merge` job as a deployment to
  `pr-autopilot`: one per passed or reused review, not one per sweep. Check
  whether Slack or other integrations subscribe to deployment events.

Owner settings for `future-agi/future-agi`. A read-only check on 11 Nov 2026
found none of them in place: the Actions setting is on, `dev`'s only rule does
not require last-push approval, and neither environment exists.

The order matters. While a writer can still get a PR approved without another
person, that writer can land a job that names `pr-autopilot` on `dev` (or
`release-autopilot` on `main`), dispatch it and read the approver key. A
leaked key outlives the later settings: with Contents: write it can push to
unprotected branches and give approvals that count, and it is never the last
pusher. So steps 2 to 5, and the whole one-time baseline in the
`deploy_github.tf` header of `future-agi/deployment`, come before the key is
added in step 6. Where these steps and that header differ on the order,
follow the header.

1. **Create the approver App.** Use it only for the two autopilots in this
   repository (this one and `release-autopilot.yml`), and install it on
   `future-agi/future-agi` only. It needs "Pull requests: Read and write" to
   post reviews. GitHub counts required approvals only from reviewers with
   write access, and one report plus one independent reproduction say an
   installation with only "Pull requests: write" gives approvals that do not
   count. So also grant the installation "Contents: Read and write". The
   workflow's token keeps only `permission-pull-requests: write`, because
   counting follows the installation's access, not the token's scopes; no
   `permission-contents` is requested. With Contents: write the key could mint
   tokens that push to unprotected branches and merge PRs. So the key lives
   only in the two environments below, and the App is on no ruleset bypass
   list. Do not store the key in any repository, environment or organisation
   secret yet.
2. **Turn off "Allow GitHub Actions to create and approve pull requests"** for
   `future-agi/future-agi`. Neither autopilot needs it. An organisation-level
   "off" also covers this repository. `frontend-auto-approve-hotfix.yml`
   approves with the workflow token and stops working once the setting is off.
   `future-agi/ee` and `future-agi/agent-learning-kit` also produce deployed
   images, have the same setting on and are under the same organisation
   ruleset. They have the same exposure and need the same change, plus a
   repository ruleset on their `main` with `require_last_push_approval: true`
   (step 3). On 11 Nov neither had a workflow-token approval (default branch
   and dev), nor did docs, and their jobs that open PRs use the App token.
   futureagi-sdk holds a copy of the release-bot key, so step 5's custody
   needs the setting off there too. Its `release-please.yml` approves the
   generated version PR with the workflow token (`release_guard.py approve`),
   so with the setting off that step fails while a version PR is open and the
   PR is never queued for merge. Leaving the setting on there is not an
   option. That workflow changes first, in a reviewed PR: it approves with a
   dedicated approver App whose key sits in a main-only environment, or it
   accepts a person's approval and drops the workflow-token approval step
   (`docs/auto-deploy.md`, Administrator setup step 1, in
   `future-agi/deployment`).
3. **Require approval of the most recent push** on `main` and `dev`, in a
   repository ruleset rather than by editing organisation ruleset 2289447,
   which covers other repositories. Otherwise a writer can push onto someone
   else's PR and approve it. The autopilot never pushes, so the approver is
   never the last pusher. A branch workflow can still push onto another
   person's PR as a bot, and the writer's own approval then probably counts
   (inferred, not tested). Test two workflow-token pushes on a scratch
   repository: a direct push, and a workflow that merges another branch into
   the PR branch. Until the test shows that the writer's approval does not
   count after either, this step is not met and step 6 waits. A push with an
   App token is expected to let the writer's approval count, so for App keys
   the key custody in step 5 is the control.
4. **Create the environments, with no key yet.** GitHub creates a missing
   environment with no rules the first time a job names it, so create each one
   with its branch rule first. Neither may have required reviewers or a wait
   timer: they would hold the approving job and, through the PR's concurrency
   group, every later run for that PR.
   - `pr-autopilot`: deployment branch rule `dev`. The `approve-and-merge`
     job of `low-risk-pr-autopilot.yml` names it.
   - `release-autopilot`: deployment branch rule `main`. The `merge` job of
     `release-autopilot.yml` names it.
5. **Move the release-bot App key** (`RELEASE_BOT_APP_ID`,
   `RELEASE_BOT_PRIVATE_KEY`, organisation secrets today) into environment
   secrets whose deployment branch policy admits only refs that one writer
   cannot create or merge into alone. "Protected branches only" is not
   enough: `fix/nightly-dev-27-05` is protected here with one approval and no
   status checks, and until a tag ruleset exists any writer can push a tag
   at their own commit, whose workflow files a tag run uses. Use "Selected
   branches and tags", make each admitted branch need a person's review
   (steps 2 and 3), and add a tag ruleset on `v*` that lets only
   administrators and the release App create, move or delete release tags,
   before an environment that admits tags gets the key. A ruleset does not
   remove tags that exist already, so once it is active, and before any key
   copy, each `v*` tag whose commit is not on `main` is deleted or cut again
   under a name that no key environment admits (four on 12 Nov;
   `docs/release-autopilot.md` names them). From then on, `v*` tags, beta
   and rc included, are cut only at a commit on `main`. Any writer's branch
   workflow can read an organisation secret, and that App can approve PRs
   too, so until this step a writer can still get a PR approved without
   another person. Every job that reads the key must then name such an
   environment; a job that names an environment still reads organisation
   secrets, so the jobs change first. A job names only one environment, so
   the key can need a copy in more than one. `docs/release-autopilot.md` here
   lists this repository's three holders and the jobs, and
   `docs/auto-deploy.md` in `future-agi/deployment` (Administrator setup
   step 1) lists the jobs in every repository, and the other remedy there: a
   separate App for deployment bumps under the same custody. The separate App
   covers only `future-agi/deployment`. This repository still needs the move,
   because the shared App can approve here. Do it before any approver key is
   added. The same custody is a precondition of that repository's deploy
   identity, and it is not limited to this key. No credential that a branch
   workflow can read may have Contents, Pull requests, Administration,
   Actions or Environments write on `future-agi/deployment`, or on a branch or
   tag that a key
   environment admits, such as `main` here once it holds the key. That covers
   personal access tokens (classic and fine-grained), OAuth tokens copied from
   a tool, App keys and deploy keys. The deployment documents name eight
   unclassified secrets and three checks (fine-grained Active tokens, the
   organisation's classic-token restriction, and each App installation's
   repositories), and a one-time baseline before the deployment apply. It
   covers every unmerged PR here, open or closed: it dismisses bot approvals
   and sends each PR whose final head a bot pushed to a full review by a
   person who did not author, push to or approve it, or closes the PR. A job
   that reads the key runs code only from refs that need a person's review.
   `docs/release-autopilot.md` names the jobs here that run another branch's
   code, and the order for the `sdk-sync.yml` check: on `main` with or
   before the change that names `release-bot`, and before that environment
   gets a key copy. The header moves the key in six steps, here and in
   deployment, docs, agent-learning-kit and futureagi-sdk:
   1. review rules on every admitted and fixed-ref branch (steps 2 and 3
      here);
   2. the environments with their rules and no key (step 4 here), the `v*`
      tag ruleset, the tag sweep, and each key job naming its environment;
   3. delete the organisation secrets and every repository copy. Generate
      the new private key first and hold it outside GitHub, because GitHub
      refuses to delete an App's only key, then delete every old key,
      because any branch workflow could read it. Finish the credential and
      Apps checks;
   4. at least one hour after the last old key was deleted, dismiss bot
      approvals and flag bot-pushed heads, the baseline's first two checks,
      on the PRs listed after that hour, so that a PR opened inside it is
      covered. A release-bot push is exempt from the flag only after step
      6, so at this step none is. From step 3 to step 6 no release job can
      mint a token, so a release-bot push in that span did not come from a
      release job;
   5. the one-time read: a person who authored, pushed or approved none of
      the commits that touched them reads the key jobs' workflow files and
      scripts once, in full, at each branch they run from and at each `v*`
      tag that stays, and records each tip SHA. Here that includes `dev`'s
      `scripts/pr_risk.py` and `.github/pr-autopilot.yml`, which the
      approving job runs with the approver token; the header lists the
      files. Then the header's re-listing, in each key-holding repository.
      Every `v*` tag is listed again, and each must point at a commit on
      `main`. Tags are compared with step 2's listing, which holds only the
      tags kept after the sweep. A tag that points at another commit than
      in step 2 was moved, and an administrator puts it back at its step-2
      commit. That tag push starts `release-images.yml` on the tag with
      every component; its runs are cancelled and deleted, not approved in
      Production. Any other tag is deleted with its runs unless its commit
      is on `main` and its `.github` and listed paths match main's recorded
      tip, or a reader reads the differing files at the tag. Each run of
      the last 30 days on a ref that a key or approver environment admits is
      compared only on its own workflow file, what that file reaches
      through `uses: ./` (at the base, and at the run's own version where
     the workflow file differs) and the paths the one-time read lists for it,
      such as `scripts/pr_risk.py` and `.github/pr-autopilot.yml` for this
      workflow. Those files must match what was read, or their versions
      are read, or the run is deleted. Runs on `dev`, which only
      `pr-autopilot` admits, may be checked after the key move's step 6, as
      long as that ends before the approver key is added (step 6 below).
      The run listing is sliced so that each slice returns all its runs,
      because the endpoint stops at 1,000 without an error.
      This repository had up to 712 runs in one hour of 12 Nov (read on
      13 Nov), so its slices are an hour or less. It also keeps
      `pull_request_target` runs, this workflow's PR-event runs among them,
      because they run main's workflow file whatever branch they report.
      The merged-PR runs, `pull_request` runs created at or after their PR
      was merged, have the branch it merged into as their ref and are
      deleted (20 such runs on `dev` for the PRs updated from 14 Oct to
      13 Nov). Such a run starts on a later edit of a merged PR (seen from
      14 minutes to about three hours after the merge), so the PRs are
      listed by their last update;
   6. the new release-bot key goes into the environments.

   Release jobs that use the key stop working from step 3 to step 6, which
   includes that hour. The dismissals come after the hour because the old
   key can post a new approval until it is deleted, and an installation
   token minted from it can for up to an hour after that (GitHub documents
   a one-hour token lifetime and does not list key deletion among the ways
   a token ends; documented by omission, not tested). Step 6 below (the
   approver key) waits for the whole one-time baseline, not only for these
   six steps.
6. **Confirm, then add the approver key.** On a throwaway PR into `dev`, let
   a workflow on the PR's branch try to approve that PR with its
   `GITHUB_TOKEN`. GitHub must refuse it, and the PR must show no approval.
   Add `AUTOPILOT_APPROVER_CLIENT_ID` (variable) and
   `AUTOPILOT_APPROVER_PRIVATE_KEY` (secret) to both environments only then,
   and only once the whole one-time baseline in the `deploy_github.tf`
   header is complete, so that its dismissals never remove an approval this
   App made. If the approver key was stored anywhere a branch workflow could
   read before then, generate a new private key for the App, delete the old
   one and store only the new one.
7. **Check live, before `PR_AUTOPILOT` is set to `on`,** that the App's
   approval counts. On a throwaway PR into `dev`, approve with a token minted
   exactly as the workflow does (pull-requests: write only). Then
   `gh pr view <n> --json reviewDecision,mergeStateStatus` must show `APPROVED`,
   and a merge state that no other rule holds. Use the API fields, not the
   merge button: organisation admins bypass the ruleset. Use a trusted
   author's PR that carries agent co-authored commits: organisation ruleset
   2289447 has `require_extra_approval_for_unattributed_changes` on (a GitHub
   preview setting), and one App approval must still give `APPROVED`. Then dismiss the
   approval with the same token. If `reviewDecision` stays `REVIEW_REQUIRED`,
   grant the installation "Contents: Read and write" if it lacks it, and read
   the same PR again. If the approval still does not count, the mint step in
   `low-risk-pr-autopilot.yml` must also request `permission-contents: write`
   (a code change, reviewed like any other). Until this check passes, every
   approved PR stops at `blocked` and the autopilot tells the PR so (see item
   7 of [Trust boundary and merge decision](#trust-boundary-and-merge-decision)).

   On a scratch repository with `dev`'s rules, also check an approval that
   names an older head. Push a commit that changes the diff, then approve as
   the App with `commit_id` set to the previous head. Record the HTTP status
   of that request and `reviewDecision`. If it shows `APPROVED`, only the
   controller's withdrawal (item 6 of [Trust boundary and merge
   decision](#trust-boundary-and-merge-decision)) stops one person from
   merging the new commits on that approval. Then confirm that the same
   token can dismiss it (`PUT .../pulls/<n>/reviews/<id>/dismissals`, HTTP
   200) and that a second dismissal answers 422. The controller counts only
   422 as already dismissed; any other answer posts a note asking a person to
   dismiss it. A rule that limits who can dismiss reviews must allow the App.
8. **Keep "Allow auto-merge" off** for this repository. It is off today
   (read-only check, 12 Nov 2026). With it on, a writer could enable
   auto-merge on a LOW PR, and GitHub would merge it the moment the App
   approves: before the final recheck, with the author's merge method, and
   without the `autopilot-merged` label or the post-merge dispatches.

## Trust boundary and merge decision

1. A `pull_request_target` run for a PR into `dev` only classifies the PR,
   writes its tier label and comment, and, for a trusted author's LOW PR
   without a hold, dispatches this workflow on `dev` with `pr_number`. Review,
   approval and merge happen **only in `workflow_dispatch` runs on `dev`**
   (from that dispatch, the sweep or a person). Since 8 Dec 2025
   `pull_request_target` takes the workflow file, `GITHUB_REF` and `GITHUB_SHA`
   from the default branch, `main`, whatever the PR's base branch, so its runs
   are not `dev`'s copy and never produce review evidence. `main`'s copy runs
   `dev`'s controller (its checkout names `ref: dev`). Privileged jobs sparsely
   check out only `dev`'s controller and policy, with checkout credentials not
   persisted. Every Python call in a job that holds a write token, a secret
   or the approver App's environment runs as `python3 -I`. That is wider
   than the rule in the `deploy_github.tf` header in `future-agi/deployment`,
   which requires it wherever the approver App's token is held. In isolated mode
   Python puts neither the script's directory nor the current directory on
   `sys.path` and ignores `PYTHON*` variables, so a module in the checkout
   cannot replace a standard one. The controller imports only the standard
   library. A test plants `pip`, `uuid`, `typing`, `json` and `argparse`
   modules (and a `pip` package) in a copy of the checkout and runs the
   workflow's own commands: none is imported, and the same runs without
   `-I` import them. The unprivileged `policy-tests` job runs the PR's own
   test code by design, with a read-only token and no secret, so it keeps
   plain `python3`. A
   dispatch run stops and waits for a fresh run if `dev` moved after it was
   dispatched (the checkout is not `GITHUB_SHA`, the run's `head_sha`) or after
   checkout (the checkout is not the live `dev` tip). The PR must already contain that tip:
   the controller always asks GitHub's compare API, because a recorded base
   SHA equal to the tip only means the PR was opened after that commit, not
   that its branch contains it. A PR that is behind `dev` spends no review
   round and waits. The bot posts one behind-dev note (marker
   `pr-autopilot:behind-dev`) that names the head only when merging `dev`
   in would let the run continue. Three things must hold. The PR passes
   every other part of the merge decision (item 2: trusted author, LOW, no
   hold, review key, hours, mergeability). No standing escalation names
   its head. Its push history needs no person (the who-pushed check in
   [Switches and administrative
   prerequisites](#switches-and-administrative-prerequisites)): every push
   came from a trusted author, the history reaches back to the
   branch's creation, and it already shows this head. The run reads that
   history before it posts the note. If the history needs a person, the
   run escalates at once and posts no note, because merging `dev` in does
   not lift that escalation. If the read fails, or the history does not
   show this head yet, the run posts nothing; the PR is not parked, so the
   next sweep reads again. A behind run does not read the history again
   once an escalation names the head, because it cannot tell what that
   escalation was for. So the escalation stays until the head changes or
   the branch contains the `dev` tip, even if the owner then adds the
   pusher to the trusted-author list. The author merges `dev` in and
   pushes, and the run on that head checks again. The note says that the
   autopilot reviews and merges only a head that contains the live `dev` tip, and that the
   author (or another trusted author) merges `dev` into the branch locally
   and pushes. It also says that a push or an "Update branch" by anyone
   not on the trusted-author list sends the PR to a person for good. A new
   head that is still behind gets the same comment, edited to name it.
   The bot marks the note "No longer applies", with fixed text, in three
   cases:
   - the first run that finds the tip contained;
   - a run on a behind head that a person now has (an escalation names
     the head);
   - any run, from a PR event or a dispatch, that finds the PR is a draft,
     has `autopilot-hold`, is not LOW, or has an author who is not on the
     trusted-author list. Hours and mergeability do not clear it, because
     they change without a push and the note would flip back and forth.
   If the PR waits behind `dev` again later, the bot edits that comment
   back. GitHub sends no notification for an edited comment, so the bot
   first posts one short new comment that names the head and points to the
   note. It does the same when the note is still live but names an older
   head, and the new head has taken in `dev` since that head. This happens
   when the author merges `dev` in and pushes, and `dev` moves again before
   the first run on the new head. The bot compares both heads with the live
   `dev` tip (GitHub's compare API). If their merge bases differ, the bot
   treats the new head as having taken in `dev`. A force push that moves the
   branch back to an older `dev` commit also changes the merge base, so it
   gets the comment too. A new head that only adds commits keeps the
   merge base and gets only the edit, because the author was already told
   about that wait. If the older head's compare fails, a merge base is
   missing, or the note names no head, the bot posts the comment. Anyone
   with write access can edit the note, so the head it names decides only
   whether this comment is posted. So the author is notified each time a
   wait starts: the first note, a note that comes back, and a merge of
   `dev` that `dev` overtakes. A push that does not merge `dev` in starts no
   new wait. The comment comes first: if it fails, the run fails, the note
   does not name the new head and does not park the PR, and the next sweep
   tries again. If the edit then fails, the next run posts the comment a
   second time. A change to the trusted-author
   list starts no run, and the sweep does not dispatch a PR that the list
   no longer covers. The note on such a PR stays live until the PR's next
   event (a push, a label, a draft change) or a manual dispatch. `dev`
   does not require up-to-date branches and the repository setting
   "Always suggest updating pull request branches" is off, so GitHub shows
   no out-of-date notice and no "Update branch" button on these PRs; the
   bot's comments are the only sign of the wait. The note does not suggest
   `gh pr update-branch`: whether that REST call works with the setting
   off is not known yet (rollout step 2). Manual dispatches on other branches are rejected.
   PR-event runs and the schedule both need this workflow on the default
   branch.
2. The controller classifies and audits the tier. It requires an open,
   non-draft, non-fork PR from an explicitly trusted author, a LOW classification,
   a present review key, no hold label, an allowed time and known mergeability.
   Every push to the PR's branch must also come from a trusted author (see
   above), and a dismissed autopilot approval of this head counts as a hold.
   Usually a maintainer dismissed it. GitHub's stale-approval rule and the
   autopilot's own withdrawal (item 6) also leave one when the branch moves
   away from that head and is later pushed back to it. That is on purpose:
   pushing a reviewed head back does not buy another attempt.
3. It reads classic check contexts through GraphQL `Ref.refUpdateRule`, the
   non-admin protection view, and applicable active rules through
   `GET /repos/{owner}/{repo}/rules/branches/dev`. Required checks are the union
   of those and the policy's minimum, never a replacement list. The non-admin
   view has no classic strictness field, and the controller does not need one:
   whether `dev` requires up-to-date branches is not read. The controller
   proves with the compare API that the PR head contains the live `dev` tip,
   at reservation, at each recheck, and again as the last read before the
   merge call (item 7).
   From the same rules call, an active `pull_request` rule must have
   `dismiss_stale_reviews_on_push`. The bot's approval names one head, but
   GitHub counts it for the PR, so only that setting stops a later push from
   being merged on it. It acts only on a push after the approval, and a push
   that only merges `dev` into the branch may leave the approval counting
   (item 7). For a push
   that lands while the approval is being posted, the controller withdraws
   its own approval (item 6). A missing stale-approval rule or inaccessible
   evidence stops automation. Check
   runs and legacy commit statuses are read with their separate permissions
   (`checks` and `statuses`). Skipped runs are ignored, and the newest
   result for each check that did not skip must be `success` on the exact
   head SHA. The expected integration ID is
   honored when supplied by a ruleset; otherwise a GitHub Actions check-run
   identity is required. Legacy status contexts with no verifiable App identity,
   unavailable API evidence, merge queues and required-workflow rules wait for
   a separate integration/human review instead of weakening verification.
4. The AI job runs on a **different runner with a read-only token**. Its explicit
   prompt selects the action's prompt-only agent mode. It receives JSON-encoded
   patches as data, not PR title/body/comments or executable files. Built-in
   tools, MCP tools and skills are disabled. No PR checkout, dependency install,
   shell interpolation of the diff, write token or patch execution is allowed.
   Commit messages and the PR title therefore reach `dev`'s history unreviewed,
   as they do after a person's approval. release-please reads commit messages
   on `main`.
5. A trusted follow-up job validates the structured output and publishes the
   separate **`PR Autopilot review`** Check Run on the **PR head SHA**. The
   publish step decides one outcome and exits 0 whenever it published:
   - **blocked** (check conclusion `failure`): a valid verdict with blocking
     findings, even if the head or `dev` moved during the review; or
     malformed, incomplete or wrong-SHA output from an AI job that reported
     success.
   - **retryable** (conclusion `cancelled`): a non-blocking verdict whose head
     or `dev` base moved (or could not be confirmed) during the review, or an
     AI job that was skipped, cancelled, failed or timed out. The Claude step
     has a 12-minute step timeout under the job's 15, so a slow review fails
     the step instead of timing out the job. A model that returns no
     structured output also lands here, not under **blocked**: the pinned
     action then fails the step (`base-action/src/run-claude-sdk.ts`). A diff
     that leads the model not to answer is therefore sampled again after each
     branch change, up to the cap of nine attempts.
   - **passed** (conclusion `success`): everything else.

   The publish step reads the pending check first, retried on no answer, 5xx
   or 429. A readable check that is not this run's pending reservation stops
   the step with no outcome. If the check stays unreadable, a blocking verdict
   is still **blocked** and anything else becomes **retryable**, because
   nobody can confirm the check was left alone. If the check cannot be
   completed, the outcome is still emitted: the check's conclusion is never
   read, and a check left in progress only shows as pending on that head.

   Then exactly one of three marker steps runs; each only runs `true`. They are
   named `Review passed: head <head> base <base> patch <patch>`, `Review
   blocked: ...` and `Review retryable: ...`, rendered from the trusted prepare
   output (templates in `OUTCOME_STEPS` in `scripts/pr_risk.py`; a test checks
   that the workflow matches them). The `approve-and-merge` job, which holds
   the merge step, starts only after this job finished, and only after
   `passed` in the same run or for a reused pass with no reservation in this
   run. A failed result comment does not change the outcome or fail the step.
6. Before approval and again before merge, the controller re-reads the PR,
   files, rules, current-head CI, push history, hold/draft/author state, time,
   and review result. Outstanding human change requests block it, and so does
   a dismissed approval of this head by the approver App. Review evidence comes **only from
   step names in the cited run**, read from attempt 1
   (`GET .../actions/runs/{id}/attempts/1/jobs`). It never comes from a check
   run's conclusion, name or `external_id`, because any Actions token in the
   repository can edit those, while a finished run's step names cannot change.
   A cited run must be a `workflow_dispatch` run of this workflow on `dev`,
   for this PR number (the run name is `PR autopilot #N`), in this repository,
   and its `head_sha` must equal the base the record names. A pass is reused
   only when that run shows the `Review passed` marker for exactly this head,
   base and diff digest, and no attempt of the same diff was blocked or never
   finished. It labels and comments each automated approval, refreshes every
   gate **after** the audit writes and existing-approval lookup, then approves
   as the approver App. An existing approval of this head by that App is not
   duplicated, and the audit comment is posted once per head. An audit
   label/comment without a submitted approval can remain if this final refresh
   catches a hold or another changed gate, or if GitHub refuses the approval.
   A refusal (HTTP 403 or 422) fails the run with a message that names the
   App's installation and permission. It posts no second audit comment, and
   one note per head tells the PR (see [Approver App and owner
   settings](#approver-app-and-owner-settings)).

   The approval names the reviewed head, and a push can land between the last
   read and the approval. If the recheck after the approval, or the merge
   call, then fails, the controller re-reads the PR. When the head is no
   longer the reviewed head, it dismisses the approval this run posted, as the
   App, with a fixed message. An answer of 422 counts as done: usually
   GitHub's stale-approval rule already dismissed it, because the push came
   after the approval. Any other answer, including 404, posts one note asking
   a person to dismiss the App's approval. A dismissed review still exists, so
   a 404 means GitHub did not find the review or route, and the approval may
   still count. In every case the run still fails with its original error.
   A failed re-read, or the same head, keeps the approval, so the next run
   reuses it.
   An approval from an earlier run is never withdrawn: it was posted while its
   head was current, so the stale-approval rule covers later pushes. This
   matches the bot autopilot in `future-agi/deployment` (#438), which also
   withdraws its approval when the head moved.
7. It waits for GitHub's mergeability/protection state to be `clean` or
   `unstable` (`unstable` means a check that is not required is failing or still
   running; the required checks were already verified above), then calls
   the merge API with the expected head SHA and `merge_method: merge`. GitHub
   remains the final authority for branch protection; no bypass token or
   `--admin` equivalent is used. If the state is `blocked` right after the
   approval, the run fails and the next sweep tries again, because GitHub may
   still be catching up. If it is still `blocked` when the approval already
   existed, a rule needs something the autopilot cannot give (a code-owner
   review, another approval, resolved conversations, or an approval GitHub
   does not count). The bot then posts one comment per head saying a person
   must review or merge the PR, and the run exits green. While that comment
   names the PR's head, the sweep dispatches the PR about once an hour, so it
   merges within about an hour once a person supplies what the rule needs. A
   dispatch on `dev` with `pr_number` checks at once. The controller does not
   delete the head branch. This repository has `delete_branch_on_merge` on,
   so GitHub deletes it after the merge.

   `dev` does not require up-to-date branches, so GitHub would merge a head
   that is behind `dev`. The last read before the merge call is therefore the
   `dev` tip and a compare with the head. If the head no longer contains the
   tip, the run stops with "head or dev base changed" and merges nothing.
   The approval stays, because the head did not change. The next run finds
   the PR behind `dev`, posts the behind-dev note (item 1) and waits until
   the author merges `dev` in and pushes. The new head is then reviewed
   again on the new base. Three gaps remain:
   - If `dev` moves in the moment between that read and the merge call, the
     PR merges one merge behind `dev`. The post-merge dispatches then run CI
     on the combined `dev` tip.
   - While the approval stays on a PR that fell behind `dev`, non-strict
     protection lets a person, the author included, merge it by hand on that
     approval without updating the branch, as with any person's approval
     today.
   - A review note cites GitHub's documentation: merging the base branch
     into a PR does not dismiss approvals. Neither that page nor the
     behaviour was checked here. If it holds, after the author merges
     `dev` into the branch the App's approval of the older head may still
     count. If the review of the new head then blocks or does not finish,
     the PR shows that approval beside the bot's escalation, and a person
     can merge on it. A maintainer
     who wants to stop that dismisses the App's approval.

The review check is **mandatory for the autopilot**, independently of the
managed Claude review UI/check, which never fails. It is not installed as a
blanket required branch check by this code: it is only produced for eligible
LOW PRs with a review key and passing CI. Making it required for *every* PR
would block the intentionally manual medium/high and disabled-feature paths.
An administrator must design a human-reviewed alternative before imposing it
repository-wide. Normal protection requirements still apply to every merge.

GitHub's merge API atomically enforces the expected head and its branch rules.
It cannot atomically compare a label or working-hours variable. Mutable PR
metadata is read last before each write, the `dev` tip is read last before the
merge call (item 7), and a hold/cancellation should be used for an immediate
operational stop.
Variables are an Actions run snapshot, not a live external kill-switch service.

## AI review contract and bounded retries

The action is pinned to the commit of `anthropics/claude-code-action` v1.0.271
(`4fa93a88de49bbef68b10eb15aabedf28ca0e1ed`, tagged 10 Nov 2026), because this
job holds the API key and a tag can be moved. The tag-to-commit mapping was
read from the action repository's git API. That commit installs Claude Code
2.1.322 and bundles Agent SDK 0.3.322. The earlier pin, v1.0.120, installed
Claude Code 2.1.139, which predates `claude-opus-5-5` (Claude Code added it in
2.1.280) and two `--json-schema` fixes (2.1.187 and 2.1.205).

Checked in the action's source at that commit, without running it:

- `action.yml` still declares every input the workflow sets
  (`anthropic_api_key`, `github_token`, `allowed_bots`, `track_progress`,
  `include_fix_links`, `classify_inline_comments`, `display_report`,
  `show_full_output`, `prompt`, `claude_args`) and the `structured_output`
  output.
- An explicit `prompt` on a `workflow_dispatch` run selects agent mode
  (`src/modes/detector.ts`). Agent mode writes the prompt to a file and adds
  no MCP server unless `--allowedTools` names one; it does not check out the
  PR or load its context (`src/modes/agent/index.ts`). A run started by
  `github-actions[bot]` passes the actor check through `allowed_bots`.
- The steps that run in this job are the same as at v1.0.120: install Bun
  (pinned `oven-sh/setup-bun`, now Bun 1.4.4 without its cache), install the
  action's locked dependencies, then install Claude Code with
  `https://claude.ai/install.sh` and run it. The step that apt-installs
  bubblewrap and socat runs only when `allowed_non_write_users` is set, which
  this job does not do, at either pin. One post-step, "Post buffered inline
  comments", runs under `always()` at both pins unless
  `classify_inline_comments` is `'false'`, and it gets the API key and the
  job token. The workflow sets that input to `'false'`, so the step is
  skipped. Agent mode buffers no inline comments, so the step would have
  nothing to post. The input's only other use is an environment variable of
  the inline-comment server, which agent mode does not start. A contract test
  fails if the input is missing or has another value. The other optional
  steps (a custom Bun path, SSH signing cleanup, App token revocation) are
  off, because the workflow passes `github_token` and sets neither
  `path_to_bun_executable` nor `ssh_signing_key`.
- `--model` and `--max-turns` now become Agent SDK options rather than CLI
  arguments; the other flags still pass to the CLI. The runner now also fails
  the step when a run reports success after more turns than `--max-turns`.
  It still fails the step when `--json-schema` was given and no structured
  output came back, and now also when a successful result has `is_error` set.

**Diff size.** The diff reaches the model only through the action's `prompt`
input. The action has no other prompt input, agent mode writes that input
to its own prompt file, and the review has no tools to read anything else.
The action's run step exports the prompt twice: as `PROMPT`, and inside
`ALL_INPUTS`, which is `toJson(inputs)` (`action.yml` lines 291 and 317). The
runner's `toJson` (`ToJson.cs` in `actions/runner`, with Newtonsoft's default
string escaping) escapes every backslash and quote of the diff's JSON a
second time. Linux refuses to start a process when one environment string
is longer than 131,072 bytes. The review step would then fail the same way
on every retry. So the policy's `max_diff_bytes` (120,000) bounds the diff
in that twice-escaped form. A larger diff goes to a person on the first run,
with one escalation note. The rest of `ALL_INPUTS` (the instructions, the
other inputs, and 1,024 bytes allowed for each of the two secrets) is 4,768
bytes. The longest accepted `ALL_INPUTS` string is therefore 124,776 bytes,
6,296 under the limit. A contract test rebuilds `ALL_INPUTS` from the
workflow's inputs and the pinned action's defaults, and fails if the largest
accepted diff does not fit. Plain ASCII text keeps almost the whole limit: a
200-line prose diff of 119,679 bytes as JSON is accepted. Quotes,
backslashes and non-ASCII text are escaped more, so such diffs go to a
person sooner: a 200-line CJK diff at about 103 KB as JSON, or a diff of
only quote characters at about 60 KB. No real runner has run a diff at the
limit.

One flag did not reach the CLI as written, at either pin. The action drops an
empty quoted value, so `--tools ""` became a bare `--tools`, and the CLI's
argument parser then took the next flag, `--disable-slash-commands`, as the
tool list. The workflow now passes `--tools=`, the form the Agent SDK itself
writes for an empty tool list, which the CLI reads as no built-in tools. A
contract test applies the action's rule to `claude_args` and fails on an
empty value or a flag left without one. None of this has run against the
API: rollout step 2 confirms it in a disposable repository first.

It uses `claude-opus-5-5`, at most three turns per invocation, and no fallback
that could convert an unavailable review into a pass. The model must return:

```json
{
  "blocking_findings": false,
  "complete": true,
  "reasons": ["A concrete explanation of the review verdict"],
  "head_sha": "<the reviewed 40-character commit SHA>",
  "base_sha": "<the reviewed 40-character dev SHA>"
}
```

The example SHAs above are placeholders, not valid verdicts. The validator
requires exact matching SHAs, actual booleans, no extra fields and 1–20 nonempty
reasons. There is no string-to-boolean coercion. Binary, missing, inconsistent
or oversized patches require a person; the model never certifies a truncated
diff. The bot posts one escalation for them and the run stays green; a later
reviewable diff marks it as no longer applying. Review findings and the
decision are recorded. The controller does not log raw diffs, but the action prints
the prompt, which contains the patches, in the AI job's log.

Each attempt is reserved **before** the AI call, three times, in this order:
a bot-authored PR comment carrying the record (PR, head, base, run ID, round,
diff digest); a commit status on the head with context
`pr-autopilot/reservation` whose target URL is the run
(`https://github.com/<repo>/actions/runs/<id>`); and an in-progress `PR
Autopilot review` check run on the head with the same record as its
`external_id`. The status's state (`success`, description "AI review attempt
N reserved. A record, not a verdict.") is never read. In the same run, the
prepare job then runs a step named `Review reserved: head <head> base <base>
patch <patch>` (template `RESERVED_STEP` in `scripts/pr_risk.py`), rendered
from the policy step's output; it runs even if that step failed after
reserving. The budget counts **different diffs**, not commits: the digest
covers the exact patches the model reviews (hunk line numbers ignored).

Each of the three writes is tried up to three times on no answer, 5xx or 429.
A 403 is not retried. GitHub's secondary rate limit often answers 403, but the
controller does not read response headers, so it cannot tell that from a
refusal, and a retry within seconds would not outlast the limit. Such an
attempt ends as **retryable** (below), and the PR waits for a branch change.
A lost response can only repeat a comment or status, which the controller
folds into one attempt by run ID. Before it posts the check again, it looks for
this record's check on the head, so a lost response does not leave a second
check in progress. If a write still fails, the policy step fails before it
emits `review=true`. The AI job then does not run, and the prepare job runs a
second step, `Review retryable: head <head> base <base> patch <patch>`, for the
same reservation. No verdict exists that this could re-roll, so the attempt is
**retryable**, not barred, and the bot tells the author to update the branch.

The controller builds a PR's attempt history from the ledger comments, the
reservation statuses and the review check runs (name `PR Autopilot review`,
app `github-actions`) on every head the PR has had. The heads are the PR's
commits, both sides of each force push in its timeline (GraphQL
`HEAD_REF_FORCE_PUSHED_EVENT`), and both sides of each push in the branch's
activity feed. Anyone with write access can delete or edit a comment, and any
Actions token in the repository can rename a check run or change its
`external_id` (`PATCH .../check-runs/{id}` accepts both). A commit status
cannot be edited or deleted; GitHub only appends them. So deleting the
comment and renaming the check does not remove the attempt: the status still
indexes it. A status carries only the run link. The controller rebuilds the
attempt from the run itself: the PR number from the run name `PR autopilot
#N`, and the head, base and diff from its `Review reserved` step. Anyone with
write access can post a status, so a forged one can add an attempt (or send
the PR to a person) but cannot shape one.

The following go to a person (an escalation comment, no review, no merge):

- a history list that is truncated (more than 100 commits or force pushes) or
  returned in an unexpected shape;
- a review check on one of the PR's heads whose record cannot be parsed;
- a reservation status that does not link to a run of this repository, whose
  run is not a trusted `dev` dispatch run with exactly one `Review reserved`
  step, that sits on a head its run did not reserve, or whose run's commit is
  not the base the run reserved;
- a record that cites a run that is not the trusted `dev` dispatch run for its
  revision, or whose run or jobs GitHub reports as gone (HTTP 404 or 410, for
  example a deleted run);
- two records for one run that disagree, a run whose `Review reserved` or
  outcome step names a different head, base or diff than its record, or a run
  with no `Review reserved` step for that record. The last case stops an
  edited record from citing some other run that has no outcome, which would
  move a never-finished bar onto a made-up diff. A genuine run cancelled in
  the moment between its reservation and that step also lands here.

Any other failed request while reading this history (HTTP 5xx, 403 or 429
rate limits, a timeout, an unreadable body or a GraphQL error) is not evidence
that anything was deleted or edited. The run fails without an escalation
comment and approves nothing; the next run retries. When a later run gets past
these checks and goes on to review, reuse a pass or wait for a branch update,
it rewrites any standing escalation comment to say it no longer applies, with
fixed text (the comment's edit history keeps the earlier escalation), so a PR
never shows "a person must review" beside an automatic merge. It does this
before reserving anything.

For each attempt the controller reads the outcome marker of its run. An attempt
whose run shows its `Review reserved` step but no outcome marker never finished
(for example, the run was cancelled during the review) and counts like a
blocked one.

- A diff is **barred** if any attempt of it, on any head or base, was blocked
  or never finished. It is not reviewed again; a person must resolve the
  finding or review the PR. Merging dev in does not re-roll a blocking verdict.
- A diff whose attempt was **retryable** (stale head or `dev`, a failed AI
  job, or a review that could not start) is reviewed again after the branch
  changes. The same head and base pair
  is not retried, so an outage cannot spend attempts every 15 minutes. Later
  runs on that pair post nothing new: the bot's result comment already tells
  the author to update the branch from `dev` (merge it in locally and push)
  or push a commit. If `dev` has moved on by then, the branch is behind and
  the behind-dev note (item 1 of [Trust boundary and merge
  decision](#trust-boundary-and-merge-decision)) says so as well. This is
  the first of the two options the round-two review offered.
- Merging dev into the branch without changing the author's own diff gets a
  fresh review on the new base at no cost to the diff budget. This matters
  because the autopilot merges only a branch that contains the live `dev`
  tip, and `dev` moves often.
- At most **three different diffs** and **nine attempts** per PR are reviewed,
  counted from the union of comments, reservation statuses and check runs.
  After that the controller
  posts a human escalation.
- A record that names another PR (the same head pushed to two PRs) counts only
  toward barring this diff, not toward this PR's budget or reuse.

The bot does not modify code or dismiss findings. The author fixes blocking
findings with a reason and submits a new revision; a person takes over if the
budget or automated path cannot complete. A passing review of the exact head,
base and diff can be reused while the other merge gates are rechecked.

**Accepted limit, for the owner to confirm:** the budget and the barred-diff
rule are per PR. Opening a **new PR** with the same diff from a new commit
starts with no history, so a blocked diff can be reviewed again that way. A
repository-wide record keyed by the diff digest would close this. Until the
owner decides, this is a documented limit; the trusted-author list,
LOW-only paths and the review on every attempt still apply.

**Accepted limit, for the owner to confirm: renamed branches.** The branch
activity feed is read by the branch's current name
(`activity?ref=refs/heads/<head ref>`). Two guarantees depend on it: that
every push came from a trusted author, and finding an amended head that was
force-pushed over a later commit (that head is in neither the PR's commits
nor its force-push timeline). Whether the feed for the new name still lists
pushes made before a rename has not been observed. If it starts over without
a `branch_creation` row, the controller sends the PR to a person. If a rename
writes a fresh `branch_creation` row for the new name, earlier pushes drop
out of both checks without notice. Until the rename rehearsal in the rollout
list confirms the feed keeps them, this is a documented limit.

**Workflow reruns do not invoke review or approve/merge.** All four controller,
review and approval jobs require `github.run_attempt == '1'`; a runtime guard also precedes
any AI action. This includes “Re-run failed jobs”, a specific AI-job rerun, and
full workflow reruns. They cannot reuse the successful prepare job's saved
reservation to buy extra invocations. Use a **new** dispatch on `dev` or the next
scheduled evaluation instead; the attempt history still applies. A failed
or cancelled first attempt is not refunded. The verdict publisher checks the
original run identity and pending check record before writing. Completed
verdicts cannot be overwritten by replay, even within the original attempt.
Evidence is read from attempt 1 of each run, so a rerun of a failed merge step
does not hide the original `Review passed` marker: a fresh evaluation reuses
that pass without republishing it.

References checked while implementing the adapter:

- [Action README](https://github.com/anthropics/claude-code-action/blob/main/README.md)
- [Pinned action inputs/outputs](https://github.com/anthropics/claude-code-action/blob/4fa93a88de49bbef68b10eb15aabedf28ca0e1ed/action.yml)
- [Pinned usage and structured output](https://github.com/anthropics/claude-code-action/blob/4fa93a88de49bbef68b10eb15aabedf28ca0e1ed/docs/usage.md)
- [Pinned mode selection](https://github.com/anthropics/claude-code-action/blob/4fa93a88de49bbef68b10eb15aabedf28ca0e1ed/src/modes/detector.ts)
- [Pinned agent-mode behavior](https://github.com/anthropics/claude-code-action/blob/4fa93a88de49bbef68b10eb15aabedf28ca0e1ed/src/modes/agent/index.ts)
- [Pinned claude_args parsing](https://github.com/anthropics/claude-code-action/blob/4fa93a88de49bbef68b10eb15aabedf28ca0e1ed/base-action/src/parse-sdk-options.ts)
- [Pinned structured-output check](https://github.com/anthropics/claude-code-action/blob/4fa93a88de49bbef68b10eb15aabedf28ca0e1ed/base-action/src/run-claude-sdk.ts)
- [CLI tool restrictions](https://code.claude.com/docs/en/cli-reference)
- [GitHub Ref and RefUpdateRule fields](https://docs.github.com/en/graphql/reference/git)
- [GitHub Actions token permissions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions)
- [GitHub workflow and job reruns](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs)

## After merge and audit recovery

A merge by `GITHUB_TOKEN` does **not** trigger ordinary push workflows. The
controller explicitly dispatches existing workflows on `dev` after a confirmed
merge. Each entry in `post_merge_workflows` uses the paths of that workflow's
push filter, so a dispatch happens when a person's merge of the same files
would have started a push run:

- `backend-ci.yml`, `e2e-ci.yml` with `stack: distributed`, and
  `sdk-compatibility.yml` (SDK Compatibility), after every merge;
- `standalone-ci.yml` (Standalone install CI) and `update-ci.yml` (Update CI)
  when the merge changes a file their push filters name, for example any
  backend Python test;
- `e2e-harness-selftest.yml` (E2E Harness Self-tests) when the merge changes
  an e2e flow spec (`e2e/**` in its push filter). It reached `dev` on 12 Nov
  2026 (#3935);
- for frontend paths only, `frontend-develop.yml` and `frontend-dev-cdn.yaml`;
- the CDN dispatch respects `DEV_CDN_AUTO_DEPLOY=false`, and always passes
  `branch: dev`, `repair_spa_routes: false`. This is necessary because that
  workflow's manual entry otherwise bypasses its existing push switch.

A dispatch is not always the same as a push run:

- Standalone install CI, when dispatched, always runs the install job, also
  builds arm64, and runs the upgrade from v1.41.1 (a 120-minute limit). A push
  runs only what its file filter selects, and no upgrade job.
- Update CI, when dispatched, also runs its two real upgrade jobs
  (`automatic-upgrade`, with a 355-minute limit, and `automatic-a-b-c`). A
  push skips both.

The other `dev` push workflows that accept a dispatch are not dispatched:
AgentCC Gateway CI, API Contracts, Collector and compose CI, Helm chart CI,
Images CI and Update PostgreSQL probe. None of their push filters matches a
LOW path, so a person's merge of a LOW file would not start them either.

Python types (`python-types.yml`) is the one `dev` push workflow that a LOW
merge would start but that is not dispatched. It has no `workflow_dispatch`
trigger. It is listed, with this reason, in `post_merge_not_dispatched`: its
mypy run excludes `tests/` (`[tool.mypy]` in `futureagi/pyproject.toml`), and
every LOW Python glob is under a `tests/` directory, so no LOW merge can
change its result.

A test reads every workflow file and fails if a `dev` push workflow that a
LOW merge would start is neither dispatched under the same paths nor listed
in `post_merge_not_dispatched` with a reason, or if a dispatch's inputs do
not fit the workflow. It also fails if a LOW path could be a Python file
outside a `tests/` directory or another mypy input, so a wider LOW allowlist
forces a review of the Python types exemption. The other mypy inputs it checks
are `futureagi/mypy-baseline.txt`, `scripts/mypy_baseline.py`,
`scripts/test_mypy_baseline.py`, and `futureagi/mypy.ini` and
`futureagi/.mypy.ini`, which mypy reads before `pyproject.toml`. It fails if
`[tool.mypy]` no longer excludes `tests/`, and if a listed workflow is no
longer a `dev` push workflow. It counts every push trigger form that can run
on `dev`, and reports `branches-ignore`, `paths-ignore` and negated branch patterns as not modelled.
Widening the LOW allowlist can therefore require new entries. The test runs in
the `policy-tests` job on every PR into `dev` that changes any workflow file
or an autopilot file. A PR that changes a `dev` push filter or adds a `dev`
push workflow may need a `post_merge_workflows` entry, or, if the workflow
has no `workflow_dispatch` and a LOW merge cannot change its result, a
`post_merge_not_dispatched` entry with a reason; its `policy-tests` check
fails until it has one. `policy-tests` is not a required check and must
not become one, because a path-filtered required check leaves every other PR
pending. So it shows the drift on the PR that causes it but does not block
the merge. A workflow change that reaches `dev` another way, for
example a merge from `main`, is caught only at the next such PR. A PR that
changes only `futureagi/pyproject.toml` does not run the test, so dropping
`tests/` from the mypy exclude is also caught only at the next such PR.

Those workflows execute their own trusted post-merge code and use their own
permissions/secrets. This lane adds no production credentials and does not
change release/main automation. Dispatches test/deploy the current `dev` ref,
which can include later merges; they are not proof that a particular merge has
already passed post-merge verification.

The controller reads the changed files it needs for this choice **before** the
merge call. After GitHub confirms the merge, the run works in this order:

1. It posts the merged record: a comment that starts with
   `<!-- pr-autopilot:merged -->`, names the merge SHA and the workflows it is
   about to dispatch, and carries the approval audit. A server error or a
   lost answer is retried, three attempts in all. If every attempt fails, the
   run reads the PR's comments back, because a write whose answer was lost
   may have landed.
2. It dispatches each workflow on `dev`, three tries each. One failed
   dispatch does not skip the others.
3. It adds `autopilot-merged`.
4. If the plan, a dispatch or the label failed, it posts a second merged
   comment that names what did not start, for a person to dispatch on `dev`.
   The job then fails and names the same items.

The record comes first so that no follow-up is ever started twice: a sweep
that finds a merged record never dispatches for that PR (see below). This has
a cost. A run that ends partway through step 2 (the job's 10-minute limit or
a lost runner) leaves the rest of the follow-ups unstarted, and no sweep
starts them. Only the failed job shows it, together with a record that has no
`autopilot-merged` label and no second comment. A person then checks which of
the record's workflows started on `dev` and dispatches the rest. If the record
cannot be written and the read-back does not find it, the run dispatches
nothing, adds no label and fails, and a later sweep starts the follow-ups. If
the read-back fails as well, the run cannot tell whether the record landed.
Its error then names the workflows to dispatch by hand in case the record is
on the PR, because no sweep would start them then.

Approval comments, review records, tier labels, review check details, merge
comments and job summaries identify the policy/base SHA, head SHA, checks and
workflow run. The merge run applies `autopilot-merged` only after the API
confirms a merge. If an audit or dispatch request fails after merge, the job
fails but does not undo the merge or claim the downstream workflow ran.

**A merge whose run ended before its record.** The merge call is never
sent twice. If GitHub merged but the run got no answer, a server error or an
answer that does not confirm the merge, the run fails with no record,
dispatch or label, and its summary says the next sweep takes over. A run whose
record could not be written (and was not found on read-back) ends the same
way, as does a job that times out or loses its runner between the merge and
the record. Each sweep
therefore reads one page of the 100 most recently updated closed PRs into
`dev` (one request; on 12 Nov 2026 that page covered nine days). A PR is
recovered when all of these hold:

- it was merged at least 15 minutes ago, longer than the `approve-and-merge`
  job's 10-minute limit, so the merging run has ended;
- it has `autopilot-approved` and not `autopilot-merged`;
- `GET .../pulls/<n>` shows it merged by `github-actions[bot]` (the list rows
  carry no `merged_by`), on a head that the bot's approval audit comment
  names;
- no comment by `github-actions[bot]` starts with `<!-- pr-autopilot:merged -->`.

The sweep then posts that merged record first, naming the workflows it is
about to dispatch, with the same retries and read-back as the merging run.
It dispatches them on `dev` (respecting `DEV_CDN_AUTO_DEPLOY=false`, which
the workflow passes to the sweep for this), and adds `autopilot-merged`. A
dispatch that still fails after three tries is named in a second comment and
in the failed sweep. Because the record comes first, a later sweep never
dispatches the same merge again. If the record cannot be written, the sweep
dispatches nothing for that PR, fails and names the reason, and the next
sweep tries again. A person's merge of an approved PR is left alone: it
started the push workflows itself. The dispatches test the current `dev` tip,
which may include later merges. The merging run does not re-read the PR to
find out whether its merge happened; the sweep covers that case and the
others above within about 15 to 30 minutes.

An `autopilot-approved` label on a merged PR without `autopilot-merged` is
still a useful reconciliation queue (the query below), not proof that the
bot performed the final merge. A PR in that queue that has a merged record
but no second merged comment is one whose run ended during its dispatches, or
lost both the label and that comment. Check which of the record's workflows
started on `dev` after the merge, and dispatch the rest by hand.

## Guardrail: revert rate

Track the weekly and rolling-30-day **revert rate of autopilot-merged PRs**:

`autopilot-merged PRs later reverted / all autopilot-merged PRs in the cohort`.

Track incidents separately, linked to the source PR, and compare against the
human-reviewed cohort. Retain the labels on reverted PRs. Reverts need the
normal human review path; do not automatically retry/re-merge reverted changes.
A nonzero serious incident or an increase in revert rate is a reason to pause
and investigate, not to loosen the allowlist.

From a repository checkout, this read-only query lists the denominator:

```bash
gh pr list --base dev --state merged --label autopilot-merged --limit 1000 \
  --json number,title,url,mergedAt,mergeCommit,labels
```

For a dated cohort, add `--search 'merged:YYYY-MM-DD..YYYY-MM-DD'`. GitHub search
has a 1,000-result ceiling; split larger periods and paginate/export before
computing a denominator. Cross-check audit gaps with:

```bash
gh pr list --base dev --state merged --label autopilot-approved \
  --search '-label:autopilot-merged' --limit 1000 --json number,url,mergedAt
```

For each confirmed autopilot merge, find its revert PR/commit by the merge SHA
and the standard `This reverts commit ...` message, verify that the change was
actually reversed, and count each original PR once. GitHub labels alone cannot
detect partial or hand-written reverts, so review incident/revert records too.
This implementation records the queryable denominator; it does not pretend to
have measured a live revert rate during offline development.

## Stop, rollout and verification

**Stop new automation:** unset `PR_AUTOPILOT` or set it to `off`. For an immediate
stop, also cancel active autopilot runs; their variable snapshot may still be
`on`. With the switch off no sweep runs, so a merge whose run ended before its
merged record is not recovered until the switch is on again; the reconciliation
query in [Guardrail: revert rate](#guardrail-revert-rate) lists such PRs.
Add `autopilot-hold` to affected open PRs. A missing review key also prevents
new automatic approvals/merges, and so does a `PR_AUTOPILOT_TRUSTED_AUTHORS`
value that names no login in the policy list. Explicit off plus cancellation
is the clearest emergency procedure. Any writer can change these variables
back, so a stop that only a reviewed change can undo is a policy PR into
`dev` that empties `trusted_authors`. Removing the approver App's
key from the `pr-autopilot` environment also stops every approval; each PR
that passes review then gets one note per head saying so. Do not
remove normal required checks to make a stopped PR mergeable. An App approval
already recorded remains visible; a maintainer can dismiss it if it must not
count toward a later manual merge. Dismissing the App's approval also stops
automation for that head, like `autopilot-hold`: the bot does not approve it
again, and only a new revision (or a person merging by hand) moves the PR on.
The same holds for an approval that GitHub's stale-approval rule or the
autopilot itself dismissed, if the branch is later pushed back to that head.
A merged change must be reverted through the usual reviewed process.

Suggested rollout:

1. Human-review these HIGH-risk CI/policy changes. Confirm branch protection,
   a `pull_request` rule that dismisses stale approvals on push, no merge
   queue or required-workflow rule on `dev`, merge-commit support and
   current-head check SHAs. Before
   this workflow reaches `main`, and before 2 Dec 2026, the owner adds the
   Actions event policy that allows `pull_request_target` for
   `.github/workflows/low-risk-pr-autopilot.yml`, and completes the owner
   settings in [Approver App and owner settings](#approver-app-and-owner-settings).
2. Keep `PR_AUTOPILOT` unset during CI validation. Run the synthetic tests and
   actionlint; verify the actual action/API contract in a disposable repository
   before any eligible real PR can merge. Rehearse with **exactly** each job's
   declared `GITHUB_TOKEN` permissions, not a personal/admin token: the GraphQL
   `refUpdateRule` query, active branch rules, check runs and commit statuses must
   all succeed. Confirm missing-scope/error cases deny automation, a `dev`
   without the stale-approval rule is rejected, and a partial AI-job rerun
   makes no new model call.
   Also confirm these GitHub behaviors, which development could not observe:
   - first, one billable review with the pinned action (v1.0.271) and a test
     API key: the execution file's init message lists no tools and no slash
     commands (the `--tools=` form), a `structured_output` verdict comes back
     within three turns, a forced failure (for example a model name the
     API rejects) ends as **retryable**, and the job log shows the action's
     "Post buffered inline comments" step as skipped;
   - `actions/runs/{id}/attempts/1/jobs` lists the `Review reserved` step and
     the outcome marker step with their rendered names; the run object of a
     dispatch run has `head_branch` `dev` and `head_sha` equal to the
     dispatched commit; and both hold after "Re-run failed jobs";
   - the branch activity endpoint and the GraphQL commits and
     `HEAD_REF_FORCE_PUSHED_EVENT` timeline query succeed with the job token,
     and the oldest activity row of a freshly pushed branch is
     `branch_creation`;
   - the prepare job's token (`statuses: write`) can post the
     `pr-autopilot/reservation` status, and `commits/<sha>/statuses` still
     lists it for a head that a force push replaced;
   - an end-to-end rehearsal of the rename case: get a blocking verdict on a
     test PR, then from a workflow on another branch whose job has `checks:
     write`, rename that review check (`PATCH .../check-runs/{id}` with a new
     `name`) and blank its `external_id`; delete the ledger comment; amend
     the commit without changing the diff and force-push it. The next run
     must not reserve a review and must escalate as a blocked diff. The
     controller does not depend on GitHub refusing that `PATCH`, which the
     REST documentation allows;
   - rename a test PR's branch and check whether
     `activity?ref=refs/heads/<new name>` still lists the pushes made before
     the rename (see the renamed-branches limit above);
   - deleting a workflow run leaves its reservation status in place, so the
     next run sends that PR to a person instead of dropping the attempt;
   - when the Claude step hits its 12-minute step timeout, `needs.ai-review.result`
     is `failure` and the gate job still runs, so the outcome is retryable.
     If the gate job does not run, no marker is written and the diff is barred;
   - a policy step that fails after it emitted `reserved` still renders both
     the `Review reserved` and the `Review retryable` steps from its outputs
     (make the status POST fail, for example by a temporary 403 in a
     disposable repository);
   - the `approve-and-merge` job runs in the `pr-autopilot` environment
     without waiting, also for a reused pass (when `ai-review` is skipped);
     the token step mints a token whose approval satisfies `dev`'s rules (the
     live check in owner setting 7), and that can dismiss an approval that
     names an older head (the scratch check in the same setting); a copy of
     the workflow dispatched on
     another branch cannot read the environment's key; with the key
     removed, the merge step still runs and the PR gets the approver note;
     and a sweep run, a PR-event run and a dispatch run that only waits show
     `approve-and-merge` as skipped, not failed;
   - a recovered merge: in the disposable repository, merge an
     `autopilot-approved` PR with a workflow token from a scratch workflow,
     without follow-ups. `GET .../pulls/<n>` must show `merged_by.login`
     `github-actions[bot]` (no such merge exists in the organisation to
     read), and a sweep at least 15 minutes later must post one merged
     record, dispatch once and add `autopilot-merged`;
   - whether `PUT .../pulls/<n>/update-branch` (the call behind
     `gh pr update-branch`) works while "Always suggest updating pull
     request branches" is off. Record the HTTP status and whether the
     branch moved. The behind-dev note does not mention the command until
     this is known. If it works, also record which account the branch
     activity feed names for that update: the push-history check counts it
     like any other push.
   No authenticated GitHub integration rehearsal, live merge or billable review
   was exercised during development; synthetic scope checks are not that proof.
3. On `dev` and the default branch, enable classification with an **empty trust
   list**. Check tier comments on docs, tests, sensitive paths, forks and drafts.
   This emits audit labels/comments but cannot run AI review or merge. After the
   workflow is on `main`, open or push to a test PR into `dev` and confirm that
   the PR event produces a run of this workflow whose conclusion is not
   `startup_failure`; otherwise the event policy is missing. The backfill is one
   run and one tier comment per open same-repository, non-draft PR without a
   tier label: about 280 on 11 Nov 2026. At 25 per sweep it takes about 12
   sweeps, about three hours. After that, sweeps dispatch only PRs pushed since
   their last tier audit until the policy file lists a trusted author.
4. Provision the review key, set a weekday window, and add a very small
   approved list to `trusted_authors` through a reviewed PR into `dev`. Do not
   use the variable for this: it cannot add a login. Exercise a benign PR, blocking review, stale head/base, hold, missing key,
   pending/failed checks, three-diff escalation, a dev catch-up with an unchanged
   diff, a branch behind `dev` (one behind-dev note naming the head, the
   sweep dispatching the PR about hourly, and the note marked "No longer
   applies" after the author merges `dev` in and pushes, and again after
   `autopilot-hold` is added), a second behind episode (after the author
   merges `dev` in, merge another PR into `dev` while CI runs: the bot posts
   one new comment naming the head, edits the note back to live, and the
   author receives a notification for the new comment; development did not
   observe that notification), an overtaken catch-up (while the note is
   live, the author merges `dev` in and pushes, and another PR merges into
   `dev` before the first run on that head: the bot posts one new comment
   naming the head and edits the note to name it, and the author receives
   a notification for the new comment; a plain push instead gets only the
   edit and no notification), a branch behind `dev` with a push by an
   account not on the list (an escalation and no behind-dev note), a push
   by an account not on the list, a dismissed App approval, a
   blocked diff fixed by a new commit (the escalation must then say it no
   longer applies), an escalated PR that the sweep then dispatches about
   hourly, and post-merge dispatches.
   Confirm the medium/high human path and every relevant dev push consumer.
5. Expand only after the audit trail, post-merge verification and revert/incident
   guardrails behave as expected. Policy widening is itself HIGH-risk.

Development validation is synthetic and uses a fake GitHub transport. It covers
classification (including compound credentials and nested non-test filenames),
size/deletion/rename boundaries, metadata completeness, author and fork restrictions,
missing switches/key, hours, non-admin protection queries, declared evidence scopes,
required-check provenance, the trusted-author list (the variable only narrows the
policy list, and a login only the variable names is denied as author and as
pusher), stale/rerun checks, structured verdicts, immutable completed
verdicts, the three review outcomes and their marker steps, attempt-1 evidence
after reruns, trusted-run rules (a `pull_request_target` run or a run on another
commit never vouches), attempt history built from comments, reservation
statuses and per-commit check runs across force pushes (including a renamed
check plus a deleted comment), forged or misplaced statuses, tampered or
unreadable records, persistent budgets, HTTP 404/410 versus transient API
failures and the HTTP code on `GitHub.request` errors, clearing a superseded
escalation, pusher trust and the `branch_creation` row, dismissed approvals,
the compare-only behind-dev rule, the behind-dev note (one per head, only for
a PR the merge decision would otherwise allow, not on an escalated head, and
only after the push history is read and shows the head; a history that needs
a person is escalated at once instead; cleared once the branch contains the
tip, once a person has the head, or once the PR is a draft, held, not LOW or
no longer from a trusted author, but not for hours or mergeability; edited
back to live when it applies again, after one new comment that notifies the
author, with a retry when that comment fails; the same new comment when a
live note moves to a head whose merge base with the tip differs, or whose
older head cannot be compared, and none for a plain push; and parked by the
sweep), the PR-event
types that clear it, the sweep's
dispatch filter and the workflow's runtime partial-rerun guard,
approval/merge races (including holds during audit/duplicate-review reads, and `dev`
moving just before the merge call), audit
ordering and dispatch selection. It also covers approvals only by the approver
App (a missing or unsafe identity, a refusal or another login approves
nothing and posts one note per head; the workflow gives the key only to the
merge step, in a job apart from the outcome markers), the stale-approval rule, retried and
persistently failing reservation writes and check reads and updates,
withdrawal of this run's approval when the head moved (and no withdrawal on
the same head, after a failed re-read or for an earlier approval), the
not-started marker, unreviewable diffs (including the byte limit measured as
the review step receives the diff), a PR blocked after an earlier approval,
a refused approval, the sweep's order, caps, rotation, pause, throttling stop,
re-tier of moved heads and hourly dispatch of PRs that wait on a person,
recovery of a merge whose answer was lost or unconfirmed (record before
dispatch, no repeat, only the bot's own merges, the CDN switch),
concurrent label creation, post-merge coverage
checked against every workflow file, and `python3 -I` in every
credential-holding job (with modules planted in a copy of the checkout). No cloud/cluster or production
configuration is needed. In CI the unprivileged `policy-tests` job tests proposed
changes to an autopilot file or to any workflow file; privileged jobs never
execute the proposed controller.

```bash
# In E2B or CI, not on a shared developer machine:
python3 scripts/test_pr_risk.py

# Local static lint only:
uvx --from actionlint-py==1.7.12.24 actionlint .github/workflows/low-risk-pr-autopilot.yml
uvx --from ruff==0.14.4 ruff check scripts/pr_risk.py scripts/test_pr_risk.py
```
