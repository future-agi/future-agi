import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from AddProviderDialog.jsx and hooks/useGatewayConfig.js.
const PROVIDERS_ROUTE = '/dashboard/gateway/providers/config';
const UPDATE_PATH = '/agentcc/gateways/default/update-provider/';
const CONFIG_PATH = '/agentcc/gateways/default/config/';
// Saving permits unresolved hosts (agentcc/services/url_safety.py). Discovery
// must fail for this reserved domain; manual IDs keep the flow fully synthetic.
const SYNTHETIC_UPSTREAM = 'https://provider-format.invalid';
const UI_READY = 60_000;

interface GatewayConfig {
  result: { providers: Record<string, { api_format: string; models: string[] }> };
}

test('GW-E2E-020: named providers save their native API format', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-020',
    area: 'gateway',
    userGoal: 'A user changes named providers without saving a connection under the previous provider’s protocol',
    steps: [
      'open Add Provider and switch from OpenAI to Anthropic, Google and Azure in turn',
      'see the selected native API format, with no incompatible choices',
      'save each provider with a synthetic endpoint, key and unique manually entered model',
      'reopen each saved provider and see the same API format',
      'switch from Anthropic back to OpenAI, then to Custom and choose a format explicitly',
    ],
    backendChecks: [
      'the gateway config API returns each named provider with its native api_format and the unique model entered in the UI',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // Three 300s provider sequences plus 60s for the reverse/Custom transitions.
  test.setTimeout(960_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  await testInfo.attach('provider-format-organization', {
    body: JSON.stringify({ organizationId: actor.organizationId, suffix }),
    contentType: 'application/json',
  });
  await page.goto(PROVIDERS_ROUTE);
  const dialog = page.getByRole('dialog');
  const format = dialog.getByRole('combobox', { name: /^API Format/ });

  for (const [label, name] of [
    ['Anthropic', 'anthropic'],
    ['Google (Gemini)', 'google'],
    ['Azure OpenAI', 'azure'],
  ]) {
    await test.step(`save and reopen ${name}`, async () => {
      const model = `e2e-format-${name}-${suffix}`;
      await page.getByRole('button', { name: 'Add Provider', exact: true }).click();
      await dialog.getByRole('combobox', { name: /^Provider/ }).click();
      await page.getByRole('option', { name: label, exact: true }).click();
      await expect(format).toHaveText(name, { timeout: UI_READY });
      await expect(format).toBeDisabled({ timeout: UI_READY });
      await dialog.getByLabel(/^Base URL/).fill(SYNTHETIC_UPSTREAM);
      await dialog.getByLabel(/^API Key/).fill(`synthetic-format-${suffix}`);
      // Free-solo model input is also available after a failed native listing.
      const models = dialog.getByPlaceholder(/Fetching models|Select models|type manually|Azure deployment names/);
      await models.fill(model);
      await models.press('Enter');
      const save = dialog.getByRole('button', { name: 'Add Provider', exact: true });
      await expect(save).toBeEnabled({ timeout: UI_READY });
      const saved = page.waitForResponse(response =>
        response.url().includes(UPDATE_PATH) && response.request().method() === 'POST',
      { timeout: UI_READY });
      await save.click();
      expect((await saved).status()).toBe(200);
      await expect(dialog).toBeHidden({ timeout: UI_READY });

      const config = await actor.api.get<GatewayConfig>(CONFIG_PATH);
      expect(config.result.providers[name].api_format).toBe(name);
      expect(config.result.providers[name].models).toEqual([model]);
      await testInfo.attach(`saved-${name}-format`, {
        body: JSON.stringify({ name, api_format: config.result.providers[name].api_format, model }),
        contentType: 'application/json',
      });

      const card = page.locator('div').filter({ hasText: model })
        .filter({ has: page.getByTitle('Edit provider') }).last();
      await card.getByTitle('Edit provider').click();
      await expect(format).toHaveText(name, { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    }, { timeout: 300_000 });
  }

  await page.getByRole('button', { name: 'Add Provider', exact: true }).click();
  for (const [label, expected] of [['Anthropic', 'anthropic'], ['OpenAI', 'openai'], ['Custom / Self-hosted', 'openai']]) {
    await dialog.getByRole('combobox', { name: /^Provider/ }).click();
    await page.getByRole('option', { name: label, exact: true }).click();
    await expect(format).toHaveText(expected, { timeout: UI_READY });
  }
  await format.click();
  await expect(page.getByRole('option')).toHaveText(['openai', 'anthropic', 'google'], { timeout: UI_READY });
  await page.getByRole('option', { name: 'anthropic', exact: true }).click();
  await expect(format).toHaveText('anthropic', { timeout: UI_READY });
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
});
