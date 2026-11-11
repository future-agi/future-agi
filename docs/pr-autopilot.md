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
| Repository variable `PR_AUTOPILOT_TRUSTED_AUTHORS` | Unset: use the policy's empty list | Set an explicit comma-separated list of GitHub logins, or review a policy change to populate `trusted_authors`; no wildcards |
| Repository variable `AUTOPILOT_HOURS_UTC` | Unset: any day/time, but only when the feature is on | Optionally set `04-15` or another zero-padded `HH-HH` window; Monday–Friday, UTC, inclusive start/exclusive end; `00-24` means all weekday hours |
| Repository secret `ANTHROPIC_API_KEY` | Absent: skip the AI job and never auto-merge, even if an older review passed | Provision an API key for the approved Anthropic account, with budget/usage monitoring; do not put it in the policy or PR |
| Existing repository variable `DEV_CDN_AUTO_DEPLOY` | Unset: preserve the existing enabled-on-push behavior | Exact `false` prevents the post-merge dev CDN dispatch; all other values preserve its existing behavior |
| PR label `autopilot-hold` | Absent | Add to stop automation for one PR; remove only when the PR may be reconsidered |
| Labels `autopilot-low`, `autopilot-medium`, `autopilot-high` | Created by the first enabled sweep or run | No setup needed. The sweep creates missing labels once before it dispatches classification runs, and a run that finds a label just created by another run continues. The controller replaces only its own current tier label |
| Labels `autopilot-approved`, `autopilot-merged` | Absent | Created/applied by the controller for audit; retain them after merge and revert |
| Built-in `GITHUB_TOKEN` and Actions permissions | Provided by GitHub; explicit least-privilege job permissions | Allow the workflow's stated read/write scopes, including `statuses: write` in the prepare job (it posts the reservation status) and `statuses: read` in the `approve-and-merge` job. No Administration permission or protection-bypass role is needed or granted. The workflow token never approves, so turn **off** “Allow GitHub Actions to create and approve pull requests” (see [Approver App and owner settings](#approver-app-and-owner-settings)). An organisation-level “off” overrides the repository setting |
| Approver App, environment `pr-autopilot`, variable `AUTOPILOT_APPROVER_CLIENT_ID`, secret `AUTOPILOT_APPROVER_PRIVATE_KEY` | Not created | The owner creates them as described in [Approver App and owner settings](#approver-app-and-owner-settings). Without them the token step in the `approve-and-merge` job fails. The review outcome is already recorded by then. Nothing is approved or merged, and the bot posts one note per head on the PR |
| Actions event policy for `pull_request_target` | None today: in GitHub's 9 Nov 2026 brownout, all 57 `pull_request_target` runs in this repository ended in `startup_failure` | From 2 Dec 2026 GitHub blocks `pull_request_target` in public repositories unless an Actions event policy, at repository or organisation level, allows it. The policy must allow it for `.github/workflows/low-risk-pr-autopilot.yml`. If the policy for `admission.yml` and `labeler.yml` names workflow files, it must name this file too. It must be in place before this workflow reaches `main` and before 2 Dec 2026; the 16 Nov 16:00–18:00 UTC brownout applies as well. Without it, every PR event into `dev` creates a run that fails at startup, even with `PR_AUTOPILOT` off, and only the sweep classifies and dispatches PRs (see below) |
| `dev` protection / active org and repository branch rules | Read, never changed by this code | Enable an active branch ruleset with at least one required status check and strict up-to-date enforcement; preserve existing classic requirements. Classic-only strictness cannot be verified with `GITHUB_TOKEN`, so it is not sufficient. An active `pull_request` rule must also dismiss stale approvals on push; the controller refuses without it. dev's organisation rule has it on today (read-only check, 11 Nov). Also require approval of the most recent push; the controller does not check this one |
| Required status checks | Policy minimum: `Backend Tests Pass`, union all active classic/ruleset requirements | Keep required checks meaningful and green on the PR head; missing, skipped, neutral, pending, stale or failing checks cannot satisfy the bot |
| Merge commits | The policy's only merge method | Keep merge commits enabled; this matches nine of the ten recent first-parent `dev` commits inspected at `a75af77f6` |
| Workflow on the default branch | No PR-event runs and no sweep until this workflow is on `main` | PR events (`pull_request_target`) and the schedule both run `main`'s copy of this workflow. Land it on `dev` through human review and include it in the normal `dev` → `main` release; do not change the default branch. `main`'s copy checks out `dev`'s controller and policy, so a controller change on `dev` applies to those runs at once. A workflow change on `dev` (step names, environment variables, triggers) reaches them only with the next `dev` → `main` release. The classify, label and sweep paths therefore must keep working with the previous release's workflow copy; only dispatch runs on `dev` use `dev`'s copy |

**Trusted authors at installation: none.** Repository membership, write access,
CODEOWNERS, PR text and earlier bot approvals do not confer trust. A nonempty
variable **replaces**, rather than extends, the config list. Login comparison is
case-insensitive; malformed entries stop automation. Start with a small,
explicitly approved list. A fork, even from a trusted author, is never eligible.

Trust covers **who pushed**, not only who opened the PR. Before it reserves a
review, and again before approval and merge, the controller reads the branch's
push history (`GET /repos/{owner}/{repo}/activity?ref=refs/heads/<branch>`,
one page of up to 100 rows). Every row must name a trusted author. A push by
anyone else, including an "Update branch" click by someone not on the list,
sends the PR to a person for good; the author has to update the branch
themselves. An empty history, a full page that may hide older pushes, or a
page whose oldest row is not the branch's `branch_creation` row (so earlier
pushes are missing) also goes to a person. On 2026-11-11 the oldest row of
all 19 sampled PR branches in this repository was `branch_creation`. If the
newest push in the feed is not yet the PR head, the run waits. If the
endpoint does not answer, the run stops and approves nothing.

An invalid or overnight working-hours window denies automation. Use one
same-day UTC window; there is no local-time/DST conversion or implicit fallback.

A sweep runs about every 15 minutes, subject to GitHub's scheduler delay. It
skips drafts, forks and PRs whose head repository no longer exists, and
dispatches a run on `dev` for these PRs, in this order:

1. Every trusted author's `autopilot-low` PR without `autopilot-hold`. These
   are not capped, so the backfill never delays a merge. A PR that waits on a
   person or the owner at its current head is dispatched only on one sweep
   in four, about once an hour. It waits when the bot's standing escalation,
   merge-blocked note or approver note names its current head, or when a
   maintainer dismissed the autopilot's approval of that head. A push ends
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
sweep stops dispatching until the next sweep. The sweep fails and names the PRs
it could not dispatch or whose tier comment it could not read.

Parked PRs are still dispatched hourly, not skipped. An escalation can stop
applying without a new head (for example a run that read as deleted reads
again), a person can supply what a branch rule needs, and the owner can fix
the approver App. Skipping such PRs would strand them. The notes only slow
the sweep: a writer who edits or deletes one changes when the PR is
dispatched, never what a run checks. A PR that waits with no note (pending
CI, a branch behind `dev`, a review to retry after the branch changes) is
still dispatched on every sweep, because nothing tells the sweep when that
wait ends. A dispatch on `dev` with input `pr_number` re-evaluates a PR at
once; it cannot reset the review budget.

The existing E2E suite remains **advisory**, given the observed failure rate.
It is not added to the minimum required checks. If an administrator explicitly
makes it required, this controller respects that requirement and does not
silently ignore it. The label instructions describe the medium/high human
policy; this workflow does not replace repository rules that enforce the
number of human approvals.

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
  The token is used for one request: the approval. Comments, labels, the
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
pusher. So steps 2 to 5 come before the key is added in step 6.

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
   ruleset. They have the same exposure and need the same change.
3. **Require approval of the most recent push** on `main` and `dev`, in a
   repository ruleset rather than by editing organisation ruleset 2289447,
   which covers other repositories. Otherwise a writer can push onto someone
   else's PR and approve it. The autopilot never pushes, so the approver is
   never the last pusher.
4. **Create the environments, with no key yet.** GitHub creates a missing
   environment with no rules the first time a job names it, so create each one
   with its branch rule first. Neither may have required reviewers or a wait
   timer: they would hold the approving job and, through the PR's concurrency
   group, every later run for that PR.
   - `pr-autopilot`: deployment branch rule `dev`. The `approve-and-merge`
     job of `low-risk-pr-autopilot.yml` names it.
   - `release-autopilot`: deployment branch rule `main`. The release job of
     `release-autopilot.yml` names it.
5. **Move the release-bot App key** (`RELEASE_BOT_APP_ID`,
   `RELEASE_BOT_PRIVATE_KEY`, organisation secrets today) into environment
   secrets whose deployment branch policy allows only protected branches and
   release tags. Any writer's branch workflow can read an organisation secret,
   and that App can approve PRs too, so until this step a writer can still
   get a PR approved without another person. Every job that reads the key
   must then name such an environment. A job names only one environment, so
   the key can need a copy in more than one. `docs/bot-pr-autopilot.md` in
   `future-agi/deployment` and `docs/release-autopilot.md` here list the
   jobs. Once every such job names an environment, delete the organisation
   secrets, generate a new private key for the release-bot App and delete
   the old one, because any branch workflow could read the old key. Step 6
   waits for this.
6. **Confirm, then add the approver key.** On a throwaway PR into `dev`, let
   a workflow on the PR's branch try to approve that PR with its
   `GITHUB_TOKEN`. GitHub must refuse it, and the PR must show no approval.
   Only then add `AUTOPILOT_APPROVER_CLIENT_ID` (variable) and
   `AUTOPILOT_APPROVER_PRIVATE_KEY` (secret) to both environments. If the
   approver key was stored anywhere a branch workflow could read before
   steps 2 to 5 were done, generate a new private key for the App, delete
   the old one and store only the new one.
7. **Check live, before `PR_AUTOPILOT` is set to `on`,** that the App's
   approval counts. On a throwaway PR into `dev`, approve with a token minted
   exactly as the workflow does (pull-requests: write only). Then
   `gh pr view <n> --json reviewDecision,mergeStateStatus` must show `APPROVED`,
   and a merge state that no other rule holds. Use the API fields, not the
   merge button: organisation admins bypass the ruleset. Then dismiss the
   approval with the same token. If `reviewDecision` stays `REVIEW_REQUIRED`,
   grant the installation "Contents: Read and write" if it lacks it, and read
   the same PR again. If the approval still does not count, the mint step in
   `low-risk-pr-autopilot.yml` must also request `permission-contents: write`
   (a code change, reviewed like any other). Until this check passes, every
   approved PR stops at `blocked` and the autopilot tells the PR so (see item
   7 of [Trust boundary and merge decision](#trust-boundary-and-merge-decision)).

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
   persisted. A
   dispatch run stops and waits for a fresh run if `dev` moved after it was
   dispatched (the checkout is not `GITHUB_SHA`, the run's `head_sha`) or after
   checkout (the checkout is not the live `dev` tip). The PR must already contain that tip:
   the controller always asks GitHub's compare API, because a recorded base
   SHA equal to the tip only means the PR was opened after that commit, not
   that its branch contains it. A PR that is behind `dev` waits quietly and
   spends no review round. Manual dispatches on other branches are rejected.
   PR-event runs and the schedule both need this workflow on the default
   branch.
2. The controller classifies and audits the tier. It requires an open,
   non-draft, non-fork PR from an explicitly trusted author, a LOW classification,
   a present review key, no hold label, an allowed time and known mergeability.
   Every push to the PR's branch must also come from a trusted author (see
   above), and an autopilot approval of this head that a maintainer dismissed
   counts as a hold.
3. It reads classic check contexts through GraphQL `Ref.refUpdateRule`, the
   non-admin protection view, and applicable active rules through
   `GET /repos/{owner}/{repo}/rules/branches/dev`. Required checks are the union
   of those and `Backend Tests Pass`, never a replacement list. The non-admin
   view has **no classic strictness field**; the administrative
   `branchProtectionRule` view is deliberately not used. An active ruleset with
   strict required checks must supply positive up-to-date-enforcement evidence.
   From the same rules call, an active `pull_request` rule must have
   `dismiss_stale_reviews_on_push`. The bot's approval names one head, but
   GitHub counts it for the PR, so only that setting stops a later push from
   being merged on it. Classic-only protection or inaccessible evidence stops
   automation. Check
   runs and legacy commit statuses are read with their separate permissions
   (`checks` and `statuses`). The newest result for each
   check must be `success` on the exact head SHA. The expected integration ID is
   honored when supplied by a ruleset; otherwise a GitHub Actions check-run
   identity is required. Legacy status contexts with no verifiable App identity,
   unavailable API evidence, merge queues and required-workflow rules wait for
   a separate integration/human review instead of weakening verification.
4. The AI job runs on a **different runner with a read-only token**. Its explicit
   prompt selects the action's prompt-only agent mode. It receives JSON-encoded
   patches as data, not PR title/body/comments or executable files. Built-in
   tools, MCP tools and skills are disabled. No PR checkout, dependency install,
   shell interpolation of the diff, write token or patch execution is allowed.
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
     the step instead of timing out the job.
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

The review check is **mandatory for the autopilot**, independently of the
managed Claude review UI/check, which never fails. It is not installed as a
blanket required branch check by this code: it is only produced for eligible
LOW PRs with a review key and passing CI. Making it required for *every* PR
would block the intentionally manual medium/high and disabled-feature paths.
An administrator must design a human-reviewed alternative before imposing it
repository-wide. Normal protection requirements still apply to every merge.

GitHub's merge API atomically enforces the expected head and its branch rules.
It cannot atomically compare a label or working-hours variable. Mutable PR
metadata is read last before each write, strict protection covers base/check
races, and a hold/cancellation should be used for an immediate operational stop.
Variables are an Actions run snapshot, not a live external kill-switch service.

## AI review contract and bounded retries

The action is pinned to the commit of `anthropics/claude-code-action` v1.0.120
(`dde2242db6af13460b916652159b6ba19a598f30`), because this job holds the API key
and a tag can be moved. Its `anthropic_api_key`, `github_token`, `prompt`,
`claude_args` and `structured_output` inputs were checked against the published
documentation for that version.
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
decision are recorded, without logging raw diffs.

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
  is not retried, so an outage cannot spend attempts every 15 minutes; the
  run waits quietly, and the bot's result comment tells the author to update
  the branch from `dev` or push a commit. This is the first of the two options
  the round-two review offered.
- Merging dev into the branch without changing the author's own diff gets a
  fresh review on the new base at no cost to the diff budget. This matters
  because `dev` requires branches to be up to date and moves often.
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
- [Pinned action inputs/outputs](https://github.com/anthropics/claude-code-action/blob/v1.0.120/action.yml)
- [Pinned usage and structured output](https://github.com/anthropics/claude-code-action/blob/v1.0.120/docs/usage.md)
- [Pinned agent-mode behavior](https://github.com/anthropics/claude-code-action/blob/v1.0.120/src/modes/agent/index.ts)
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

- `backend-ci.yml`, advisory `e2e-ci.yml` with `stack: distributed`, and
  `sdk-compatibility.yml` (SDK Compatibility), after every merge;
- `standalone-ci.yml` (Standalone install CI) and `update-ci.yml` (Update CI)
  when the merge changes a file their push filters name, for example any
  backend Python test;
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
LOW path, so a person's merge of a LOW file would not start them either. Every
`dev` push workflow accepts `workflow_dispatch`. A test reads every workflow
file and fails if a `dev` push workflow that a LOW merge would start is not
dispatched under the same paths, or if a dispatch's inputs do not fit the
workflow. It counts every push trigger form that can run on `dev`, and reports
`branches-ignore`, `paths-ignore` and negated branch patterns as not modelled.
Widening the LOW allowlist can therefore require new entries.

Those workflows execute their own trusted post-merge code and use their own
permissions/secrets. This lane adds no production credentials and does not
change release/main automation. Dispatches test/deploy the current `dev` ref,
which can include later merges; they are not proof that a particular merge has
already passed post-merge verification.

The controller reads the changed files it needs for this choice **before** the
merge call. Each dispatch is tried three times. The merge label, the merge
comment and every dispatch are attempted independently, so one failure does not
skip the others; the job then fails and names what was missed.

Approval comments, review records, tier labels, review check details, merge
comments and job summaries identify the policy/base SHA, head SHA, checks and
workflow run. `autopilot-merged` is applied only after the API confirms a merge.
If an audit or dispatch request fails after merge, the job fails but does not
undo the merge or claim the downstream workflow ran. Inspect the run, reconcile
its merge label/comment, and dispatch any missed checks through the normal
operating procedure. An `autopilot-approved` label with a merged PR is a useful
reconciliation queue, not proof that the bot performed the final merge.

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
`on`. Add `autopilot-hold` to affected open PRs. A missing review key or empty
trust list also prevents new automatic approvals/merges, but explicit off plus
cancellation is the clearest emergency procedure. Removing the approver App's
key from the `pr-autopilot` environment also stops every approval; each PR
that passes review then gets one note per head saying so. Do not
remove normal required checks to make a stopped PR mergeable. An App approval
already recorded remains visible; a maintainer can dismiss it if it must not
count toward a later manual merge. Dismissing the App's approval also stops
automation for that head, like `autopilot-hold`: the bot does not approve it
again, and only a new revision (or a person merging by hand) moves the PR on.
A merged change must be reverted through the usual reviewed process.

Suggested rollout:

1. Human-review these HIGH-risk CI/policy changes. Confirm branch protection,
   an active strict-check ruleset, a `pull_request` rule that dismisses stale
   approvals on push, merge-commit support and current-head check SHAs. Before
   this workflow reaches `main`, and before 2 Dec 2026, the owner adds the
   Actions event policy that allows `pull_request_target` for
   `.github/workflows/low-risk-pr-autopilot.yml`, and completes the owner
   settings in [Approver App and owner settings](#approver-app-and-owner-settings).
2. Keep `PR_AUTOPILOT` unset during CI validation. Run the synthetic tests and
   actionlint; verify the actual action/API contract in a disposable repository
   before any eligible real PR can merge. Rehearse with **exactly** each job's
   declared `GITHUB_TOKEN` permissions, not a personal/admin token: the GraphQL
   `refUpdateRule` query, active branch rules, check runs and commit statuses must
   all succeed. Confirm missing-scope/error cases deny automation, classic-only
   strictness is rejected, and a partial AI-job rerun makes no new model call.
   Also confirm these GitHub behaviors, which development could not observe:
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
     live check in owner setting 7); a copy of the workflow dispatched on
     another branch cannot read the environment's key; with the key
     removed, the merge step still runs and the PR gets the approver note;
     and a sweep run, a PR-event run and a dispatch run that only waits show
     `approve-and-merge` as skipped, not failed.
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
   their last tier audit until the trust list is set.
4. Provision the review key, set a weekday window and a very small approved trust
   list. Exercise a benign PR, blocking review, stale head/base, hold, missing key,
   pending/failed checks, three-diff escalation, a dev catch-up with an unchanged
   diff, a push by an account not on the list, a dismissed App approval, a
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
required-check provenance, stale/rerun checks, structured verdicts, immutable completed
verdicts, the three review outcomes and their marker steps, attempt-1 evidence
after reruns, trusted-run rules (a `pull_request_target` run or a run on another
commit never vouches), attempt history built from comments, reservation
statuses and per-commit check runs across force pushes (including a renamed
check plus a deleted comment), forged or misplaced statuses, tampered or
unreadable records, persistent budgets, HTTP 404/410 versus transient API
failures and the HTTP code on `GitHub.request` errors, clearing a superseded
escalation, pusher trust and the `branch_creation` row, dismissed approvals,
the compare-only behind-dev rule, the sweep's
dispatch filter and the workflow's runtime partial-rerun guard,
approval/merge races (including holds during audit/duplicate-review reads), audit
ordering and dispatch selection. It also covers approvals only by the approver
App (a missing or unsafe identity, a refusal or another login approves
nothing and posts one note per head; the workflow gives the key only to the
merge step, in a job apart from the outcome markers), the stale-approval rule, retried and
persistently failing reservation writes and check reads and updates, the
not-started marker, unreviewable diffs, a PR blocked after an earlier approval,
a refused approval, the sweep's order, caps, rotation, pause, throttling stop,
re-tier of moved heads and hourly dispatch of PRs that wait on a person,
concurrent label creation, and post-merge coverage
checked against every workflow file. No cloud/cluster or production
configuration is needed. In CI the unprivileged `policy-tests` job tests proposed
changes; privileged jobs never execute the proposed controller.

```bash
# In E2B or CI, not on a shared developer machine:
python3 scripts/test_pr_risk.py

# Local static lint only:
uvx --from actionlint-py==1.7.12.24 actionlint .github/workflows/low-risk-pr-autopilot.yml
uvx --from ruff==0.14.4 ruff check scripts/pr_risk.py scripts/test_pr_risk.py
```
