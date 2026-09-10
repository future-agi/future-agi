import type { Page } from '@playwright/test';
import type { ApiClient } from './api-client';

// Pinned the same way flows/prompts/author-and-commit.spec.ts does: the mock
// is reachable in the compose network at mock-llm:8080; the dev-attach spike
// overrides this to the host mock via E2E_MOCK_LLM_BASE.
export const MOCK_LLM_BASE = process.env.E2E_MOCK_LLM_BASE ?? 'http://mock-llm:8080/v1';
export const MOCK_LLM_KEY = 'local-dev-only-shared-secret-replace-me';

const CUSTOM_MODEL_PATH = '/model-hub/custom_models/create/';
const API_KEYS_PATH = '/model-hub/api-keys/';

// Same endpoint and wire body flows/prompts/author-and-commit.spec.ts uses —
// creating the custom model runs a live completion against api_base before
// it persists, so a 2xx already proves the backend can reach the mock.
export function seedMockCustomModel(api: ApiClient, name: string) {
  return api.post(CUSTOM_MODEL_PATH, {
    model_provider: 'openai',
    model_name: name,
    input_token_cost: 0,
    output_token_cost: 0,
    config_json: { key: MOCK_LLM_KEY, api_base: MOCK_LLM_BASE },
  });
}

// ApiKeyViewSet.create (model_hub/views/run_prompt.py) looks up an existing
// key for the (organization, provider) pair and updates it in place rather
// than rejecting the POST, so this is already upsert-safe server-side — no
// duplicate-key error to swallow here.
export function seedOpenAIProviderKey(api: ApiClient) {
  return api.post(API_KEYS_PATH, { provider: 'openai', key: 'e2e-mock' });
}

// Opens the model picker from the Run button and selects an option by its
// (unique) name.
export async function selectModel(page: Page, name: string) {
  await page.getByRole('button', { name: 'Run Prompt' }).click();
  const search = page.getByRole('textbox', { name: 'Select model' });
  await search.fill(name);
  await page.getByRole('menuitem').filter({ hasText: name }).last().click();
}
