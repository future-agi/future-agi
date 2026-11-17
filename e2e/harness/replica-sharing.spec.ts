import { request } from '@playwright/test';
import { test, expect } from '../lib/walk/fixtures';
import { E2E } from '../lib/env';
import { GatewayAdmin } from '../lib/gateway-admin';
import { LedgerSeeder, topupFixture, syntheticSession, FIXTURE_NOTE } from '../lib/ledger-seed';
import { RedisProbe } from '../lib/redis-probe';
import { POLL } from '../lib/state-probe';

// useManagedModels.js / serializers/contracts.py: real org-config enablement.
const UPDATE_CONFIG = '/agentcc/gateways/default/update-config/';
// ManagedModelsCard.jsx at 3c20e189ea: enabled-state switch only, no OFF opt-in.
const CREDITS_CONTROL_NAME = 'Future AGI credits';
// README Writing a flow: each browser first-paint observation gets 60 seconds.
const UI_READY = 60_000;

test('replica live: real mock traffic spends shared Redis credits on both gateways', async ({ scopeActors, walk }, testInfo) => {
  test.setTimeout(240_000);
  test.skip(process.env.E2E_GATEWAY_REPLICAS !== '2', 'requires the H8 two-replica overlay');
  expect(walk.inspection().error).toBeUndefined();
  const owner = scopeActors.ownerA;
  const a = await GatewayAdmin.open();
  const b = await GatewayAdmin.open(undefined, process.env.E2E_GATEWAY_REPLICA_URL ?? 'http://localhost:28091');
  const seeder = await LedgerSeeder.open();
  const http = await request.newContext();
  const redis = new RedisProbe([owner.organizationId], undefined, '');
  try {
    expect((await a.credits(owner.organizationId)).ledger).toBe('redis');
    expect((await b.credits(owner.organizationId)).ledger).toBe('redis');
    const minted = (await owner.api.post<{ result: { key: string; gateway_key_id: string } }>('/agentcc/api-keys/', { name: 'e2e-shared-redis' })).result;
    await testInfo.attach('replica-key', { contentType: 'application/json',
      body: JSON.stringify({ organizationId: owner.organizationId, gatewayKeyId: minted.gateway_key_id }) });
    // Fixture setup, not a console opt-in journey. Keep the actual shared Redis
    // traffic and per-replica config/key convergence assertions below unchanged.
    const enabled = await owner.api.post<{ result: { version: number } }>(UPDATE_CONFIG, { managed_models: { enabled: true } });
    expect(enabled.result.version).toBeGreaterThan(0);
    await testInfo.attach('replica-enabled-config', { contentType: 'application/json',
      body: JSON.stringify({ organizationId: owner.organizationId, version: enabled.result.version, enabled: true }) });
    const context = await walk.open(owner, 'replica-owner');
    const page = await context.newPage();
    await page.goto('/dashboard/gateway/models');
    await expect(page.getByLabel(CREDITS_CONTROL_NAME, { exact: true })).toBeChecked({ timeout: UI_READY });
    const grant = await seeder.grant(owner.organizationId, '1', 'replica-sharing');
    expect([200, 201]).toContain(grant.status);
    const session = syntheticSession(`replica-${owner.organizationId}`);
    const topup = topupFixture(owner.organizationId, session, '1');
    expect(topup.note).toContain(FIXTURE_NOTE);
    expect(topupFixture(owner.organizationId, session, '1').created).toBe(false);
    walk.receipt.extra.topupFixture = { note: topup.note, created: topup.created };
    // Explicit sync bound: do not mislabel this as a push to both replicas.
    await expect.poll(async () => (await b.key(minted.gateway_key_id)).status, POLL.GATEWAY_SYNC).toBe(200);
    await expect.poll(async () => (await b.orgConfig(owner.organizationId))?.managed_models?.enabled, POLL.GATEWAY_SYNC).toBe(true);
    await expect.poll(async () => (await b.credits(owner.organizationId)).granted_total_micros, POLL.GATEWAY_SYNC).toBe(2_000_000);
    const call = async (base: string) => {
      const response = await http.post(`${base}/v1/chat/completions`, { headers: { Authorization: `Bearer ${minted.key}` },
        data: { model: 'openai/gpt-4o', messages: [{ role: 'user', content: 'synthetic replica sharing proof' }] } });
      expect(response.status()).toBe(200);
    };
    const before = (await a.credits(owner.organizationId)).spent_micros;
    await call(E2E.gatewayUrl);
    const afterA = await b.credits(owner.organizationId);
    expect(afterA.spent_micros).toBeGreaterThan(before);
    await call(process.env.E2E_GATEWAY_REPLICA_URL ?? 'http://localhost:28091');
    const afterB = await a.credits(owner.organizationId);
    expect(afterB.spent_micros).toBeGreaterThan(afterA.spent_micros);
    const account = redis.snapshot(owner.organizationId).find(k => k.key === `mc:{${owner.organizationId}}:acct`);
    walk.receipt.extra.sharedRedis = { ledger: afterB.ledger, before, afterA: afterA.spent_micros,
      afterB: afterB.spent_micros, accountKeyObserved: !!account, reads: redis.log.length };
    expect(account?.kind).toBe('hash');
    expect(account).toBeDefined();
    walk.receipt.notes.push('Django pushes to the primary only. Replica key/config convergence is bounded by POLL.GATEWAY_SYNC; fan-out remains owed.');
  } finally { await a.dispose(); await b.dispose(); await seeder.dispose(); await http.dispose(); }
});
