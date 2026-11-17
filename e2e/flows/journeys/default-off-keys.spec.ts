import { test, expect } from '../../lib/trace-hygiene/fixture';
import { provisionActor } from '../../lib/provisioning';
import { ApiError } from '../../lib/api-client';
import { flowAnnotation } from '../../lib/flow-meta';
import { JOURNEY_PUBLICATION_ACCEPTED, JOURNEY_PUBLICATION_GAP } from './publication-contract';

// Never call provisionActor while the publication contract is unaccepted.
test.skip(!JOURNEY_PUBLICATION_ACCEPTED, JOURNEY_PUBLICATION_GAP);

// JOURNEYS-708 J00 amendment: zero OrgApiKey and AgentccAPIKey
// before explicit Owner create; GET /accounts/keys/ returns 410, creates zero.
// The existing harness provisionActor calls that bootstrap before returning an
// actor. Reuse it only to detect the served behaviour. It CANNOT prove the
// pre-bootstrap census. HR-JRN-17 asks its owner for keyless phased provisioning.
// No signup implementation, SQL mutation or alternate actor harness in this spec.
const GAP = 'HR-JRN-17: 733 bootstrap returns 410, but provisionActor cannot return a keyless owner or expose the pre-bootstrap org; need keyless signup/session fixture plus read-only before/after OrgApiKey and AgentccAPIKey counts';

test('JRN-E2E-000.L11: J00.L11 no automatically created keys', {
  tag: ['@flow', '@journey', '@journey-J00'],
  annotation: [flowAnnotation({ id: 'JRN-E2E-000.L11', area: 'journeys',
    userGoal: 'A fresh organization has no API key until its owner explicitly creates one',
    steps: ['Detect 733 by the served authenticated bootstrap response, never by a source flag',
      'When absent report NOT_PRESENT with the bootstrap status',
      'When present require the keyless fixture before asserting zero key rows before and after bootstrap'],
    backendChecks: ['Presence requires GET /accounts/keys/ = 410',
      'Owed until HR-JRN-17: zero OrgApiKey and zero AgentccAPIKey rows before explicit create, unchanged after bootstrap'] }),
    { type: 'journey-flow-id', description: 'JRN-E2E-000' }],
}, async ({ request }, testInfo) => {
  test.setTimeout(120_000);
  let bootstrapStatus: number;
  try {
    // The harness returns only after an authenticated successful GET /accounts/keys/.
    await provisionActor(request, `j00-key-census-${testInfo.workerIndex}`);
    bootstrapStatus = 200;
  } catch (error) {
    if (!(error instanceof ApiError) || error.path !== '/accounts/keys/' || error.status !== 410) throw error;
    bootstrapStatus = 410;
  }
  await testInfo.attach('key-bootstrap-presence', { contentType: 'application/json',
    body: JSON.stringify({ bootstrapStatus, present: bootstrapStatus === 410 }) });
  if (bootstrapStatus !== 410) {
    const reason = 'NOT_PRESENT: served authenticated GET /accounts/keys/ returns 200, not 410; ruling 733 is not in this stack';
    testInfo.annotations.push({ type: 'journey-outcome', description: reason });
    test.skip(true, reason);
  }
  expect(bootstrapStatus, '733 presence is served behaviour').toBe(410);
  test.skip(true, GAP);
});
