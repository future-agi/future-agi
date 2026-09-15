import { expect, type Page } from '@playwright/test';

// Picking a judge model in the eval playground's ModelSelector
// (frontend/src/sections/evals/components/ModelSelector.jsx) has two traps
// that every spec driving it hit, so they live here once.
//
// 1. WHICH PILL TO CLICK. EvalCreatePage seeds `model` with "turing_large"
//    (:175) and the draft POST sends it (:348), but :273-275 clears it back
//    to "" once TURING_MODELS is a *confirmed* denial. `turing_models` is
//    `oss_locked=True` in futureagi/tfc/capabilities/registry.py, so on this
//    OSS stack the seed is always dropped and the pill renders its
//    `!model` fallback, "Select model" (ModelSelector.jsx:794). On an
//    entitled EE/cloud deployment the seed survives and the pill reads
//    "Turing Large" instead — hence the fallback below rather than a bare
//    getByText('Select model'), which would be silently OSS-only.
//
// 2. WHICH ROW TO CLICK. The picker's "Your Models" section lists the whole
//    BYOK catalog matching the search, not just the model the spec created.
//    LiteLLMModelListView sorts available-first
//    (futureagi/model_hub/views/run_prompt.py:2598
//    `response_data.sort(key=lambda x: not x["is_available"])`), so `.last()`
//    — which these specs used to call — deliberately lands on an
//    *unavailable* row. Clicking one does NOT select it: ModelSelector.jsx
//    :1299-1308 opens the KeysDrawer and returns early, leaving the model
//    Popover open. Its invisible MUI backdrop then intercepts every later
//    click, so the next `.ql-editor` click burns the full test timeout
//    (the failure mode that took out the agent-eval specs). Matching an
//    exact text node pins the intended model — a bare
//    getByRole('menuitem', { name }) substring-matches sibling variants like
//    "gpt-4o-mini-2024-07-18" too.
// The pill that opens the model picker, under either deployment. Reach for
// this instead of a bare getByText('Select model'): that text is the `!model`
// fallback (ModelSelector.jsx:794), so it only renders where the seeded
// `turing_large` was dropped — i.e. OSS. On an entitled EE/cloud stack the
// seed survives and the same pill reads "Turing Large", and a spec pinned to
// the OSS string waits out its full timeout there instead of failing fast.
export function modelPill(page: Page) {
  return page.getByText('Select model', { exact: true })
    .or(page.getByText('Turing Large', { exact: true }));
}

export async function selectJudgeModel(page: Page, modelName: string): Promise<void> {
  await modelPill(page).first().click();

  await page.getByPlaceholder('Search models...').fill(modelName);
  await page
    .getByRole('menuitem')
    .filter({ has: page.getByText(modelName, { exact: true }) })
    .first()
    .click();

  // Selecting closes the Popover (ModelSelector.jsx:1305-1307). If it is
  // still open the click hit an unavailable row and opened the KeysDrawer
  // instead — fail here, loudly and immediately, rather than 120s later on
  // whatever the spec clicks next.
  await expect(page.getByPlaceholder('Search models...')).toBeHidden();
}

// The judge model every eval-playground spec routes through the mock LLM.
// Kept here rather than re-declared per spec so the constants and the
// idempotency rule below can never drift apart.
export const JUDGE_MODEL = 'gpt-4o-mini';
export const GATEWAY_INTERNAL_URL = 'http://agentcc-gateway:8080/v1';
export const GATEWAY_INTERNAL_KEY = 'local-dev-only-shared-secret-replace-me';

interface JudgeModelActor { api: { post<T>(path: string, data?: unknown): Promise<T> } }

// `actor` is a WORKER-scoped fixture (e2e/lib/fixtures.ts), so every spec a
// worker runs shares one org — and each of them wants the same mock-routed
// `gpt-4o-mini` registered. The first spec in the worker creates it; every
// later one gets 400 "Model name already exists. Please use a different
// name." from ModelHub and dies before touching the UI. That is why this is
// create-if-absent rather than a bare POST: the specs are describing a
// precondition ("this org can reach the mock judge"), not an action.
//
// The narrower the worker count the worse the original bug: at `workers: 1`
// every spec after the first shares one org, so all of them would fail.
export async function ensureJudgeModel(
  actor: JudgeModelActor, modelName: string = JUDGE_MODEL,
): Promise<void> {
  try {
    await actor.api.post('/model-hub/custom_models/create/', {
      model_provider: 'openai', model_name: modelName,
      input_token_cost: 0, output_token_cost: 0,
      config_json: { key: GATEWAY_INTERNAL_KEY, api_base: GATEWAY_INTERNAL_URL },
    });
  } catch (err) {
    // Only "already registered" is benign — anything else (auth, gateway
    // config, a renamed field) must still fail the spec at its precondition.
    const body = JSON.stringify((err as { body?: unknown }).body ?? err);
    if (!/already exists/i.test(body)) throw err;
  }
}

// Replaces the playground's test-data JSON instead of appending to it.
//
// TestPlayground.jsx keeps a scaffold in sync with the variables it extracts
// from the instructions: once `{{output}}` exists it rewrites the editor to
// `{\n  "output": ""\n}` (the `varsChanged` effect, ~:149-215). Specs that
// clicked `.view-lines` and typed straight away appended to that scaffold,
// producing `{ "output": "" }{"output": "world"}` — invalid JSON. The
// component's `jsonKeys` memo (~:85) is a bare `JSON.parse` in a try/catch,
// so an unparseable buffer silently yields NO keys, `varMapping.output`
// stays "", and the run posts `config.mapping = {output: ""}`. The backend
// then rejects it with 400 "No input received for any of 'output'" and no
// verdict ever renders — which is why so many specs died waiting on
// `getByText('Pass')` with nothing in the UI explaining why.
//
// Select-all + delete first makes the buffer exactly what the spec typed,
// whether or not a scaffold was there. `.last()` is the test-data editor:
// ResizablePanels renders the left "Eval details" panel before the right
// TestPlayground panel, so when a Code eval puts a second Monaco on screen
// the code editor is `.first()` and this one is still last.
export async function fillTestData(page: Page, json: string): Promise<void> {
  await page.locator('.monaco-editor .view-lines').last().click();
  await page.keyboard.press('ControlOrMeta+A');
  await page.keyboard.press('Backspace');
  await page.keyboard.type(json, { delay: 20 });
}
