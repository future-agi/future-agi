import { test } from '../../lib/walk/fixtures';
import { checkConsoleAbsence, consoleAnnotation, consoleOptions, type ConsoleLeg } from './console-absence';

// JOURNEYS-708 v2 J18. Hidden/refused triggers never satisfy C6. Cost does not
// qualify cap semantics; J13.W04 remains a separate, digest-bound dependency.
const FLOW = 'JRN-E2E-018';
const APPLIES = 'tuple:T-FULL & admitted:console';
const legs: readonly ConsoleLeg[] = [
  { id: 'L-W', title: 'Start exactly one run from a signed webhook', applies_when: APPLIES,
    flags_required: ['G04 triggers'],
    steps: ['Register a signed webhook trigger', 'Deliver a signed event',
      'Replay the event', 'Deliver an event with a bad signature'],
    pass_criteria: 'Exactly one run per signed event; replay and bad signature refused',
    gap: 'HR-CJ-11: need accepted G04 registration/ingress contract, synthetic signer and delivery receipt probe; assert exactly one correlated run, replay refusal, bad-signature refusal and no extra run/provider call',
    // schedules/events is explicitly refused to host callers in EE
    // managed-auth/host-hidden-features-http.test.ts; no guessed webhook URL.
    reads: ['/omega/api/workspace/schedules', '/omega/api/workspace/schedules/events'], model: true },
  { id: 'L-S', title: 'Run, restart, pause and delete an agent schedule', applies_when: APPLIES,
    flags_required: ['/workspace/schedules not in the union manifest hidden list', 'console rollout stage'],
    steps: ['Create an independent agent and near-future schedule; GET the saved schedule',
      'Run now once, then let the scheduled time fire', 'Restart the EE daemon across a scheduled time',
      'Pause and prove no fire, then delete', 'Org B lists, reads, runs and deletes org A schedule by id'],
    pass_criteria: 'Run-now starts exactly one run; each scheduled time fires exactly once across restart; no paused fire; org B refused; hidden if not qualified',
    gap: 'HR-CJ-12: need isolated daemon restart/time control and schedule-run receipt reader (FA TemporalProbe does not control EE schedules); assert saved schedule, run-now/fire uniqueness, restart persistence, pause/delete absence and unchanged A state after B refusals',
    // 7C route manifest and customer OpenAPI: schedules GET/POST, detail
    // GET/PATCH/DELETE, /{schedule}/run POST. Absence never invents a child ID.
    reads: ['/omega/api/workspace/schedules'], model: true },
  { id: 'L-C', title: 'Read cost by agent, key and run', applies_when: APPLIES,
    flags_required: ['usage_spend'],
    steps: ['Create an independent profile-mock-backed billed run',
      'Open cost by agent, key and run using the accepted gateway read APIs'],
    pass_criteria: 'Cost numbers equal ledger debits',
    gap: 'HR-CJ-13: need accepted TX3-A attribution contract and profile-model billing fixture; compare exact integer monetary values and correlated agent/key/run identities against ledger deltas, never token count or rounded display alone',
    // 7C manifest: workspace/usage; gateway monetary contracts remain owed.
    reads: ['/omega/api/workspace/usage'], model: true },
  { id: 'L-K', title: 'Show a cap refusal before provider dispatch',
    applies_when: `${APPLIES} & served:gateway`, flags_required: ['G8-B caps'],
    steps: ['Set a G8-B daily cap through the admitted Console', 'Exceed it from an independent Console run'],
    pass_criteria: 'Typed refusal before dispatch, displayed in Console; cap semantics require J13.W04',
    gap: 'HR-CJ-14: need accepted G8-B Console cap surface and J13.W04 receipt binding; assert persisted cap, typed refused terminal run and unchanged profile-mock dispatch receipts and ledger',
    reads: ['/omega/api/workspace/usage', '/omega/api/workspace/conversations'], model: true },
];

test.use(consoleOptions);
for (const leg of legs) {
  test(`${FLOW}.${leg.id}: J18.${leg.id} ${leg.title}`, {
    tag: ['@flow', '@journey', '@journey-J18', '@mock-provider'],
    annotation: consoleAnnotation('J18', FLOW, leg),
  }, async ({ scopeActors, scopeProbe, request, walk }, info) => {
    test.setTimeout(480_000); // 4 x UI_READY + bounded backend reads and fixture setup.
    await checkConsoleAbsence('J18', leg, { scopeActors, scopeProbe, request, walk }, info);
  });
}
