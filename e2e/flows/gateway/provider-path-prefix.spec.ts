import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// docker-compose.distributed.yml AGENTCC_ADMIN_TOKEN default. This flow talks to the
// managed E2E gateway's admin read endpoint to prove Django pushed the value.
const GATEWAY_ADMIN_TOKEN = 'local-dev-only-admin-token-replace-me';
const UI_READY = 60_000;

interface ProviderCredentialResponse {
  result: {
    id: string;
    extra_config: Record<string, unknown>;
  };
}

interface GatewayConfigResponse {
  result: {
    providers: Record<string, { api_path_prefix?: string | null }>;
  };
}

interface GatewayOrgConfigList {
  data: Record<string, {
    providers?: Record<string, { api_path_prefix?: string | null }>;
  }>;
}

test('GW-E2E-002: custom provider keeps an explicitly empty API path prefix', {
  tag: ['@flow', '@smoke'],
  annotation: flowAnnotation({
    id: 'GW-E2E-002',
    area: 'gateway',
    userGoal: 'A user configures a custom OpenAI-compatible provider without an API version prefix',
    steps: [
      'seed a custom provider for the current organization',
      'open Gateway provider configuration',
      'edit the provider and clear its API path prefix',
      'save and reopen the provider',
      'see that the empty prefix was preserved',
    ],
    backendChecks: [
      'the provider credential API returns api_path_prefix as an explicit empty string',
      'the Django gateway config returns api_path_prefix as an explicit empty string',
      'the organization config pushed to the Go gateway retains api_path_prefix as an explicit empty string',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // Five UI_READY-budgeted waits (60s each), the seeding POST, and the ~15s
  // upstream model fetch below exceed the config's 120s per-test default. The
  // harness precedent is 300s for OBS-E2E-001. Under-budgeting means the outer
  // timeout fires first and hides which assertion actually ran out.
  test.setTimeout(300_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const providerName = `e2e-prefix-${suffix}`;
  const modelName = `e2e-model-${suffix}`;
  const manuallyAddedModel = `e2e-manual-${suffix}`;

  // Pinned from AgentccProviderCredentialCreateSerializer. Seed through the
  // credential API so model discovery never needs a live external provider.
  // The prefix is deliberately not `/v1`: that is also the Django read default
  // (`views/gateway.py`) and the form default (`utils.js`), so the hydration
  // assertion below would pass off a default even if the seed never landed.
  // `base_url` is the one public host under `e2e/flows/`; listing models from it
  // is EXPECTED to fail, and that failure is load-bearing, because the "type
  // manually" placeholder only renders while `modelOptions` is empty.
  const created = await actor.api.post<ProviderCredentialResponse>(
    '/agentcc/provider-credentials/',
    {
      provider_name: providerName,
      credentials: { api_key: `e2e-key-${suffix}` },
      base_url: 'https://api.perplexity.ai',
      api_format: 'openai',
      models_list: [modelName],
      extra_config: { api_path_prefix: '/openai/v1' },
    },
  );
  await testInfo.attach('provider-credential-id', {
    body: created.result.id,
    contentType: 'text/plain',
  });

  await page.goto('/dashboard/gateway/providers/config');
  const providerCard = page.locator('.MuiCard-root').filter({ hasText: providerName });
  await expect(providerCard).toBeVisible({ timeout: UI_READY });
  await providerCard.getByTitle('Edit provider').click();

  const prefixInput = page.getByLabel('API Path Prefix');
  await expect(prefixInput).toHaveValue('/openai/v1', { timeout: UI_READY });
  await prefixInput.fill('');
  await page.getByPlaceholder(/type manually/i).fill(manuallyAddedModel);
  await page.getByPlaceholder(/type manually/i).press('Enter');
  await expect(page.getByRole('button', { name: 'Save Changes' })).toBeEnabled({ timeout: UI_READY });

  const updateResponse = page.waitForResponse(
    (response) => response.url().includes('/agentcc/gateways/default/update-provider/')
      && response.request().method() === 'POST',
    { timeout: UI_READY },
  );
  await page.getByRole('button', { name: 'Save Changes' }).click();
  expect((await updateResponse).status()).toBe(200);

  await providerCard.getByTitle('Edit provider').click();
  await expect(page.getByLabel('API Path Prefix')).toHaveValue('', { timeout: UI_READY });

  const credential = await actor.api.get<ProviderCredentialResponse>(
    `/agentcc/provider-credentials/${created.result.id}/`,
  );
  expect(credential.result.extra_config.api_path_prefix).toBe('');

  const backendConfig = await actor.api.get<GatewayConfigResponse>(
    '/agentcc/gateways/default/config/',
  );
  expect(backendConfig.result.providers[providerName]?.api_path_prefix).toBe('');

  const gatewayRequest = await request.newContext({
    baseURL: E2E.gatewayUrl,
    extraHTTPHeaders: { Authorization: `Bearer ${GATEWAY_ADMIN_TOKEN}` },
  });
  const gatewayResponse = await gatewayRequest.get('/-/orgs/configs');
  expect(gatewayResponse.status()).toBe(200);
  const gatewayConfigs = await gatewayResponse.json() as GatewayOrgConfigList;
  expect(
    gatewayConfigs.data[actor.organizationId]?.providers?.[providerName]?.api_path_prefix,
  ).toBe('');
  await gatewayRequest.dispose();
});
