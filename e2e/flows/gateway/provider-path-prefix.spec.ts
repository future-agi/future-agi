import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Root docker-compose.yml AGENTCC_ADMIN_TOKEN default. This flow talks to the
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

test('GW-E2E-001: custom provider keeps an explicitly empty API path prefix', {
  tag: ['@flow', '@smoke'],
  annotation: flowAnnotation({
    id: 'GW-E2E-001',
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
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const providerName = `e2e-prefix-${suffix}`;
  const modelName = `e2e-model-${suffix}`;

  // Pinned from AgentccProviderCredentialCreateSerializer. Seed through the
  // credential API so model discovery never needs a live external provider.
  const created = await actor.api.post<ProviderCredentialResponse>(
    '/agentcc/provider-credentials/',
    {
      provider_name: providerName,
      credentials: { api_key: `e2e-key-${suffix}` },
      base_url: 'https://api.perplexity.ai',
      api_format: 'openai',
      models_list: [modelName],
      extra_config: { api_path_prefix: '/v1' },
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
  await expect(prefixInput).toHaveValue('/v1', { timeout: UI_READY });
  await prefixInput.fill('');

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
