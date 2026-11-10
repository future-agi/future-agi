import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from agentcc/serializers/org_config.py and views/org_config.py.
const CONFIG_PATH = '/agentcc/org-configs/';
const ACTIVE_PATH = '/agentcc/org-configs/active/';
// OrgConfigSection / OrgConfigEditor / GuardrailConfigTab use these controls.
const SETTINGS_PATH = '/dashboard/gateway/settings';
// agentcc-gateway/internal/server exposes this org-scoped admin snapshot.
const GATEWAY_CONFIG_PATH = '/-/orgs/configs';
const UI_READY = 60_000;

interface OrgConfig {
  id: string;
  version: number;
  is_active: boolean;
  guardrails: {
    timeout_ms: number;
    checks: Record<string, { config: { words: string[] } }>;
  };
}
interface SaveResponse {
  result: OrgConfig & { gateway_synced: boolean };
}
interface GatewayConfigs {
  data: Record<string, { guardrails: Record<string, unknown> }>;
}

test('GW-E2E-022: an edited guardrail saves and activates from Org Config', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-022',
    area: 'gateway',
    userGoal: 'A user edits an existing guardrail in Gateway Settings, saves and activates it, and sees the saved value after reopening',
    steps: [
      'seed a legacy keyword guardrail with a unique blocked word through the Org Config API',
      'open Gateway Settings and edit Org Config on the Guardrails tab',
      'change the timeout from 1000 to 1500 milliseconds and Save & Activate',
      'reopen the editor and see the saved timeout',
    ],
    backendChecks: [
      'the save creates the next active config version and the active-config API retains the unique keyword and edited timeout',
      'the real Go gateway receives that same keyword rule and timeout with only contract fields',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  test.setTimeout(300_000);
  // Four UI_READY waits (240s) plus seeding, navigation and API reads (60s).
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const word = `e2e-guardrail-${suffix}`;
  // Legacy rules are accepted by the serializer; editing produces checks plus
  // editor metadata. This is the save path reported in #3271.
  const seed = await actor.api.post<SaveResponse>(CONFIG_PATH, {
    change_description: word,
    guardrails: {
      enabled: true,
      timeout_ms: 1000,
      fail_open: false,
      pipeline_mode: 'parallel',
      rules: [{
        name: 'keyword-blocklist', enabled: true, action: 'block',
        threshold: 0.8, mode: 'sync', stage: 'pre', config: { words: [word] },
      }],
    },
  });
  await testInfo.attach('seeded-org-config', {
    body: JSON.stringify({
      organizationId: actor.organizationId, configId: seed.result.id,
      version: seed.result.version, word,
    }),
    contentType: 'application/json',
  });
  expect(seed.result.gateway_synced).toBe(true);

  await page.goto(SETTINGS_PATH, { waitUntil: 'domcontentloaded' });
  await page.getByRole('tab', { name: 'Org Config', exact: true }).click();
  await page.getByRole('button', { name: 'Edit Config', exact: true }).click();
  const editor = page.getByRole('dialog').filter({ hasText: 'Edit Organization Config' });
  await editor.getByRole('tab', { name: 'Guardrails', exact: true }).click();
  await expect(editor.getByLabel('Timeout (ms)')).toHaveValue('1000', { timeout: UI_READY });
  await editor.getByLabel('Timeout (ms)').fill('1500');
  const saving = page.waitForResponse(
    r => new URL(r.url()).pathname === CONFIG_PATH && r.request().method() === 'POST',
    { timeout: UI_READY },
  );
  await editor.getByRole('button', { name: 'Save & Activate', exact: true }).click();
  const saved = await saving;
  expect(saved.status()).toBe(200);
  const saveBody = await saved.json() as SaveResponse;
  expect(saveBody.result.gateway_synced).toBe(true);
  expect(saveBody.result.version).toBe(seed.result.version + 1);
  expect(saveBody.result.is_active).toBe(true);
  await expect(editor).not.toBeVisible({ timeout: UI_READY });

  // The same active-config endpoint used by useOrgConfig.js; save is synchronous.
  const active = await actor.api.get<{ result: OrgConfig }>(ACTIVE_PATH);
  expect(active.result.id).toBe(saveBody.result.id);
  expect(active.result.version).toBe(seed.result.version + 1);
  expect(active.result.is_active).toBe(true);
  expect(active.result.guardrails.timeout_ms).toBe(1500);
  expect(Object.keys(active.result.guardrails.checks)).toEqual(['keyword-blocklist']);
  expect(active.result.guardrails.checks['keyword-blocklist'].config.words).toEqual([word]);
  await testInfo.attach('activated-org-config', {
    body: JSON.stringify({ configId: active.result.id, version: active.result.version }),
    contentType: 'application/json',
  });

  // Same synthetic compose default as GW-E2E-002; attach mode can override it.
  const adminToken = process.env.AGENTCC_ADMIN_TOKEN || 'local-dev-only-admin-token-replace-me';
  const gateway = await request.newContext({
    baseURL: E2E.gatewayUrl,
    extraHTTPHeaders: { Authorization: `Bearer ${adminToken}` },
  });
  try {
    const response = await gateway.get(GATEWAY_CONFIG_PATH);
    expect(response.status()).toBe(200);
    const body = await response.json() as GatewayConfigs;
    // tenant.GuardrailConfig omits false fail_open; exact equality also rejects
    // leaked rules, provider, stage, mode and internal policy metadata.
    expect(body.data[actor.organizationId].guardrails).toEqual({
      timeout_ms: 1500,
      pipeline_mode: 'parallel',
      checks: {
        'keyword-blocklist': {
          enabled: true, action: 'block', confidence_threshold: 0.8,
          config: { words: [word] },
        },
      },
    });
  } finally {
    await gateway.dispose();
  }

  await page.getByRole('button', { name: 'Edit Config', exact: true }).click();
  await editor.getByRole('tab', { name: 'Guardrails', exact: true }).click();
  await expect(editor.getByLabel('Timeout (ms)')).toHaveValue('1500', { timeout: UI_READY });
});
