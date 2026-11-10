# Low-risk PR autopilot

The autopilot can approve and merge a narrowly allowlisted PR into `dev` after
CI and a separate, failing-on-findings AI review gate pass. It ships **off**.
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
path tokens on separators and camelCase boundaries. The LOW allowlist is
case-sensitive. An unrecognized path is never LOW. No file deletion or rename
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

No repository settings are changed by the workflow installation.

| Variable, secret, label or setting | Default | Administrative action |
| --- | --- | --- |
| Repository variable `PR_AUTOPILOT` | Unset/off; only exact `on` enables automation | Set to `on` only after the rollout checks below; unset or use `off` to stop new runs |
| Repository variable `PR_AUTOPILOT_TRUSTED_AUTHORS` | Unset: use the policy's empty list | Set an explicit comma-separated list of GitHub logins, or review a policy change to populate `trusted_authors`; no wildcards |
| Repository variable `AUTOPILOT_HOURS_UTC` | Unset: any day/time, but only when the feature is on | Optionally set `04-15` or another zero-padded `HH-HH` window; Monday–Friday, UTC, inclusive start/exclusive end; `00-24` means all weekday hours |
| Repository secret `ANTHROPIC_API_KEY` | Absent: skip the AI job and never auto-merge, even if an older review passed | Provision an API key for the approved Anthropic account, with budget/usage monitoring; do not put it in the policy or PR |
| Existing repository variable `DEV_CDN_AUTO_DEPLOY` | Unset: preserve the existing enabled-on-push behavior | Exact `false` prevents the post-merge dev CDN dispatch; all other values preserve its existing behavior |
| PR label `autopilot-hold` | Absent | Add to stop automation for one PR; remove only when the PR may be reconsidered |
| Labels `autopilot-low`, `autopilot-medium`, `autopilot-high` | Created by the first enabled run | No setup needed; the controller replaces only its own current tier label |
| Labels `autopilot-approved`, `autopilot-merged` | Absent | Created/applied by the controller for audit; retain them after merge and revert |
| Built-in `GITHUB_TOKEN` and Actions permissions | Provided by GitHub; explicit least-privilege job permissions | Allow the workflow's stated read/write scopes and “Allow GitHub Actions to create and approve pull requests”; do not grant a protection-bypass role |
| `dev` protection / active org and repository branch rules | Read, never changed by this code | Enable strict required checks (“Require branches to be up to date”) and protect the branch; the controller refuses an unprotected/unknown strict-check configuration |
| Required status checks | Policy minimum: `Backend Tests Pass`, union all active classic/ruleset requirements | Keep required checks meaningful and green on the PR head; missing, skipped, neutral, pending, stale or failing checks cannot satisfy the bot |
| Merge commits | The policy's only merge method | Keep merge commits enabled; this matches nine of the ten recent first-parent `dev` commits inspected at `a75af77f6` |
| Actions/default-branch installation | No scheduled polling until GitHub can discover this workflow on the repository default branch | Land on `dev` through human review, and include it in the normal `dev` → `main` release; do not change the default branch |

**Trusted authors at installation: none.** Repository membership, write access,
CODEOWNERS, PR text and earlier bot approvals do not confer trust. A nonempty
variable **replaces**, rather than extends, the config list. Login comparison is
case-insensitive; malformed entries stop automation. Start with a small,
explicitly approved list. A fork, even from a trusted author, is never eligible.

An invalid or overnight working-hours window denies automation. Use one
same-day UTC window; there is no local-time/DST conversion or implicit fallback.
A sweep reconsiders waiting PRs about every 15 minutes, subject to GitHub's
scheduler delay. A dispatch on `dev` with input `pr_number` can also re-evaluate
a PR; it cannot reset the review budget.

The existing E2E suite remains **advisory**, given the observed failure rate.
It is not added to the minimum required checks. If an administrator explicitly
makes it required, this controller respects that requirement and does not
silently ignore it. The label instructions describe the medium/high human
policy; this workflow does not replace repository rules that enforce the
number of human approvals.

## Trust boundary and merge decision

1. `pull_request_target` handles PRs targeting `dev`. Privileged jobs sparsely
   check out only `dev`'s controller and policy, with checkout credentials not
   persisted. The checkout SHA must match the live PR base. Schedule runs
   dispatch separate evaluations on `dev`; manual dispatches on other branches
   are rejected. The scheduled entry needs to exist on the default branch.
2. The controller classifies and audits the tier. It requires an open,
   non-draft, non-fork PR from an explicitly trusted author, a LOW classification,
   a present review key, no hold label, an allowed time and known mergeability.
3. It reads both classic branch protection and active branch rules. Required
   checks are the union of those and `Backend Tests Pass`, never a replacement
   list. Strict up-to-date checks must be enabled. The newest result for each
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
   separate **`PR Autopilot review`** Check Run on the **PR head SHA**, not the
   `pull_request_target` job's base SHA. Blocking findings, incomplete output,
   malformed JSON, action failure or a changed head/base produce a failed check.
6. Before approval and again before merge, the controller re-reads the PR,
   files, rules, current-head CI, hold/draft/author state, time, and review result.
   Outstanding human change requests block it. A cached AI pass must match both
   head and base and come from this trusted workflow's run. It labels and comments
   each automated approval, then approves with `GITHUB_TOKEN`. Existing matching
   bot approvals are not duplicated.
7. It waits for GitHub's mergeability/protection state to be `clean`, then calls
   the merge API with the expected head SHA and `merge_method: merge`. GitHub
   remains the final authority for branch protection; no bypass token or
   `--admin` equivalent is used. The branch is not deleted.

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

The action is pinned to `anthropics/claude-code-action@v1.0.120`, with the
`anthropic_api_key`, `github_token`, `prompt`, `claude_args` and
`structured_output` interfaces confirmed against its published documentation.
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
diff. Review findings and the decision are recorded, without logging raw diffs.

A bot-authored PR comment reserves each round **before** the AI call. The
reservation survives force pushes and workflow reruns. At most **three rounds
per PR**, including failed and cancelled attempts, are allowed. A previously
attempted head/base pair is not automatically re-reviewed. A changed head or
base may consume the next round; after three, the controller posts a human
escalation. The bot does not modify code or dismiss findings. The author fixes
blocking findings with a reason and submits a new revision; a person takes over
if the budget or automated path cannot complete. Do not delete/edit reservation
comments to bypass the cap. A passing exact-pair review can be reused while the
other merge gates are rechecked.

References checked while implementing the adapter:

- [Action README](https://github.com/anthropics/claude-code-action/blob/main/README.md)
- [Pinned action inputs/outputs](https://github.com/anthropics/claude-code-action/blob/v1.0.120/action.yml)
- [Pinned usage and structured output](https://github.com/anthropics/claude-code-action/blob/v1.0.120/docs/usage.md)
- [Pinned agent-mode behavior](https://github.com/anthropics/claude-code-action/blob/v1.0.120/src/modes/agent/index.ts)
- [CLI tool restrictions](https://code.claude.com/docs/en/cli-reference)

## After merge and audit recovery

A merge by `GITHUB_TOKEN` does **not** trigger ordinary push workflows. The
controller explicitly dispatches existing workflows on `dev` after a confirmed
merge:

- `backend-ci.yml` and advisory `e2e-ci.yml` with `stack: distributed`;
- for frontend paths only, `frontend-develop.yml` and `frontend-dev-cdn.yaml`;
- the CDN dispatch respects `DEV_CDN_AUTO_DEPLOY=false`, and always passes
  `branch: dev`, `repair_spa_routes: false`. This is necessary because that
  workflow's manual entry otherwise bypasses its existing push switch.

Those workflows execute their own trusted post-merge code and use their own
permissions/secrets. This lane adds no production credentials and does not
change release/main automation. Dispatches test/deploy the current `dev` ref,
which can include later merges; they are not proof that a particular merge has
already passed post-merge verification. The team must inventory other push-only
consumers before enabling the autopilot.

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
cancellation is the clearest emergency procedure. Do not remove normal required
checks to make a stopped PR mergeable. A bot approval already recorded remains
visible; a maintainer can dismiss it if it must not count toward a later manual
merge. A merged change must be reverted through the usual reviewed process.

Suggested rollout:

1. Human-review these HIGH-risk CI/policy changes. Confirm branch protection,
   the GitHub token's read access to protection/rules evidence, App identities,
   strict required checks, merge-commit support and current-head check SHAs.
2. Keep `PR_AUTOPILOT` unset during CI validation. Run the synthetic tests and
   actionlint; verify the actual action/API contract in a disposable repository
   before any eligible real PR can merge. No live GitHub merge or billable review
   was exercised during development.
3. On `dev` and the default branch, enable classification with an **empty trust
   list**. Check tier comments on docs, tests, sensitive paths, forks and drafts.
   This emits audit labels/comments but cannot run AI review or merge.
4. Provision the review key, set a weekday window and a very small approved trust
   list. Exercise a benign PR, blocking review, stale head/base, hold, missing key,
   pending/failed checks, three-round escalation, and post-merge dispatches.
   Confirm the medium/high human path and every relevant dev push consumer.
5. Expand only after the audit trail, post-merge verification and revert/incident
   guardrails behave as expected. Policy widening is itself HIGH-risk.

Development validation is synthetic and uses a fake GitHub transport. It covers
classification, size/deletion/rename boundaries, metadata completeness, author
and fork restrictions, missing switches/key, hours, required-check provenance,
stale/rerun checks, structured verdicts, persistent review budgets, approval/merge
races, audit ordering and dispatch selection. No cloud/cluster or production
configuration is needed. In CI the unprivileged `policy-tests` job tests proposed
changes; privileged jobs never execute the proposed controller.

```bash
# In E2B or CI, not on a shared developer machine:
python3 scripts/test_pr_risk.py

# Local static lint only:
uvx --from actionlint-py==1.7.12.24 actionlint .github/workflows/low-risk-pr-autopilot.yml
uvx --from ruff==0.14.4 ruff check scripts/pr_risk.py scripts/test_pr_risk.py
```
