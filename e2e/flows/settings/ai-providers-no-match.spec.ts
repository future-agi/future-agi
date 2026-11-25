import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// One browser action's first-paint budget (README "UI_READY").
const UI_READY = 60_000;
const ui = expect.configure({ timeout: UI_READY });

// routes/sections/dashboard.jsx settingsRoute "ai-providers".
const PAGE = '/dashboard/settings/ai-providers';
// src/utils/axios.js endpoints.develop.apiKey.status and
// endpoints.settings.customModal.getCustomModal: the two lists the page reads.
const PROVIDER_STATUS = '/model-hub/develops/provider-status/';
const CUSTOM_MODELS = '/model-hub/custom-models/';
// components/custom-model-dropdown/KeysHelper.js CLOUD_ONLY_PROVIDERS.
const CLOUD_ONLY = ['sagemaker', 'azure', 'bedrock', 'databricks'];
// AIProviders.jsx searchPlaceholder per category.
const SEARCH_ALL = 'Search AI Provider';
const SEARCH_CUSTOM = 'Search Custom Model';
// components/empty-state, variant "no-results": its clear action.
const CLEAR = 'Clear filters';

interface ProviderStatus { provider: string; display_name: string; type: string }
interface StatusEnvelope { result: { providers: ProviderStatus[] } }
interface CustomModels { results: unknown[] }

/** The category toggle reads `<title> (<count>)` (AIProviders.jsx filterOptions). */
function category(page: Page, title: string) {
  return page.getByRole('button', { name: new RegExp(`^${title} \\(\\d+\\)$`) });
}

test('SET-E2E-005: a provider search that matches nothing says so and clears back to every provider', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SET-E2E-005', area: 'settings',
    userGoal: 'Search AI Providers for a name that does not exist, be told nothing matched in the category I am in, and get the full list back in one click',
    steps: [
      'open AI Providers',
      'search for a provider name that does not exist',
      'see that no AI providers match it',
      'choose the Custom model category and see that no custom models match it',
      'click Clear filters',
      'see the All category with an empty search and every provider',
    ],
    backendChecks: [
      'GET /model-hub/develops/provider-status/ has no provider whose name contains the searched text',
      'after Clear filters the All count equals the providers provider-status lists plus the org custom models',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // Navigation + 6 browser waits of UI_READY each can pass the 120 s default.
  test.setTimeout(240_000);

  const missing = `e2e-set5-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const status = await actor.api.get<StatusEnvelope>(PROVIDER_STATUS);
  const custom = await actor.api.get<CustomModels>(CUSTOM_MODELS);
  const providers = status.result.providers;
  expect(providers.filter(p => p.display_name.toLowerCase().includes(missing))).toEqual([]);
  const everyProvider = providers.filter(p => (p.type === 'text' && !CLOUD_ONLY.includes(p.provider))
    || p.type === 'json' || CLOUD_ONLY.includes(p.provider)).length + custom.results.length;
  expect(everyProvider).toBeGreaterThan(1);
  await testInfo.attach('ai-providers-search', { contentType: 'application/json',
    body: JSON.stringify({ organizationId: actor.organizationId, missing, everyProvider }) });

  await test.step('UI: a search that matches nothing names the category', async () => {
    await page.goto(PAGE, { waitUntil: 'domcontentloaded' });
    await ui(category(page, 'All')).toHaveText(`All (${everyProvider})`);
    await page.getByPlaceholder(SEARCH_ALL).fill(missing);
    await ui(page.getByText(`No AI providers match “${missing}”`, { exact: true })).toBeVisible();
    await ui(category(page, 'All')).toHaveText('All (0)');
  });

  await test.step('UI: the same search under Custom model', async () => {
    await category(page, 'Custom model').click();
    await ui(page.getByPlaceholder(SEARCH_CUSTOM)).toHaveValue(missing);
    await ui(page.getByText(`No custom models match “${missing}”`, { exact: true })).toBeVisible();
  });

  await test.step('UI + API: Clear filters brings back every provider', async () => {
    await page.getByRole('button', { name: CLEAR }).click();
    await ui(page.getByPlaceholder(SEARCH_ALL)).toHaveValue('');
    await ui(category(page, 'All')).toHaveText(`All (${everyProvider})`);
    await expect(page.getByText(/^No .* match “/)).toHaveCount(0);
  });
});
