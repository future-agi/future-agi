# Approve and build: plan

How a person reviews and approves an environment's design before the first build, when building from an existing agent. Covers the backend, frontend and the ALK changes it needs.

## 1. Goal

Before the first cloud build of an environment, show the person what will be built (record tables, tools, rules, use cases and planned scenario coverage), let them change it, and build only after they approve. Then build the world, tools, checks and scenarios from the approved design.

PRD: builder step 3 "Approve the proposed design", "Build from an existing Agent version" steps 2 and 3, and task T02 (week 1, never cut). "Approval starts the cloud build … never skips this design approval." "Approval is never bypassed."

## 2. Decisions already made

| Decision | Result |
|---|---|
| Who sees a design review | Builds from an existing agent. From-scratch and template designs are Azain's design mode (ALK `propose_design`, already merged in ALK). |
| When | Only before the **first** version. Later changes use the existing draft, diff and save, which makes a new environment version (PRD step 6). |
| Agent and environment | Decoupled. Edits are stored on the environment and never written to the agent version. The agent's contract is only read. |
| Scenarios | Generated after approval, during the build, from the edited contract. Not before. |
| Deletes on later versions | Allowed (option A, as the PRD says), after an impact warning. Each delete makes a new environment version; blocked scenarios are kept with the reason. |
| Dependent tools | Before a delete, list the tools that need it (`requires`, `inputs_from`), the use cases (before v1) or scenarios (v2 and later) it blocks, and offer Keep, Fix it instead, or Remove anyway. Keep is the default. |
| New agent version later | No second review. Dev's rebuild (from #3533) runs one builder turn on the current environment with only what changed between the agent versions, so the edits made in review stay. The one gap is when the agent change touches something the person removed or edited; section 9 covers it. |
| Where the review is stored | On the environment's own `HostedHarnessJob` row, not a new table. See section 6. |

## 3. Decisions still open

| Question | Options | Who |
|---|---|---|
| How heavy the review is for agent builds | B: a summary with Build first and Review details on request. D: the full review every time. | Manager, Vel |
| First save | "Review and save v1" by hand, or saved on its own once the checks pass | Manager, Vel |
| Hosted agents (Vapi, Retell, LiveKit, phone) | ALK ignores a seed contract for them by design. Edits would have to go as instructions that ALK applies after its own reading. | Khushal |
| Reuse of Azain's design mode | Reuse `propose_design`, park and Continue, or keep a separate gate | Azain |

## 4. Scope

**In, first:** code agents that already have a stored reading, which are "Your agents" and agents connected on the Agents page (GitHub or folder). Their stored contract already holds everything the review needs.

**In, second:** GitHub and folder builds started from the drawer. These have no reading yet, so the flow first connects the agent (which runs the reading), then shows the review.

**Out, for now:** hosted agents (needs the ALK instructions channel), try-a-tool, the Fixed reply tier and injected faults (week 2), must-pass.

## 5. Flow and states

```
Build from an agent
  → Draft          proposal made from the agent version's stored contract
  → Awaiting approval   person reviews and edits; nothing is built
        (Parked)    left open; Continue resumes the same proposal, never a new one
  → Approved       edited contract made; build starts with it as the seed
  → Building       world, tool handlers, checks, then scenarios
  → Ready to save  release checks passed
  → Saved          environment v1 + scenario v1
Discard at any point before Building: nothing is built and the agent is untouched.
```

The job waits in the existing `received` state, which nothing else sets today, and its design review has the status `awaiting_approval`. No workflow starts while it waits, so the jobs table needs no change. The environments list shows it as Building; the review page labels it "Awaiting your approval".

## 6. Data model

**No new table and no migration.** The review is stored on the environment's own `HostedHarnessJob` row. That row *is* the environment (a job with `environment` empty), so "edits are stored on the environment" holds without a second model. It follows Azain's design mode (#3612), which keeps its design result in `payload["metadata"]["design_result"]`, the job's state and `stage_outputs`, and adds no table for the design itself.

| What | Where on `HostedHarnessJob` |
|---|---|
| Waiting for approval | `state = received` (nothing else sets it today) and `current_stage = "awaiting_approval"`. No workflow runs while it waits. |
| The agent version it came from | `agent_version`, the existing field |
| Proposal, edits and approval | `payload["metadata"]["design_review"]` = `{status, source_contract_hash, proposal, edits, approved_by, approved_at}` |
| `status` | `awaiting_approval`, `approved`, `discarded` |
| `proposal` | Tables (with columns), tools (with `requires`, `inputs_from`, effects, refusals), rules, use cases and the scenario count, each with its source. Planned scenarios per use case are worked out when read. |
| `edits` | Ordered list of `{op, kind, id, text, by, at}`: remove a table, tool, rule or use case; add or reword a rule. At least one use case must stay. |
| `approved_by`, `approved_at` | The approver's user id and display name, and the time |
| Discard | The existing cancel: `cancel_requested_at` with `cancel_reason = "design_discarded"` |

Writes lock the row (`select_for_update`), as #3612 does. `payload` can be changed after creation: idempotency compares the stored `request_digest`, not `payload`.

**Why not a separate `HarnessDesignReview` table (the first draft):**

| | On the job (chosen) | Separate table |
|---|---|---|
| Migration | None | One new table |
| Matches the from-scratch design mode | Yes, same place as `design_result` | No, a second pattern for one PRD step |
| Lives with the environment for later rebuilds | Yes, the row is the environment | Needs a join, or a copy on each version |
| Audit of who approved | User id and name in JSON, no foreign key | Foreign key to the user |
| Querying "everything awaiting approval" | JSON key filter on `payload` | Plain column filter |

The two costs of the job option are small: approvals are read per environment, never listed across the organization, and the approver's name is kept with the id.

**Dropped: `environment_edits` on `HarnessEnvironmentVersion`.** The edits stay on the environment row for its whole life, so a version doesn't need its own copy.

The agent version and its stored contract are not changed.

**Undo the first draft's local state** before phase 1 starts again: unapply migration `0116_harness_design_review` on the local database (with a yes first) and delete the model, migration and its service, serializer and test files.

## 7. API

All under the environment (`harness-environments/{id}/design-review/`):

| Method | Path | Does |
|---|---|---|
| GET | `design-review/` | Proposal with the edits applied, the edits list, status, and impact for each item |
| POST | `design-review/edits/` | Add one edit. Returns the updated proposal and the impact of that edit |
| DELETE | `design-review/edits/{index}/` | Undo one edit |
| GET | `design-review/impact/?kind=tool&id=check_otp` | What removing an item would block, before the person confirms |
| POST | `design-review/approve/` | Build the edited contract, start the build, set status `approved` |
| POST | `design-review/discard/` | Discard the draft. Nothing is built. |

Creating the build (`POST harness-jobs/`) stops at `received` for these builds instead of starting the workflow. All of it sits behind `HARNESS_DESIGN_REVIEW_ENABLED`, off by default.

## 8. Building from the edited contract

1. Copy the agent version's stored contract.
2. Apply the edits in order: remove tools and tables, edit or remove rules, remove use cases. Keep the contract valid (no duplicate tools, at least one use case).
3. Upload it as the seed (`/work/seed/contract.json`). The build reads it instead of reading the agent again.
4. **Guard 1, no silent fallback:** ALK quietly reads the agent again when a seed doesn't match, which would drop the person's edits. When there are edits, the backend reads ALK's seed result and fails the build with a clear reason instead.
5. **Guard 2, no write-back:** today a seeded build's contract replaces the agent version's contract (`store_build_contract`). Skip that when the build had edits, so the agent stays as it was read.
6. **ALK fix needed first:** every seed is a mismatch today, because readings don't carry the build's eval list (`available_evals`), so the eval hash never matches. Khushal's area.

## 9. Rebuilds keep the review's edits

**How a rebuild works on dev today.** When the agent gets a new version, the environment shows a card with Skip and Rebuild environment. Rebuild runs one builder turn on the *current* environment version. The briefing names only what changed between the two agent versions: "Rebuild the environment for agent v2 (it was built for v1)", the change summary, the changed files and their patches (`harness_versioning/rebuild.py`, `briefing`). The result lands as the next environment version, with the current one as its parent.

**What that means for review edits.** Env v1 was built without the tools and tables the person removed, and with the rules as they edited them. The rebuild starts from env v1, so every edit the agent change doesn't touch stays. Agent v2 is only the new code: it never holds the edits, and still has the tool the person removed.

**The gap.** When agent v2 changes something the person removed or edited, the briefing shows that change, and nothing tells the builder the removal was on purpose. It can add the removed tool back or rewrite the edited rule, and the person loses the edit without being asked. Vel's design already makes each changed rule need an answer ("Policy change / Not intended"). Nothing covers a removed tool or table coming back.

**Plan:**

1. **Find the overlap when the nudge is made.** Compare the agent change (the contract diff between the two agent versions, plus the changed files) with the environment's `design_review.edits`. Store the overlapping items in the nudge's facts as `review_overlap`.
2. **Ask before rebuilding.** For each overlapping item the card asks first, and Rebuild stays disabled until every one is answered:
   - A removed tool or table the agent changed: "Agent v2 changed `check_otp`, which you removed in review." **Keep it removed** (default) or **Bring it back**.
   - An edited rule whose source changed: the existing "Policy change / Not intended" answer.

   The answer is added to `edits` (Bring it back is a new `restore` edit), so later rebuilds know it.
3. **Brief the builder every time.** The rebuild briefing gets a last line listing the review's edits that still apply, for example: "Kept on purpose from the design review: removed tools `check_otp`, `send_sms`; rule 3 reads '…'. Keep them as they are." This goes in even with no overlap, so a builder turn never reverses an edit by accident.
4. **Check before landing.** Before the rebuild's draft becomes the new version, check its contract: no tool or table removed in review is back unless the person chose Bring it back, and every edited rule still reads as edited. If either check fails, don't land: the rebuild ends with `review_edit_undone`, and the card offers Retry.
5. **Out of scope:** hosted agents, as in section 4.

**Where the code goes:** the nudge facts in `harness_versioning/nudges.py`; the briefing and the landing check in `harness_versioning/rebuild.py`; the questions on the card in `NudgeCard.jsx` and the Agents page's `RebuildAction.jsx`. Jaya owns this versioning code (#3533). Vel designs the copy for the removed-tool question.

**Testing:** backend tests for the overlap, the briefing line and the landing check. An E2E flow needs a real rebuild, which the E2E stack can't run yet: `mock-daytona` answers readings, not builder turns. Covering it means teaching the mock a canned builder turn; that is a harness change to agree first.

## 10. Frontend

- **Review page:** Vel's "Review the design" as the build page while the job is `awaiting_approval`: Records, Tools, Rules, Use cases with planned scenario counts, and Can't build here. Remove on each row, an inline edit for rules, an Undo line, and **Approve and build**.
- **Remove dialog** when there are dependents: lists the tools that need it and the use cases it blocks; Keep (default), Fix it instead (opens the builder chat), Remove anyway.
- **Entry points:** "Your agents" in the drawer and the agent page's Build environment open the review instead of building straight away. Drawer GitHub and folder come second.
- **Leaving and coming back:** the address and session storage already resume the drawer. The review itself is saved on the server, so Continue opens the same proposal.

## 11. Phases and branches

Base branch `feat/TH-10550-approve-and-build`, cut from the environments drawer branch (PR #4206 is not merged yet). One pull request per phase, rebased onto `dev` once #4206 lands.

| Phase | What | Layer | Needs |
|---|---|---|---|
| 1 | Review stored on the job (no migration), `awaiting_approval` stage, proposal from the stored contract, edits, impact, approve and discard endpoints | Backend | Removing the first draft's table and migration 0116 (a yes before unapplying it locally) |
| 2 | Review page, remove dialog, entry points for "Your agents" and the agent page | Frontend | Phase 1 |
| 3 | Edited contract as the seed, and the two guards | Backend | The ALK eval-hash fix |
| 4 | Drawer GitHub and folder: connect, then reading, then review | Backend and frontend | Phases 1 to 3 |
| 5 | Rebuilds keep the review's edits: overlap check, questions on the card, briefing line, landing check (section 9) | Backend and frontend | Phase 3; Jaya for the versioning code; Vel for the card copy |

## 12. Testing

- Backend tests written once each phase's code is final, run with `bin/test`, and left to CI unless asked.
- Each phase is proved on the running stack: create an environment through the UI, review, remove a tool that others depend on, approve, wait for the build, and start a one-scenario run. Check in the database that the agent version's contract is unchanged and the environment's job row holds the edits.
- Each phase ships an E2E flow (new or updated). The review and approve steps need the agent reading that `mock-daytona` already provides; the build and rebuild steps need a canned builder turn, which the mock doesn't have yet.

## 13. People to involve

| Who | About |
|---|---|
| Khushal | ALK eval-hash fix; whether a seeded build can re-add a removed tool; hosted agents |
| Azain | Reusing `propose_design`, park and Continue, and his design page |
| Karthik Avinash | The release checks in his readiness work, shown after the build |
| Vel | The follow-ups: dependency warnings, Fix this tool, review weight, first save; the copy for the removed-tool question on the rebuild card |
| Jaya | The rebuild changes in `harness_versioning` (#3533): overlap in the nudge, the briefing line, the landing check |
| Manager | The open decisions in section 3 |

## 14. Risks

- **Silent loss of edits** if the seed doesn't match. Guard 1 covers it, but only once ALK reports its seed result reliably.
- **Approval friction:** if most people click Approve without reading, the summary option (B) keeps it to one click.
- **Agent changes during the review:** `source_contract_hash` notices it; the proposal is rebuilt and the edits re-applied.
- **Overlap with Azain's design mode:** two gates for one PRD step. Agree on the shared pieces before phase 2. Storing the review where #3612 stores its design result keeps the two closer.
- **A rebuild undoing a review edit:** the briefing line asks the builder to keep the edits; the landing check catches it if the builder doesn't.
