import type { Page } from '@playwright/test';
// A fresh org per run: this flow saves a TypeSafe key, which the worker-scoped
// actor must not carry (EVAL-E2E-011 and EVAL-E2E-013 expect no key).
import { test, expect } from '../../lib/fresh-actor';
import { flowAnnotation } from '../../lib/flow-meta';

// One browser action's first-paint budget (README "UI_READY").
const UI_READY = 60_000;
const ui = expect.configure({ timeout: UI_READY });

// routes/sections/dashboard.jsx settingsRoute "ai-providers"; AIProviders.jsx
// reads ?provider= and opens the list already searched to it.
const PAGE = '/dashboard/settings/ai-providers';
const PROVIDER = 'typesafe';
// src/utils/axios.js endpoints.develop.apiKey.status / .create.
const PROVIDER_STATUS = '/model-hub/develops/provider-status/';
const API_KEYS = '/model-hub/api-keys/';
// e2e/stack/mock-llm/server.mjs accepts this TypeSafe key only; any other is refused.
const TYPESAFE_KEY = 'e2e-decision-typesafe-key';
// components/custom-model-dropdown/KeysHelper.js CLOUD_ONLY_PROVIDERS: listed
// under cloud whatever their type.
const CLOUD_ONLY = ['sagemaker', 'azure', 'bedrock', 'databricks'];
// AIProviders.jsx searchPlaceholder for the All and Custom model categories.
const SEARCH_ALL = 'Search AI Provider';
const SEARCH_CUSTOM = 'Search Custom Model';

interface ProviderStatus { provider: string; display_name: string; type: string; has_key: boolean }
interface StatusEnvelope { result: { providers: ProviderStatus[] } }
interface CustomModels { results: unknown[] }

/** The providers AIProviders.jsx lists under All, by display name: default
 * model providers (text, not cloud-only) plus cloud providers (json or
 * cloud-only), as KeysHelper.js filterAndSortProviders splits them. */
function listedUnderAll(providers: ProviderStatus[], search = ''): ProviderStatus[] {
  const term = search.toLowerCase();
  return providers.filter(p => p.display_name.toLowerCase().includes(term)
    && ((p.type === 'text' && !CLOUD_ONLY.includes(p.provider))
      || p.type === 'json' || CLOUD_ONLY.includes(p.provider)));
}

/** The category toggle reads `<title> (<count>)` (AIProviders.jsx filterOptions). */
function category(page: Page, title: string) {
  return page.getByRole('button', { name: new RegExp(`^${title} \\(\\d+\\)$`) });
}

test('SET-E2E-004: a link to the TypeSafe key opens AI Providers on that provider and survives navigation', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SET-E2E-004', area: 'settings',
    userGoal: 'Follow a link to add a TypeSafe key, save it, and land on the TypeSafe card again after moving around AI Providers and going Back',
    steps: [
      'open AI Providers from a link that names the TypeSafe provider',
      'see the search filled with typesafe and only the TypeSafe card',
      'enter the TypeSafe API key and add it',
      'click AI Providers in the sidebar and see every provider',
      'choose the Custom model category',
      'go Back and see the All category with only the TypeSafe card again',
    ],
    backendChecks: [
      'POST /model-hub/api-keys/ saves the key and GET /model-hub/develops/provider-status/ then lists typesafe with has_key true',
      'PG model_hub_apikey holds exactly one live typesafe row for the actor org',
      'the All count after the plain sidebar link equals the providers provider-status lists plus the org custom models',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Navigation + 7 browser waits of UI_READY each can pass the 120 s default.
  test.setTimeout(240_000);

  const before = await actor.api.get<StatusEnvelope>(PROVIDER_STATUS);
  const typesafe = before.result.providers.filter(p => p.provider === PROVIDER);
  expect(typesafe).toHaveLength(1);
  const typesafeName = typesafe[0].display_name;
  const linked = listedUnderAll(before.result.providers, PROVIDER);
  // The link's search must narrow the list to TypeSafe alone, or "only the
  // TypeSafe card" proves nothing.
  expect(linked.map(p => p.provider)).toEqual([PROVIDER]);
  await testInfo.attach('ai-providers', { contentType: 'application/json',
    body: JSON.stringify({ organizationId: actor.organizationId, typesafeName,
      providers: before.result.providers.map(p => [p.provider, p.type, p.has_key]) }) });

  const typesafeCard = page.getByText(typesafeName, { exact: true });

  await test.step('UI: the link opens the list searched to TypeSafe', async () => {
    await page.goto(`${PAGE}?provider=${PROVIDER}`, { waitUntil: 'domcontentloaded' });
    await ui(page.getByPlaceholder(SEARCH_ALL)).toHaveValue(PROVIDER);
    await ui(category(page, 'All')).toHaveText('All (1)');
    await ui(typesafeCard.first()).toBeVisible();
  });

  await test.step('UI + API: add the TypeSafe key', async () => {
    const saved = page.waitForResponse(r => new URL(r.url()).pathname === API_KEYS
      && r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByLabel('API Key').fill(TYPESAFE_KEY);
    await page.getByRole('button', { name: 'save' }).click();
    expect((await saved).status()).toBeLessThan(300);

    const after = await actor.api.get<StatusEnvelope>(PROVIDER_STATUS);
    expect(after.result.providers.filter(p => p.provider === PROVIDER))
      .toEqual([expect.objectContaining({ provider: PROVIDER, has_key: true })]);
  });

  await test.step('storage: one live TypeSafe key for the actor org', async () => {
    const rows = await probe.pg<{ provider: string }>(
      `SELECT provider FROM model_hub_apikey
        WHERE organization_id = $1 AND provider = $2 AND deleted = false`,
      [actor.organizationId, PROVIDER]);
    expect(rows).toEqual([{ provider: PROVIDER }]);
  });

  await test.step('UI + API: the plain sidebar link lists every provider', async () => {
    await page.getByRole('link', { name: 'AI Providers', exact: true }).first().click();
    await ui(page).toHaveURL(new RegExp(`${PAGE}$`));
    await ui(page.getByPlaceholder(SEARCH_ALL)).toHaveValue('');
    const status = await actor.api.get<StatusEnvelope>(PROVIDER_STATUS);
    const custom = await actor.api.get<CustomModels>('/model-hub/custom-models/');
    const expected = listedUnderAll(status.result.providers).length + custom.results.length;
    expect(expected).toBeGreaterThan(1);
    await ui(category(page, 'All')).toHaveText(`All (${expected})`);
  });

  await test.step('UI: Custom model, then Back, shows All and TypeSafe again', async () => {
    await category(page, 'Custom model').click();
    await ui(page.getByPlaceholder(SEARCH_CUSTOM)).toBeVisible();
    await page.goBack();
    await ui(page).toHaveURL(new RegExp(`${PAGE}\\?provider=${PROVIDER}$`));
    // The linked provider resets the category to All (AIProviders.jsx), so the
    // Custom model category can't hide the card the link names.
    await ui(page.getByPlaceholder(SEARCH_ALL)).toHaveValue(PROVIDER);
    await ui(category(page, 'All')).toHaveText('All (1)');
    await ui(typesafeCard.first()).toBeVisible();
  });
});
