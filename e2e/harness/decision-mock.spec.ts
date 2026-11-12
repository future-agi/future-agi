import { test, expect } from '@playwright/test';
import { E2E } from '../lib/env';
import { inspectManagedMock, MOCK_KEY } from '../lib/managed-mock';

// e2e/stack/mock-llm/server.mjs pins these synthetic-only credential names and
// marker-driven replies. This proves transport, not real-model accuracy.
const USAGE = { input_tokens: 37, output_tokens: 0 };
const ROUTES = [
  { model: 'openai/gpt-6-luna', connection: 'openai', native: true, actual: 'gpt-6-luna' },
  { model: 'openrouter/openai/gpt-6-luna-decisions', connection: 'openrouter', native: false, actual: 'openai/gpt-6-luna-decisions' },
  { model: 'typesafe/jev-1.14.0', connection: 'typesafe', native: false, actual: 'jev-1.14.0' },
  { model: 'perplexity/pplx-decider-v1.2', connection: 'perplexity', native: false, actual: 'pplx-decider-v1.2' },
] as const;

for (const route of ROUTES) {
  test(`managed typed decision mock preserves ${route.connection} routing and never falls back`, async ({ request }, testInfo) => {
    test.setTimeout(120_000);
    const receipt = inspectManagedMock();
    await testInfo.attach('verified-managed-mock', { contentType: 'application/json', body: JSON.stringify(receipt) });
    const url = `${E2E.gatewayUrl}${route.native ? '/v1/decisions' : '/v1/systemone'}`;
    for (const outcome of ['pass', 'fail', 'refuse'] as const) {
      const input = `e2e-decision:${outcome} ${testInfo.workerIndex}`;
      const response = await request.post(url, {
        headers: { Authorization: `Bearer ${MOCK_KEY}`, 'x-agentcc-provider-key': `e2e-decision-${route.connection}-key` },
        data: route.native ? {
          model: route.model, input, questions: [{ name: 'q', type: 'predicate', instructions: 'Synthetic fixture only' }],
        } : {
          model: route.model, state: input, questions: { q: { type: 'noul', instructions: 'Synthetic fixture only' } },
        },
      });
      expect(response.status()).toBe(200);
      const answer = outcome === 'refuse' ? { type: 'refusal' } : route.native
        ? { type: 'predicate', probability: outcome === 'pass' ? 0.9 : 0.1 }
        : { type: 'noul', noul: outcome === 'pass' ? 0.9 : 0.1 };
      expect(await response.json()).toEqual({ model: route.actual, usage: USAGE,
        answers: route.native ? [{ ...answer, name: 'q' }] : { q: answer } });
      expect(response.headers()['x-agentcc-provider']).toBe(route.connection);
    }
    // A nonmatching synthetic credential must not become an operator-key call.
    const refused = await request.post(url, {
      headers: { Authorization: `Bearer ${MOCK_KEY}`, 'x-agentcc-provider-key': 'e2e-wrong-connection-key' },
      data: route.native ? { model: route.model, input: 'e2e-decision:pass', questions: [{ name: 'q', type: 'predicate' }] }
        : { model: route.model, state: 'e2e-decision:pass', questions: { q: { type: 'noul' } } },
    });
    expect(refused.status()).toBe(403);
    expect((await refused.json()).error.code).toBe('provider_key_rejected');
  });
}
