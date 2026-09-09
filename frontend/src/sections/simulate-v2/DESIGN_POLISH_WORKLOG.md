# Simulate Studio v2 — design polish worklog

Branch: `feat/sim-studio-v2-design-polish` (cut from `feat/simulation-studio-v2`).
Goal: raise design quality + fill missing states/flows, grounded in the existing FutureAGI
design system (theme palette + `components/primitives.jsx`). Fable used as design critic.

Design system anchors:
- Brand primary `#7857FC`; semantic `success #5ACE6D`, `warning #F5E65F`/icon `#8C3F08`,
  `error #DB2F2D`, `info #2F7CF7`. Grey scale has a violet bias.
- Reuse primitives: `Verdict`, `StatusChip`, `StatusDot`, `ScorePill`, `MetricTile`,
  `SectionCard`, `EmptyState`, `PersonaBadge`, `DomainChip`, `VERDICT_TONE`, `STATUS_META`.

## Workstreams (priority order)
- [~] A. Data/number + color coherence — Traces "not measured" cells now show "—" (no phantom score); CSAT alarm reserved for 1-2; VERDICT_TONE gained an amber `error`. Deeper cross-screen count reconciliation (40 vs 50 scenarios; agent v1 vs "no agent"; gate "cleared to ship" vs run failures) left for dev — it spans mock selectors.
- [x] B. Clipped text / overflow — Scenarios table (fixed layout + colgroup); Interface code (pre-wrap).
- [x] C. Lead with the verdict — Traces rows lead with Verdict + specific scenario name + summary.
- [x] D. Missing states & flows — shared EmptyState with a real exit on twin-detail / twin-connect / use-template; /simulate/runs redirect.
- [~] E. Chart craft & compare — "1 runs" grammar + misleading copy fixed; trend chart held until >=3 runs. Per-column deltas: the baseline-delta feature already exists on baseline-select; auto-defaulting it is a follow-up (don't destabilise baseline UX).
- [~] F. Cut self-narrating prose — fixed the re-prove banner casing/length; entry-card affordance. Broad prose trim left partial (brand voice is deliberate; trimmed only clear dupes).
- [ ] G. Chrome/overlay — Overview CTA consolidation + FAB overlap: recommended for dev (touches global layout / shared toolbar).

## Follow-ups recommended for dev (higher-risk / cross-cutting)
- One source of truth for counts + statuses (40/50 scenarios; agent-attached; release gate vs run verdict).
- Collapse the 3-tier workspace header to 2; single primary CTA; disabled "Run simulation" needs a reason.
- FAB + avatar overlap on right-aligned content (global layout).
- Traces trailing grader columns still overflow right; cap numeric widths / column reorder.

## Rules
- Only commit simulate-v2 design files (+ shared primitives/routes as needed). NEVER commit the
  dev-only auth stub (`auth-provider.jsx`) or fast-play (`useRunPlayer.js`) — kept uncommitted.
- Match existing code idioms. No new deps. Run lint after edits.
- Verify each screen visually (Playwright capture) before/after; Fable critic on the changed screens.

## Log
- (start) branch cut; design system read; worklog created.
- Commit 1: clipped-text fix (Scenarios fixed table layout; Interface pre-wrap).
- Commit 2: lead run rows with verdict; CSAT alarm 1-2; error tone; summary grammar/copy + chart gate.
- Commit 3: graceful shared not-found states + /simulate/runs redirect + PropTypes.
- Commit 4: Traces name/summary lead; grader "—" for not-measured; drop vertical grid; banner casing.
- Commit 5: chart held to >=3 runs; self-improve "up to N%" neutral; amber (not red) measurement-fix card.
- Commit 6: entry-card violet hover + persistent arrow affordance.
- Verified each screen via Playwright capture; two Fable critic passes drove the priorities.
