# Contribution Policy

Future AGI is built by a small team, and we take outside contributions seriously. This page is
how we keep the queue reviewable so the good work gets attention within days, not weeks.

It is short on purpose. Read it once.

## The four rules

**1. Issue first, for anything you can't finish in an afternoon.**
Bug fixes under ~50 lines can go straight to a PR. Everything else needs an issue that a
maintainer has labelled `accepted` before you write code. PRs for un-accepted work are closed
with a pointer to open the issue; the code isn't lost, but we won't review it until the
problem is agreed on. This is the rule that would have saved you the most time in the past.

**2. You must understand and have run your change.**
If you can't explain what the diff does and how it interacts with the rest of the system
without AI help, don't submit it yet. Every PR must have been run locally against the real
stack (`bin/test up` for backend, `yarn dev` for frontend) and the checks you ran must be
pasted, not described. "Tests pass" with no output is treated as "not run".

**3. AI is fine. Disclose it, and drive it.**
Most of us use Claude Code, Cursor or similar daily and we have no interest in banning them.
What we do require:

- State the tool and how much of the change it produced, in the `AI use:` line of the PR
  template. Undisclosed AI that a reviewer spots gets the PR closed.
- The PR body is written by you. Marketing copy, badges, "Reviewer Guide" sections, or the
  description committed into `docs/` are signs the agent ran unattended; those PRs are closed.
- Verification scripts that mock the codebase to make an import work are not verification.
  If you can't run the real suite, say so and we'll help; don't fake it.
- Never let an agent argue with a reviewer, open follow-up PRs, or post anywhere on your
  behalf. If a review comes back, a human reads it and replies.

**4. Three open PRs at a time, until we know you.**
Outside contributors can have three PRs open at once. After two merges you're added to the
bypass list and the limit no longer applies to you. We'd much rather review one good PR than
five drafts.

## What gets a PR closed without full review

We close fast when review would be wasted effort, and we say why in the closing comment.
Any of these is enough:

- No accepted issue for a change bigger than an afternoon's work.
- Claims in the body that don't match the diff: test counts, benchmark numbers, "the repo has
  no test suite", pasted output that the script doesn't produce.
- The change was clearly never run: import errors, failing tests it ships, migrations that
  fail `makemigrations --check`, ID collisions the seeder would have caught.
- Duplicates an open PR for the same issue without saying so (comment on the existing one
  instead).
- Reimplements something that already exists in the repo without explaining why the existing
  one is wrong.
- Targets `main`. We branch from and merge to `dev`.

A closed PR is not a verdict on you. If you think we've misread it, reopen it and say where.
Every close comment says this because we mean it.

## What gets a PR merged fast

- Small, one logical change, conventional commit title, branch named `type/short-description`.
- Linked issue, or a clear one-paragraph "why" for a small fix.
- A regression test that fails without the fix. Reviewers check this by reverting your change.
- `E2E:` line filled per the template (`yarn coverage` from `e2e/` tells you which marker).
- Real pasted output from `make check-all` / the test run.
- Follows the conventions of the code next to it. Look at the sibling code before writing new
  patterns; the review will point at siblings if you don't.

Our target: first response inside 3 business days, merge or clear next step inside a week.

## How review works here

Every PR goes through the same pipeline, and knowing it saves you a round-trip:

1. **Admission check (automatic, minutes).** Template filled, issue linked, branch/target
   right, disclosure present, size sane. Fails here → comment listing exactly what's missing;
   fix and push, it re-runs.
2. **Mechanical gates (automatic).** ruff / eslint / migrations / contract checks / e2e
   coverage classifier / secret scan. Same as CI, reported against the base branch so you only
   see what you introduced.
3. **Standards review (automatic, then human).** The change is reviewed against our internal
   review playbook, traced through callers, and independently checked by a second model. A
   maintainer reads the result and posts it. Findings are graded: blocks merge / should fix /
   nit. Nits we usually just fix ourselves.
4. **Tests, run for real.** Your tests, the touched module's suite, and a regression proof
   (your tests must fail with your fix reverted).
5. **Merge.** A maintainer merges. Anything touching billing, auth, data migrations or
   tenancy always gets a second human regardless of what the automation says.

## Issues

Bug reports need: version/commit, environment, exact repro steps, expected vs actual, logs.
Reports missing these get `needs-info` and close in 7 days without a reply. Feature requests
describe the problem before the solution. AI-assisted issues are fine; AI-generated walls of
text are not, trim them.

Security issues go to the [security policy](SECURITY.md), never to a public issue or PR.

## Maintainers

Maintainers are exempt from rules 1 and 4 and use AI tools at their own discretion; they've
earned that. Everything else applies to everyone.
