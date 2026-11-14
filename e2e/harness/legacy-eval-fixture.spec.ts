import { randomUUID } from 'node:crypto';
import { test, expect } from '../lib/scope-actors';
import { E2E } from '../lib/env';

// Test-image-only route: stack/backend/urls_e2e.py. No route in tfc/urls.py.
const FIXTURE = '/sdk/e2e/legacy-eval/';
const CREATE = '/model-hub/eval-templates/create-v2/';

test('legacy evaluator fixture requires workspace write access and only updates the caller\'s own e2e templates',
  async ({ scopeActors: scopes, scopeProbe: probe, request }, testInfo) => {
    test.setTimeout(240_000);
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const ids: Record<string, string> = {};
    const sibling = scopes.withWorkspace(scopes.ownerA, scopes.emptyWorkspace.id);
    for (const [label, actor, name] of [
      ['own', scopes.ownerA, `e2e-legacy-${suffix}`],
      ['other', scopes.ownerB, `e2e-legacy-other-${suffix}`],
      ['sibling', sibling, `e2e-legacy-sibling-${suffix}`],
      ['ordinary', scopes.ownerA, `ordinary-${suffix}`],
    ] as const) {
      const result = await actor.api.post<{ result: { id: string } }>(CREATE, {
        name, instructions: 'Classify {{output}}.', eval_type: 'llm', model: 'turing_large',
        output_type: 'pass_fail', pass_threshold: 0.5,
      });
      ids[label] = result.result.id;
    }
    await testInfo.attach('seeded-evaluator-ids', { contentType: 'application/json', body: JSON.stringify(ids) });
    const anon = await request.post(`${E2E.apiUrl}${FIXTURE}`, { data: { template_id: ids.own } });
    expect(anon.status()).toBe(401);
    const viewer = await scopes.send(scopes.viewer, 'POST', FIXTURE, { template_id: ids.own });
    expect(viewer.status).toBe(403);
    const missing = await scopes.send(scopes.ownerA, 'POST', FIXTURE, { template_id: randomUUID() });
    expect(missing.status).toBe(404);
    for (const label of ['other', 'sibling', 'ordinary']) {
      const denied = await scopes.send(scopes.ownerA, 'POST', FIXTURE, { template_id: ids[label] });
      expect(denied).toEqual(missing);
    }
    const malformed = await scopes.send(scopes.ownerA, 'POST', FIXTURE, { template_id: 'not-a-uuid' });
    expect(malformed.status).toBe(400);
    const extraField = await scopes.send(scopes.ownerA, 'POST', FIXTURE,
      { template_id: ids.own, organization_id: scopes.ownerB.organizationId });
    expect(extraField.status).toBe(400);
    const untouched = await probe.pg<{ id: string; eval_id: number }>(
      'SELECT id,eval_id FROM model_hub_evaltemplate WHERE id=ANY($1::uuid[]) ORDER BY id', [Object.values(ids)]);
    expect(untouched.map(row => row.eval_id)).toEqual([0, 0, 0, 0]);
    const own = await scopes.send<{ id: string; eval_id: number }>(scopes.ownerA, 'POST', FIXTURE, { template_id: ids.own });
    const other = await scopes.send<{ id: string; eval_id: number }>(scopes.ownerB, 'POST', FIXTURE, { template_id: ids.other });
    expect(own.status).toBe(200);
    expect(other.status).toBe(200);
    expect(own.body.id).toBe(ids.own);
    expect(other.body.id).toBe(ids.other);
    expect(own.body.eval_id).toBeGreaterThan(1000000000);
    expect(other.body.eval_id).toBeGreaterThan(own.body.eval_id);
    const stored = await probe.pg<{ id: string; eval_id: number }>(
      'SELECT id,eval_id FROM model_hub_evaltemplate WHERE id=ANY($1::uuid[]) ORDER BY id', [[ids.own, ids.other]]);
    expect(stored).toEqual([own.body, other.body].sort((a, b) => a.id.localeCompare(b.id)));
  });
