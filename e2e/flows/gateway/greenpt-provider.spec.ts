import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// AddProviderDialog.jsx and providerPresetData.js own these labels and defaults.
const UI_READY = 60_000;
const GREENPT_URL = 'https://api.greenpt.ai/v1';
// useGatewayConfig.js uses these endpoints for discovery, saving and reading.
const FETCH_MODELS = '**/agentcc/provider-credentials/fetch_models/';
const CONFIG_PATH = '/agentcc/gateways/default/config/';
const SAVE_PATH = '/agentcc/gateways/default/update-provider/';

interface GatewayConfig {
  result: {
    providers: Record<string, {
      base_url: string;
      api_format: string;
      api_path_prefix: string;
      models: string[];
    }>;
  };
}

test('GW-E2E-021: GreenPT preserves its preset and an explicit model list', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-021',
    area: 'gateway',
    userGoal: 'Configure GreenPT with an explicit model list when discovery is unavailable and reopen the saved provider',
    steps: [
      'open Gateway provider configuration and select GreenPT',
      'check the preset URL and API path prefix',
      'make discovery unavailable in the browser and enter a synthetic key and unique model ID',
      'save through the real provider endpoint',
      'reload and reopen the saved GreenPT provider',
    ],
    backendChecks: [
      'the gateway config API retains GreenPT with its preset URL, openai format, /v1 prefix and exact explicit model list',
      'the reopened provider dialog retains the saved URL, prefix and unique model ID',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // Eleven UI_READY waits (660s), persistence API calls and navigation/headroom (60s).
  test.setTimeout(720_000);
  const model = `e2e-greenpt-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  await testInfo.attach('provider-identity', {
    body: JSON.stringify({ organization: actor.organizationId, provider: 'greenpt', model }),
    contentType: 'application/json',
  });
  // Exercise the supported manual-model path without sending a synthetic key to a vendor.
  // Saving and config readback still use the real backend; no successful response is mocked.
  await page.route(FETCH_MODELS, (route) => route.abort('failed'));
  await page.goto('/dashboard/gateway/providers/config');
  await page.getByRole('button', { name: 'Add Provider', exact: true }).click({ timeout: UI_READY });
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel(/^Provider/).click();
  await page.getByRole('option', { name: 'GreenPT', exact: true }).click({ timeout: UI_READY });
  await expect(dialog.getByLabel('Base URL')).toHaveValue(GREENPT_URL, { timeout: UI_READY });
  await expect(dialog.getByLabel('API Path Prefix')).toHaveValue('/v1', { timeout: UI_READY });
  await dialog.getByLabel(/API Key/).fill(`synthetic-${model}`);
  await dialog.getByPlaceholder(/type manually/i).fill(model);
  await dialog.getByPlaceholder(/type manually/i).press('Enter');
  const save = dialog.getByRole('button', { name: 'Add Provider', exact: true });
  await expect(save).toBeEnabled({ timeout: UI_READY });
  const saved = page.waitForResponse(
    response => response.url().includes(SAVE_PATH) && response.request().method() === 'POST',
    { timeout: UI_READY },
  );
  await save.click();
  expect((await saved).status()).toBe(200);
  await expect(dialog).not.toBeVisible({ timeout: UI_READY });

  const config = await actor.api.get<GatewayConfig>(CONFIG_PATH);
  expect(config.result.providers.greenpt).toMatchObject({
    base_url: GREENPT_URL,
    api_format: 'openai',
    api_path_prefix: '/v1',
    models: [model],
  });
  await page.reload();
  // ProviderConfigView.jsx renders the provider name in a panel with an Edit provider button.
  const card = page.locator('div').filter({ hasText: 'greenpt' })
    .filter({ has: page.getByTitle('Edit provider') }).last();
  await card.getByTitle('Edit provider').click({ timeout: UI_READY });
  await expect(dialog.getByLabel('Base URL')).toHaveValue(GREENPT_URL, { timeout: UI_READY });
  await expect(dialog.getByLabel('API Path Prefix')).toHaveValue('/v1', { timeout: UI_READY });
  await expect(dialog.getByText(model, { exact: true })).toBeVisible({ timeout: UI_READY });
});
