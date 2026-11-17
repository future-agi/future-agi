import { test } from '../../lib/walk/fixtures';
import { checkConsoleAbsence, consoleAnnotation, consoleOptions, type ConsoleLeg } from './console-absence';

// JOURNEYS-708 v2 J19.L1 is one chained goal, not four passing fragments.
const FLOW = 'JRN-E2E-019';
const leg: ConsoleLeg = {
  id: 'L1', title: 'Evaluate, block publication, fix, publish and roll back an agent version',
  applies_when: 'tuple:T-FULL & admitted:console',
  flags_required: ['TX1-A evaluation routes', 'approved capped real-model relay'],
  steps: ['Describe the agent; draft the agent and exactly 20 test cases',
    'Evaluate the pinned version on a DSX dataset through the production VersionEvaluationPort adapter',
    'Attempt publish below the required pass rate and read the immutable blocked receipt',
    'Fix, re-evaluate, publish and roll back'],
  pass_criteria: 'Exactly one verdict per pinned case; publish and rollback receipts immutable; the gate cannot be bypassed',
  gap: 'HR-CJ-15: need admitted TX1-A production adapter, deterministic 20-case drafting fixture and profile-selected mock provider with receipts; assert exact case/version/dataset binding, unique verdicts, blocked active-version invariant, successful published version, rollback identity and immutable receipts after reread. No real model call is authorized by this lane',
  // 7C route manifest workforce GET; TX1-A adds /agents/{id}/evaluation-gate
  // PUT and /agents/{id}/evaluations GET/POST. An invented agent ID would only
  // prove nonexistent-resource handling, so the absent collection gate is used
  // until the harness can supply a real scoped agent (HR-CJ-15).
  reads: ['/omega/api/workspace/workforce'], model: true,
};

test.use(consoleOptions);
test(`${FLOW}.${leg.id}: J19.${leg.id} ${leg.title}`, {
  tag: ['@flow', '@journey', '@journey-J19', '@mock-provider'],
  annotation: consoleAnnotation('J19', FLOW, leg),
}, async ({ scopeActors, scopeProbe, request, walk }, info) => {
  test.setTimeout(480_000); // 4 x UI_READY + bounded backend reads and fixture setup.
  await checkConsoleAbsence('J19', leg, { scopeActors, scopeProbe, request, walk }, info);
});
