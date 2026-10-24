import { request } from '@playwright/test';
import { test, expect } from '../lib/fixtures';
import { ApiError } from '../lib/api-client';
import { E2E } from '../lib/env';
import { GatewayAdmin } from '../lib/gateway-admin';
import { POLL } from '../lib/state-probe';
import { mintScope } from '../lib/sinks';
import { MockUpstream } from '../lib/upstream';

// MockUpstream's live test (its offline half is scripts/mock-llm.test.mjs). An org key
// reaches only its org's providers; this proves an org provider whose base URL is
// the mock on e2e-public is saved by Django and called by the gateway with both
// SSRF guards at their defaults (AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS=false, pinned
// by the managed-mock inspector), and that the mock records the call. The positive
// control proves the guard itself is intact.

const upstream = new MockUpstream();
// agentcc/services/url_safety.py PROVIDER_PRIVATE_URL_ERROR (its first sentence).
const PROVIDER_PRIVATE_URL_ERROR = 'Base URL points to a private network address, which is refused by default.';
// Django's push on save reaching the gateway's copy (org-config-secrets.spec.ts PUSH_VISIBLE).
const PUSH_VISIBLE = POLL.ASYNC_JOB;

type Created<T> = { result: T };

test('mock upstream live: an org key calls an org provider on the public-classified mock through the gateway', async ({ actor }, testInfo) => {
  // provisioning (worker fixture) + seeding + PUSH_VISIBLE 60 + one call + headroom.
  test.setTimeout(150_000);
  const scenario = mintScope('upstream');
  const providerName = `e2e-upstream-${scenario}`;
  const model = `e2e-upstream-model-${scenario}`;
  const providerKey = `sk-e2e-upstream-${scenario}`;
  // AgentccProviderCredentialCreateSerializer (GW-E2E-002's wire).
  const provider = await actor.api.post<Created<{ id: string }>>('/agentcc/provider-credentials/', {
    provider_name: providerName, credentials: { api_key: providerKey }, base_url: upstream.baseUrl(scenario),
    api_format: 'openai', models_list: [model], extra_config: { api_path_prefix: '/v1' },
  });
  const key = await actor.api.post<Created<{ key: string; gateway_key_id: string }>>('/agentcc/api-keys/',
    { name: `e2e-upstream-${scenario}` });
  await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({ scenario,
    organizationId: actor.organizationId, providerId: provider.result.id, gatewayKeyId: key.result.gateway_key_id }) });

  const admin = await GatewayAdmin.open();
  const gw = await request.newContext();
  try {
    await expect.poll(async () => Object.keys((await admin.orgConfig(actor.organizationId))?.providers ?? {}),
      PUSH_VISIBLE).toContain(providerName);
    const res = await gw.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
      headers: { Authorization: `Bearer ${key.result.key}` },
      data: { model, messages: [{ role: 'user', content: `e2e-upstream-${scenario}` }] },
    });
    const body = await res.json();
    expect(res.status(), JSON.stringify(body)).toBe(200);
    expect(body.choices[0].message.content).toBe(`echo: e2e-upstream-${scenario}`);
    expect(body.usage).toEqual(expect.objectContaining({ prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 }));
    const seen = await upstream.requests(scenario);
    expect(seen.map((r) => ({ method: r.method, path: r.path, authorization: r.headers.authorization,
      content: (r.body as { messages: { content: string }[] }).messages.at(-1)?.content }))).toEqual([{
      method: 'POST', path: `/s/${scenario}/-/v1/chat/completions`, authorization: `Bearer ${providerKey}`,
      content: `e2e-upstream-${scenario}` }]);
  } finally {
    await admin.dispose();
    await gw.dispose();
  }
});

test('mock upstream live: POSITIVE CONTROL, an org provider on the private compose network is refused', async ({ actor }) => {
  const scenario = mintScope('upstream-pc');
  const error = await actor.api.post('/agentcc/provider-credentials/', {
    provider_name: `e2e-upstream-${scenario}`, credentials: { api_key: `sk-e2e-upstream-${scenario}` },
    base_url: 'http://mock-llm:8080', api_format: 'openai', models_list: [`e2e-upstream-model-${scenario}`],
    extra_config: { api_path_prefix: '/v1' },
  }).catch((e: unknown) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect((error as ApiError).status).toBe(400);
  expect(JSON.stringify((error as ApiError).body)).toContain(PROVIDER_PRIVATE_URL_ERROR);
});
