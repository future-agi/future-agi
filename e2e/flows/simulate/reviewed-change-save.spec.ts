import { test, expect } from '../../lib/fixtures';
import { ApiError } from '../../lib/api-client';
import { flowAnnotation } from '../../lib/flow-meta';
import { certifyCandidates, seedReviewEnvironment } from '../../lib/simulate-seed';

// Routes and bodies pinned from futureagi/simulate/views/harness_environment.py and the serializers in
// futureagi/simulate/serializers/harness_environment.py (HarnessReviewDecisionsSerializer,
// HarnessDraftValidateSerializer, HarnessDraftSaveSerializer, HarnessDraftsSerializer).
const ENVIRONMENTS = '/simulate/api/harness-environments';
// An actor the person adds directly; its fields follow the actor record schema the environment seals
// (futureagi/simulate/tests/test_harness_actors.py MANAGER).
const MANAGER = {
  id: 'manager',
  name: 'Priya Raman',
  role: 'sales manager who approves discounts above 10%',
  goal: 'Approve only discounts she can justify',
  permitted_actions: ['crm.approve_discount'],
};

interface Source { agent: { kind: string }; used?: { kind: string; id: string }[] }
interface Change { id: string; kind: string; decision: string | null; sources: Source[] }
interface Review { drafts: Record<string, { revision: number; parent_id: string }>; changes: Change[]; review_digest: string }
interface Candidates { environment_version_id: string; suite_version_id: string }
interface Validation { state: string; job_id?: string | null }
interface Drafts { environment: { revision: number; parent_id: string } | null; validation: Validation | null; candidates: Candidates | null }
interface Validated { validation: Validation; candidates: Candidates }
interface Saved { saved: Record<string, { id: string; name: string | null; status: string }> }

test('SIM-E2E-003: a reviewed draft saves only the accepted change, and only through Validate', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-003', area: 'simulate',
    userGoal: 'A builder reviews a chat-proposed rule and their own actor edit, keeps the rule, rejects the actor, validates and saves a named version holding only the rule',
    steps: ['seed a versioned environment whose draft holds a rule the builder chat added in answer to a message (e2e/lib/simulate-seed.ts; no hosted build runs in this harness)',
            'add a Manager actor through the actors route',
            'read the review: the rule cited to the message, the actor to the person',
            'accept the rule and reject the actor',
            'try a plain Save with the review digest',
            'Validate',
            'stand in for the validation job by certifying the candidate pairing (no sandbox in this harness)',
            'poll drafts until validation is ready, then Save the candidates with a name'],
    backendChecks: ['a reviewed Save without candidates is refused 409 validation_required, and the defaults do not move',
                    'Validate answers 202 with candidates the drafts read returns in the same shape, while the environment default stays put',
                    'after Save the environment default is the candidate version, named, holding the rule and its amendment line and not the actor',
                    'the draft is set aside, discarded, once saved'],
  }),
}, async ({ actor, probe }, testInfo) => {
  // Seeding runs two docker exec scripts; everything else is synchronous API calls.
  test.setTimeout(120_000);
  const seed = seedReviewEnvironment({
    organizationId: actor.organizationId, workspaceId: actor.workspaceId, userEmail: actor.email,
  });
  await testInfo.attach('seed', { contentType: 'application/json', body: JSON.stringify(seed) });
  const base = `${ENVIRONMENTS}/${seed.environmentId}`;
  const defaultVersion = async () => (await probe.pg<{ environment_version_id: string }>(
    'SELECT environment_version_id::text FROM simulate_hosted_harness_job WHERE id = $1', [seed.environmentId],
  ))[0].environment_version_id;
  const before = await defaultVersion();

  let review = await actor.api.get<Review>(`${base}/drafts/review/`);
  await actor.api.post(`${base}/actors/`, { actor: MANAGER, expected_revision: review.drafts.environment.revision });

  review = await actor.api.get<Review>(`${base}/drafts/review/`);
  const rule = review.changes.find((one) => one.kind === 'rule');
  await test.step('api: the rule cites the chat message and the actor cites the person', async () => {
    expect(review.changes.map((one) => one.id).sort()).toEqual(['actor:manager', rule?.id].sort());
    expect(rule?.sources[0].used).toEqual([{ kind: 'message', id: seed.messageId }]);
    expect(review.changes.find((one) => one.id === 'actor:manager')?.sources[0].agent.kind).toBe('user');
  });

  const revisions = Object.fromEntries(Object.entries(review.drafts).map(([piece, one]) => [piece, one.revision]));
  const decided = await actor.api.post<Review>(`${base}/drafts/review/decisions/`, {
    expected_revisions: revisions,
    decisions: [{ id: rule?.id, decision: 'accepted' }, { id: 'actor:manager', decision: 'rejected' }],
  });

  await test.step('api: a reviewed Save without candidates needs Validate first', async () => {
    const drafts = await actor.api.get<Drafts>(`${base}/drafts/`);
    const refused = await actor.api.post(`${base}/drafts/save/`, {
      drafts: [{ piece: 'environment', expected_parent_id: drafts.environment?.parent_id,
                 expected_revision: drafts.environment?.revision }],
      idempotency_key: `plain-${seed.environmentId}`,
      expected_review_digest: decided.review_digest,
    }).then(() => null, (error: unknown) => error);
    expect(refused).toBeInstanceOf(ApiError);
    expect((refused as ApiError).status).toBe(409);
    expect(((refused as ApiError).body as { error: string }).error).toBe('validation_required');
    expect(await defaultVersion()).toBe(before);
  });

  const validated = await actor.api.post<Validated>(`${base}/drafts/validate/`, {
    expected_revisions: revisions, expected_review_digest: decided.review_digest,
  });
  await testInfo.attach('candidates', { contentType: 'application/json', body: JSON.stringify(validated.candidates) });
  await test.step('api: Validate builds candidates and leaves the default where it was', async () => {
    expect(validated.validation.state).toBe('validating');
    expect(validated.candidates.environment_version_id).not.toBe(before);
    expect(await defaultVersion()).toBe(before);
  });

  certifyCandidates({ environmentId: seed.environmentId });
  const polled = await actor.api.get<Drafts>(`${base}/drafts/`);
  await test.step('api: the drafts read reports the pairing ready, with the candidates as Save takes them', async () => {
    expect(polled.validation?.state).toBe('ready');
    expect(polled.candidates).toEqual(validated.candidates);
  });

  const name = `Discount cap ${seed.environmentId.slice(0, 8)}`;
  const saved = await actor.api.post<Saved>(`${base}/drafts/save/`, {
    candidates: polled.candidates, idempotency_key: `save-${seed.environmentId}`,
    expected_review_digest: decided.review_digest, name,
  });

  await test.step('pg: the saved default holds only the accepted rule, and the draft is set aside', async () => {
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
    expect(version.rules).toEqual([seed.rule]);
    expect(version.amendments).toEqual([seed.amendment]);
    expect(version.actors ?? []).not.toContain('manager');
    const open = await probe.pg<{ n: string }>(
      "SELECT count(*)::text AS n FROM simulate_harness_environment_version WHERE job_id = $1 AND status = 'draft'",
      [seed.environmentId],
    );
    expect(Number(open[0].n)).toBe(0);
  });
});
