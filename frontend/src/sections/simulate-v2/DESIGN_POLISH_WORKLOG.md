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
- [ ] A. Data/number + color coherence — reconcile pass-rate labels across run screens; one pass/warn/fail scale; drop ad-hoc peach/pink tints.
- [ ] B. Clipped text / overflow — Scenarios table columns; Interface (RL) code block.
- [ ] C. Lead with the verdict — Traces rows; live-run task titles (persona first).
- [ ] D. Missing states & flows — one not-found component; twin routes; orphaned /simulate/runs; empty states.
- [ ] E. Chart craft & compare — Simulations summary deltas + "1 runs" grammar + hide chart when n<3.
- [ ] F. Cut self-narrating prose — connect screens, list-page H1 subtitles, workspace headers, button labels.
- [ ] G. Chrome/overlay — Overview CTA consolidation; content bottom padding so FABs don't collide.

## Rules
- Only commit simulate-v2 design files (+ shared primitives/routes as needed). NEVER commit the
  dev-only auth stub (`auth-provider.jsx`) or fast-play (`useRunPlayer.js`) — kept uncommitted.
- Match existing code idioms. No new deps. Run lint after edits.
- Verify each screen visually (Playwright capture) before/after; Fable critic on the changed screens.

## Log
- (start) branch cut; design system read; worklog created.
