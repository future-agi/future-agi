import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { certifyCandidates, seedHarnessEnvironment, seedReviewDraft } from '../../lib/simulate-seed';

// Routes and bodies pinned from futureagi/simulate/views/harness_environment.py and the serializers in
// futureagi/simulate/serializers/harness_environment.py (HarnessReviewDecisionsSerializer,
// HarnessDraftValidateSerializer, HarnessDraftSaveSerializer, HarnessDraftsSerializer).
const ENVIRONMENTS = '/simulate/api/harness-environments';
// An actor the person adds directly (futureagi/simulate/tests/test_harness_actors.py MANAGER, without the
// world actions the seeded Environment does not have).
const MANAGER = {
  id: 'manager',
  name: 'Priya Raman',
  role: 'support manager who approves refunds above $50',
  goal: 'Approve only refunds she can justify',
};

interface Source { agent: { kind: string }; used?: { kind: string; id: string }[] }
interface Change { id: string; kind: string; sources: Source[] }
interface Review { drafts: Record<string, { revision: number }>; changes: Change[]; review_digest: string }
interface Candidates { environment_version_id: string; suite_version_id: string }
interface Validation { state: string }
interface Drafts { environment: { revision: number; parent_id: string } | null; validation: Validation | null; candidates: Candidates | null }
interface Validated { validation: Validation; candidates: Candidates }
interface Saved { saved: Record<string, { id: string }> }

test('SIM-E2E-005: a reviewed draft saves only the accepted change, and only through Validate', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-005', area: 'simulate',
    userGoal: 'A builder reviews a chat-proposed rule and their own actor edit, keeps the rule, rejects the actor, validates and saves a named version holding only the rule',
    steps: ['seed a built, versioned Environment and a draft rule the builder chat added in answer to a message (e2e/lib/simulate-seed.ts; no hosted build runs in this harness)',
            'add a Manager actor through the actors route',
            'read the review: the rule cited to the message, the actor to the person',
            'accept the rule and reject the actor',
            'try a plain Save with the review digest',
            'Validate',
            'stand in for the validation job by certifying the candidate pairing (no sandbox in this harness)',
            'read the drafts until validation is ready, then Save the candidates with a name'],
    backendChecks: ['a reviewed Save without candidates is refused 409 validation_required, and the default does not move',
                    'Validate answers 202 with candidates, which the drafts read returns in the same shape, while the default stays put',
                    'after Save the Environment default is the candidate version, active and named, holding the rule and its amendment line and not the actor',
                    'no draft is left open once saved'],
  }),
}, async ({ actor, probe }, testInfo) => {
  // Seeding runs three docker exec scripts; everything else is synchronous API calls.
  test.setTimeout(120_000);
  const environment = seedHarnessEnvironment({
    organizationId: actor.organizationId, workspaceId: actor.workspaceId, refunds: 1, lookups: 0,
  });
  const draft = seedReviewDraft({ environmentId: environment.environmentId, userEmail: actor.email });
  await testInfo.attach('seed', { contentType: 'application/json', body: JSON.stringify({ environment, draft }) });
  const base = `${ENVIRONMENTS}/${environment.environmentId}`;
  const defaultVersion = async () => (await probe.pg<{ environment_version_id: string }>(
    'SELECT environment_version_id::text FROM simulate_hosted_harness_job WHERE id = $1', [environment.environmentId],
  ))[0].environment_version_id;
  expect(await defaultVersion()).toBe(environment.environmentVersionId);

  let review = await actor.api.get<Review>(`${base}/drafts/review/`);
  await actor.api.post(`${base}/actors/`, { actor: MANAGER, expected_revision: review.drafts.environment.revision });

  review = await actor.api.get<Review>(`${base}/drafts/review/`);
  const rule = review.changes.find((one) => one.kind === 'rule');
  await test.step('api: the rule cites the chat message and the actor cites the person', async () => {
    expect(review.changes.map((one) => one.id).sort()).toEqual(['actor:manager', rule?.id].sort());
    expect(rule?.sources[0].used).toEqual([{ kind: 'message', id: draft.messageId }]);
    expect(review.changes.find((one) => one.id === 'actor:manager')?.sources[0].agent.kind).toBe('user');
  });

  const revisions = Object.fromEntries(Object.entries(review.drafts).map(([piece, one]) => [piece, one.revision]));
  const decided = await actor.api.post<Review>(`${base}/drafts/review/decisions/`, {
    expected_revisions: revisions,
    decisions: [{ id: rule?.id, decision: 'accepted' }, { id: 'actor:manager', decision: 'rejected' }],
  });

  await test.step('api: a reviewed Save without candidates needs Validate first', async () => {
    const drafts = await actor.api.get<Drafts>(`${base}/drafts/`);
    const refused = await actor.api.request<{ error: string }>('post', `${base}/drafts/save/`, { data: {
      drafts: [{ piece: 'environment', expected_parent_id: drafts.environment?.parent_id,
                 expected_revision: drafts.environment?.revision }],
      idempotency_key: `plain-${environment.environmentId}`,
      expected_review_digest: decided.review_digest,
    } });
    expect([refused.status, refused.body.error]).toEqual([409, 'validation_required']);
    expect(await defaultVersion()).toBe(environment.environmentVersionId);
  });

  const validating = await actor.api.request<Validated>('post', `${base}/drafts/validate/`, { data: {
    expected_revisions: revisions, expected_review_digest: decided.review_digest,
  } });
  const validated = validating.body;
  await testInfo.attach('candidates', { contentType: 'application/json', body: JSON.stringify(validated.candidates) });
  await test.step('api: Validate builds candidates and leaves the default where it was', async () => {
    expect([validating.status, validated.validation.state]).toEqual([202, 'validating']);
    expect(validated.candidates.environment_version_id).not.toBe(environment.environmentVersionId);
    expect(await defaultVersion()).toBe(environment.environmentVersionId);
  });

  certifyCandidates({ environmentId: environment.environmentId });
  const polled = await actor.api.get<Drafts>(`${base}/drafts/`);
  await test.step('api: the drafts read reports the pairing ready, with the candidates as Save takes them', async () => {
    expect(polled.validation?.state).toBe('ready');
    expect(polled.candidates).toEqual(validated.candidates);
  });

  const name = `Refund cap ${environment.environmentId.slice(0, 8)}`;
  const saved = await actor.api.post<Saved>(`${base}/drafts/save/`, {
    candidates: polled.candidates, idempotency_key: `save-${environment.environmentId}`,
    expected_review_digest: decided.review_digest, name,
  });

  await test.step('pg: the saved default holds only the accepted rule, and no draft is left open', async () => {
    expect(saved.saved.environment.id).toBe(validated.candidates.environment_version_id);
    expect(await defaultVersion()).toBe(validated.candidates.environment_version_id);
    const [version] = await probe.pg<{ status: string; name: string; rules: string[] | null; amendments: string[] | null; actors: string[] | null }>(
      `SELECT status, name, documents->'contract'->'hard_constraints' AS rules,
              documents->'contract'->'amendments' AS amendments,
              (SELECT array_agg(k) FROM jsonb_object_keys(documents->'actor_digests') k) AS actors
         FROM simulate_harness_environment_version WHERE id = $1`,
      [validated.candidates.environment_version_id],
    );
    expect(version.status).toBe('active');
    expect(version.name).toBe(name);
    expect(version.rules).toEqual([draft.rule]);
    expect(version.amendments).toEqual([draft.amendment]);
    expect(version.actors ?? []).not.toContain('manager');
    const open = await probe.pg<{ n: string }>(
      "SELECT count(*)::text AS n FROM simulate_harness_environment_version WHERE job_id = $1 AND status = 'draft'",
      [environment.environmentId],
    );
    expect(Number(open[0].n)).toBe(0);
  });
});
