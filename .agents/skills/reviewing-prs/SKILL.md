---
name: reviewing-prs
description: "Use when asked to review a pull request, branch, or diff — before approving, as a self-review before opening a PR, to judge whether a PR is ready — 'is PR 123 mergeable?', 'anything blocking here?' — or to answer 'does this change need an E2E flow'. Applies the FutureAGI coding standards and, in repos with an e2e/ harness, the E2E coverage gate. Not for reviewing a design doc or a single file in isolation, and not for carrying out git or GitHub operations on a PR such as merging, rebasing or landing it."
metadata:
  short-description: Review a PR against the coding standards with the E2E coverage gate
---

# Reviewing PRs

## Overview

The review is read-only until the human says post. Nothing is edited, staged, committed, pushed, or
sent to GitHub from inside this procedure.

The job is to move the catch from production into the diff. A finding is worth writing when it is
concrete, introduced by this change, demonstrable from the code, and something the author would fix
if they knew. Everything else is noise that costs the author's attention and your credibility.

Track the seven procedure steps as todos and work them in order. Steps 3 and 4 are conditional on
observable predicates; when a predicate is false, say so in the review rather than skipping
silently.

## Inputs

The user supplies a PR number, a branch, or nothing (= the current branch against its base). If the
base cannot be resolved by step 1, stop and ask which base to diff against. Do not guess.

## Procedure

### 1. Get the real diff

Resolve the base in this order, first success wins:

1. `gh pr view <n> --json baseRefName,body,title,headRefName,url` when a PR number or URL is known.
2. The branch's upstream merge-base (`git rev-parse --abbrev-ref @{u}` → `git merge-base HEAD <upstream>`).
3. Ask the user.

**Never assume `dev` or `main`.** Stacked PRs target a feature branch, and a base guessed wrong turns
someone else's merged work into your findings.

Then, from a checkout of the head being reviewed:

```
git fetch origin <base> <head>
git diff origin/<base>...HEAD            # the real diff (three dots)
git diff --stat -w                       # mechanical churn vs real change
git rev-list --left-right --count origin/<base>...HEAD
```

Record behind/ahead. A branch hundreds of commits behind its base is itself a finding, because every
other conclusion in the review is drawn against stale code.

Reviewing a PR you do not have checked out: fetch its head (`git fetch origin pull/<n>/head`) and
diff that SHA, or check it out in a separate worktree. Do not switch the branch of the checkout you
were handed.

Reviewing a PR that has **already merged**: its base branch now contains its head, so
`origin/<base>...HEAD` is empty and the behind/ahead counts describe history since the merge. Diff
the branch point against the head instead (`git merge-base <base-at-merge> <head>`), and say in the
review that the counts are as-of-merge.

### 2. Read the body and the ticket; list every claimed behaviour change

Write the list down before looking for bugs. Each entry is either confirmed against the diff in the
later steps or reported as unsupported. Bodies lie by omission more often than by invention: a claim
of tests, of a verification run, of "no migration", or of "moved verbatim" is a claim you check.

A claimed verification with nothing in the diff to re-run it (a manual test plan, a screenshot, a
browser session) is not evidence. Say so under **Should fix** with the concrete thing that would make
it re-runnable.

### 3. E2E coverage gate — only if `e2e/scripts/e2e-coverage.mjs` exists

If the file is absent, the review still carries the block — same two-line shape as every other
review, so the section is never missing:

```
## E2E coverage
not applicable (no e2e/ harness) · flows hit: n/a · marker: n/a · verdict: not applicable
```

then go to step 4. If it exists, run it from the repo root:

```
node e2e/scripts/e2e-coverage.mjs --pr <n> --json          # a PR exists
node e2e/scripts/e2e-coverage.mjs --base origin/<base> --body <file> --title "<branch or PR title>" --json
```

**The no-PR form needs `--title`.** A `feat` title in an area with no flows is one of the
new-surface signals, so omitting the title silently weakens the classification — `--pr` supplies it
from GitHub, nothing else does.

**Pass `--pr` without `--base`.** The script resolves the base itself — explicit `--base`, else the
PR's own base branch, else `origin/dev` — so supplying `--base` alongside `--pr` overrides the PR's
real base and silently mis-classifies a stacked PR. Use `--base` only in the no-PR form. Add
`--head <sha>` when the head being reviewed is not `HEAD`.

`--pr` reads the title, body and base from GitHub but classifies **the local checkout's diff** — so
the checkout must be at the PR's head. A result of `EXEMPT` with `autoReason: "no-changes"` means the
checkout is wrong, not that the PR is exempt; fix the checkout and rerun.

The one case that needs `--base` alongside `--pr` is an **already-merged** PR, where the PR's own
base branch has since absorbed the head and resolves to an empty diff. Pin both ends explicitly —
`--pr <n> --base <branch-point-sha> --head <head-sha>` — and say in the review that you did.

Map `verdict` to a finding with the table in
[references/e2e-coverage-policy.md](references/e2e-coverage-policy.md) — read it whenever the verdict
is anything but `pass`, when the classification looks wrong for the diff you just read, or when a
passing `EXEMPT` still names flows it hit:

The script returns exactly three verdicts:

| `verdict`      | What goes in the review                                                                                                                                                                                                                                                                                                              |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `pass`         | The E2E block, with the classification and the flows hit. No finding — **unless** the classification is `EXEMPT` and the line still names flows. `EXEMPT` is decided before hits are consulted, so that pairing is real and informational: read the diff and see the policy reference's `backend-internal` row before you accept it. |
| `needs-marker` | P1 "E2E coverage undeclared" — name the classification, the area(s), and the `E2E:` line the author should add.                                                                                                                                                                                                                      |
| `block`        | P1 "Declared exemption contradicts the diff" — quote the marker and the signal that contradicts it.                                                                                                                                                                                                                                  |

**`NEW-FLOW` passes on exactly one marker kind: `E2E: new <ID>`, with the `FLOWS.md` diff adding that
id.** `updated`, `covered-by` and `exempt` all come back `block`. `NEW-FLOW` answered with
`E2E: exempt (...)` is a reviewer override, not an author decision: ask the human one question —
accept the override, or hold the PR for a flow — and put their answer in the review.

If the coverage gate itself post-dates the PR you are reviewing (the script is absent from the base
you diffed against), the classification still goes in the E2E block, but the missing marker is a
**P2**, not a P1: the author could not declare against a gate that did not exist. Say which it is.

If any file under `e2e/` changed, also apply
[references/e2e-flow-review.md](references/e2e-flow-review.md) — the checklist for spec, catalog and
quarantine diffs. Read it only when `e2e/` is in the diff.

### 4. Mechanical gates with attribution — each keyed to a predicate

Run them; never eyeball them. Run each gate on the base's version of the same files first, and report
**only the delta**. A violation that exists identically on the base is pre-existing, and it is
reported as pre-existing — not deleted from the review because CI happens not to run that gate.

| Predicate                                                                         | Gate                                                                                                                                                          |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A `pyproject.toml` configures `[tool.ruff]` and `.py` files changed               | `ruff check` and `ruff format --check` on the changed files. A new F405 or F821 is a probable runtime `NameError` — treat it as P0 until you prove otherwise. |
| A file under a `migrations/` directory changed                                    | `makemigrations --check --dry-run` with the test settings. A second leaf node blocks.                                                                         |
| Any test file changed                                                             | Run the PR's own new tests. A failing new test ends the review until it is fixed.                                                                             |
| `frontend/package.json` defines the scripts, and serializers or contracts changed | `yarn lint`, and the contract check the repo defines (`yarn contracts:check`).                                                                                |

Never cite `yarn type-check` as evidence: without a `tsconfig.json` it is a no-op and proves nothing.

State in the review what you ran and what you did not run, with the predicate that decided it.

### 5. Trace the load-bearing path, then fan out by lens

Trace the main changed path end to end yourself first — inputs, the branch that changed, the value
written, the value read back. Then fan out.

The rules live in [references/standards-checklist.md](references/standards-checklist.md), grouped by
lens; load the section for the lens you are working, not the whole file. Dispatch one subagent per
lens when subagents are available, each carrying that lens's section; otherwise work the lenses
yourself in this order.

| Lens                   | Checklist sections                                              |
| ---------------------- | --------------------------------------------------------------- |
| Migrations & data      | Migrations & data · Data modelling                              |
| OSS/EE boundary        | OSS/EE boundary                                                 |
| Contracts & types      | Contracts, types & codegen                                      |
| Server/API layer       | Server/API layer                                                |
| Correctness & logic    | Correctness & logic                                             |
| Performance            | Performance                                                     |
| Errors & failure modes | Errors & failure modes                                          |
| Frontend               | Frontend                                                        |
| Naming, git & comments | Naming & git · Comments                                         |
| Testing & scenarios    | Testing & scenarios                                             |
| PR hygiene & shape     | PR hygiene · File size & structure · Infra & rollout · Security |

Run `git diff -w` over the diff as its own pass: a hunk that shrinks to nothing under `-w` is a
reindent, and a hunk that does not is a logic change hiding inside one. For every new indentation
level wrapped around old code, ask what now skips this.

If `../internal-docs/coding-standards/` exists, or `$FUTUREAGI_INTERNAL_DOCS` points at a checkout,
read the full standard behind a rule before writing the finding that cites it. Otherwise the
checklist row is the citation.

### 6. Adversarially verify every P0 and P1 before it is written down

Reproduce the mechanism, grep the claim across the repo, or read the framework's own code. A wrong P0
on a public review is worse than a missed P3.

If verification kills the finding, it does not go in the review — not as a hedged paragraph, not as
"defence in depth", not as a nit. If verification only weakens it, it goes in at the severity the
evidence supports.

### 7. Write the review

Fill every slot. `No findings.` replaces an empty findings section; never invent a finding to fill
one.

```
## E2E coverage
<classification> · <flows hit or "none"> · marker: <verified | missing | contradicts | n/a> · verdict: <pass | BLOCK | not applicable (no e2e/ harness)>

## Blocks merge
[P0|P1] <imperative title> — <file:line> — §NN — <one-line fix>   (introduced by this PR)

## Should fix
[P2|P3] <title> — <file:line> — §NN — <fix>   (introduced | pre-existing, touched | pre-existing, elsewhere)

## Good
- <specific thing done right, with file:line>

## Ready to merge?  Yes | With the fixes above | No
```

Rules for filling it:

- Lead with what blocks. Validation, praise and context come after the findings, in **Good**.
- Every finding carries `file:line`, a `§NN` reference from the checklist, and a one-line fix.
- Every finding carries its attribution. Only findings **introduced by this PR** may appear under
  **Blocks merge**.
- Name what is genuinely good with the same specificity as the bugs, and say plainly when the PR is
  mergeable with small fixes.
- Write it like a person: vary the voice, no reused scaffolding, no tooling attribution.

Then hand the review to the user. **The message you hand back ends with the posting question** — one
line, always present, including when nothing blocks and including when the PR has already merged:

> Post this on #NNNN as a comment / as request-changes, or hold it?

Match the kind to the severity: `comment` when nothing blocks, `request-changes` when something does.
Nothing reaches GitHub until the user answers that question.

## Severity

- **P0 — blocks.** Broken new code this PR introduced, the open build cannot deploy or migrate, data
  loss or corruption.
- **P1 — blocks.** A verified correctness or contract bug: wrong value recorded, response drift, a
  crash on a scheduled path, undeclared E2E coverage on a behaviour change.
- **P2 / P3 — should fix, does not block.** Edge cases, performance, consistency, hygiene.
- **Severity is not blocking.** A pre-existing P1 in a file this PR touches is a judgement call —
  "fix it while you are in here" or "file it urgently" — but the block is anchored on what this PR
  breaks. Do not draw an arbitrary line between two identical bugs.

## Red flags — STOP

Each of these means: go back to the step named and do it.

| Rationalization                                                                         | Reality                                                                                                   |
| --------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| "Small diff, skip the standards pass."                                                  | Diff size predicts nothing about severity. A one-line guard is the classic P0. Step 5.                    |
| "The body says it was tested."                                                          | A claim is not evidence. Step 2 lists it; step 4 runs it.                                                 |
| "A flow exists in that area, so it's covered."                                          | Coverage is per pinned surface, not per area. Step 3 answers this; guessing does not.                     |
| "It's only a refactor."                                                                 | Run `git diff -w`. A refactor that drops a handler, a prop or a branch is a behaviour change.             |
| "The migration is trivial."                                                             | Trivial migrations take locks, dangle leaves, and depend on enterprise-only apps. Run the gate.           |
| "Type-check passed."                                                                    | Without a `tsconfig.json` it is a no-op. Never cite it.                                                   |
| "I'll post it and fix the wording later."                                               | It goes out under a human's name. Show it, get the yes, then post.                                        |
| "Since this is already merged, these are tickets rather than findings."                 | Merge status changes the remedy, not the severity or the attribution. Report them at their real severity. |
| "CI doesn't run that gate, so it's not on this PR."                                     | Attribute it, don't delete it. Pre-existing is a label; silence is a miss.                                |
| "The PR's count presumably includes the file I didn't run."                             | Run it or say the number is unverified. Do not reconcile evidence by assumption.                          |
| "This isn't an active leak, but the sibling has it — leaving it in."                    | Verification killed it. It does not go in the review. Step 6.                                             |
| "It's pre-existing, but fix this first."                                                | Pre-existing findings do not lead the review and do not block. Rank by what this PR breaks.               |
| "None of these block, but here are five follow-ups."                                    | On an exempt or tooling diff, the honest output is `No findings.` plus **Good**.                          |
| "Unlikely — I confirmed the trigger doesn't exist — but free to close."                 | A finding whose trigger you proved absent is speculation. Drop it.                                        |
| "It conflicts with the convention, but I didn't want to raise it on someone else's PR." | Attribution is how you raise it fairly. Write it at its real severity.                                    |
| "The claim is unverifiable, so I'll suggest a body edit."                               | Unsupported body claims are findings, not copy-editing.                                                   |
| "The gate didn't exist when this merged, so I'll drop it."                              | It changes the severity, not whether it is reported. Record it at P2 with the reason.                     |
| "The review is written, so the job is done."                                            | The last line is the posting question. A review nobody was asked about cannot be posted.                  |

## Out of scope

- No fixes applied. This procedure produces a review, not a commit; do not edit the working tree,
  stage anything, or push.
- Nothing is posted, commented, approved, labelled or requested-changes on GitHub without an explicit
  yes from the user for that specific action.
- Not for reviewing a design doc, a spec, or a single file with no diff behind it.
- Not for writing the E2E flow the gate asks for — that is the authoring skill's job; this one names
  the gap and stops.
