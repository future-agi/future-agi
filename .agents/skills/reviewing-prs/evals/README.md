# Confirmed-defect review evals

[confirmed-defects.json](confirmed-defects.json) is a 16-case, positive-only seed set for
[reviewing-prs](../SKILL.md). Each case pairs a merged introducing PR with a later fixing PR in
`future-agi/future-agi`. It measures whether a reviewer catches a known defect on the introducing
PR, not whether the reviewer can explain the fix afterward. No model or API runner is included.

## Case format

- `id`: stable case name; grade cases separately even when they share a PR.
- `introducing_pr`, `fixing_pr`: GitHub PR numbers; the fixing PR is grader-only evidence.
- `category`: the validation needed to expose the defect, not its severity.
- `changed_files`: specific defect-bearing paths in the introducing PR, not today's paths or the
  fixing PR's entire file list.
- `expected_finding`: one-sentence answer key, matched by meaning rather than exact wording.
- `checklist_section`: heading in [standards-checklist.md](../references/standards-checklist.md),
  or `null` when no section covers the case; a testing section is a coverage lens, not a substitute
  for diagnosing the defect.

| Category | Meaning | Cases |
| --- | --- | ---: |
| U | One test with a realistic input would catch it | 8 |
| V | Needs an end-to-end run | 4 |
| P | Needs production-shaped data or load | 3 |
| C | Contract mismatch | 1 |

## Run one case

1. The grader selects a case. Give the reviewer only its introducing PR and historical revisions.
   Withhold this folder, the fixing PR, category and expected finding until the review is frozen.
   Keep the candidate skill and its references outside the historical checkout: old PRs may predate
   the skill. Record the candidate skill's commit SHA and the review model/settings.
2. Check out the introducing PR's **merge first parent** in a disposable worktree. All introducing
   PRs in this seed set have two-parent merge commits. From the current repository, for example:

   ```sh
   PR=2716
   MERGE=$(gh pr view "$PR" -R future-agi/future-agi --json mergeCommit --jq '.mergeCommit.oid')
   git fetch origin "$MERGE"
   BASE=$(git rev-parse "${MERGE}^1")
   HEAD=$(git rev-parse "${MERGE}^2")
   git worktree add --detach "../review-eval-$PR" "$BASE"
   ```

3. Run the candidate `reviewing-prs` skill against that introducing PR. Supply `BASE` and `HEAD`
   explicitly and follow its already-merged-PR procedure. The reviewer may check out `HEAD` in the
   disposable worktree to inspect changed code; compare `BASE...HEAD`, not today's base branch,
   which already contains the fix. Treat behind/ahead counts as of the merge. Do not provide later
   fixes, discussion of those fixes, or current application code as review context. Keep the review
   read-only on GitHub; do not post comments on the historical PR.
4. Freeze the findings, then let the grader open the case and fixing PR. Pass if the review contains
   the expected finding, with the concrete cause and consequence attributed to the introducing PR
   and grounded in a listed file. Merely repeating a checklist rule or requesting more tests fails.
   For multiple failure mechanisms in one case, all must be identified; multiple findings may
   jointly satisfy the case. Repeated findings earn no extra credit.

Run any tests or application/browser work only in a temporary labelled E2B sandbox or staging VM,
not on a shared local machine. Use synthetic inputs, not production records or credentials. The
credential-policy case's finding remains exactly the abstract statement in the answer key; do not
copy credential values or expand its details into prompts, fixtures, logs or review output.

## Scoring and provenance

Each case earns **1** for a pass or **0** for a miss. Full-set recall is `passed / 16`; also report
`passed / total` separately for U, V, P and C. List unrun cases separately and give them no credit in
full-set recall. A complete pass is 16/16. Additional findings neither earn nor remove case credit;
record unsupported findings separately. This positive-only set does not measure precision or
prove that a reviewer is safe to approve unrelated PRs.

PR numbers, merged status and changed paths were checked with `gh pr view` and
`gh pr diff --name-only`. GitHub rejected the oversized diffs for #401, #2381, #2716 and #3065;
the paginated `gh api repos/future-agi/future-agi/pulls/<number>/files` endpoint supplied their
file lists and selected non-sensitive patches instead. Merge parents were checked through the
commits API. The credential-policy case uses PR metadata and file lists only; its description is
intentionally not expanded. These checks verify provenance, not a replay or a reviewer score.
