import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { modelPill } from '../../lib/eval-model';

// Every other eval-playground spec pre-creates its judge model via
// POST /model-hub/custom_models/create/ before touching the UI — deliberate,
// since config_json.api_base routes that model through the mock LLM for
// deterministic runs. That also pre-satisfies the provider-key check, so no
// other spec ever sees the state a fresh org actually starts in: a model
// whose provider has no key yet.
//
// What the picker does then (frontend/src/sections/evals/components/
// ModelSelector.jsx):
//  - "Your Models" lists the whole BYOK catalog from GET /model-hub/api/
//    models_list/ (LiteLLMModelListView). A row's `is_available` is true iff
//    the ORG holds a key/config for that model's *provider*
//    (futureagi/model_hub/views/run_prompt.py:2486-2499) — it is not
//    per-model, and the response is sorted available-first (:2598).
//  - Clicking an unavailable row does NOT select it: :1299-1308 does
//    `setKeysDrawerModel(m); return;`, opening KeysDrawer
//    (frontend/src/components/custom-model-dropdown/KeysDrawer.jsx). It
//    leaves `modelAnchor` set, so the model Popover stays open *behind* the
//    drawer — and its invisible MUI backdrop swallows every click aimed at
//    the page until it is dismissed. Hence the explicit Escape below.
//  - Saving a key posts to /model-hub/api-keys/ and invalidates the
//    ["model-list"] query — but this picker's query key is
//    ["eval-model-list", search] (:766), so the open list does not refresh
//    itself. The model only becomes selectable after the Popover is closed
//    and reopened (`enabled: Boolean(modelAnchor)` flips and the stale query
//    refetches). There is no auto-select: the user picks the model again.
//
// No `deploymentMode` gate: unlike falcon_ai/turing_models, BYOK provider
// keys have no entry in futureagi/tfc/capabilities/registry.py at all, so
// this flow is identical on oss/ee/cloud.
//
// Anthropic, and no Test run: the KeysDrawer key form
// (frontend/src/sections/develop-detail/Common/ConfigureKeys/KeyCard.jsx)
// submits only `provider` + `key` — there is no api_base override, so a key
// added here always points at the real provider. The value below is a
// throwaway string: it makes the model selectable and the draft saveable,
// which is what this flow proves. Actually running the eval against real
// Anthropic would be nondeterministic and is what the opt-in @live-llm specs
// are for. Anthropic specifically because no other spec provisions that
// provider, so it is reliably keyless in this worker's org.
const MODEL_SEARCH = 'claude';

interface EvalListResponse { result: { items: { id: string; name: string }[]; total: number } }

test('EVAL-E2E-031: pick a keyless model, add its provider API key inline, then select it', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-031', area: 'evals',
    userGoal: 'A developer whose org has no key for a provider tries to pick one of its models, is '
      + 'routed into the Configure API keys drawer, adds the key, and then picks the model',
    steps: ['open the create-eval page', 'name the eval', 'switch to the LLM-as-a-Judge type',
            'open the model picker and click a model whose provider has no key',
            'land in the Configure API keys drawer instead of selecting it',
            'add a key for that provider', 'close the drawer and the stale picker',
            're-open the picker and select the now-available model', 'save/publish the eval'],
    backendChecks: ['models_list reports is_available false while the org holds no key for the provider',
                    'the key is persisted via POST /model-hub/api-keys/',
                    'the published eval is visible and searchable in the main eval list'],
  }),
}, async ({ page, actor }, testInfo) => {
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const evalName = `e2e-model-add-key-${suffix}`;
  const fakeKey = `e2e-fake-anthropic-key-${suffix}`;

  let draftId = '';

  await test.step('UI: a draft is auto-created on page load', async () => {
    await page.goto('/dashboard/evaluations/create');
    await page.waitForURL(/\/dashboard\/evaluations\/create\/.+/, { timeout: 15_000 });
    draftId = page.url().split('/dashboard/evaluations/create/')[1];
    expect(draftId).toMatch(/.+/);
  });

  const modelSearch = page.getByPlaceholder('Search models...');
  // The first row is deliberately not pinned to a hard-coded catalog id: the
  // list is sorted available-first, and nothing in this worker's org holds an
  // Anthropic key, so every "claude" row is unavailable and the first one is
  // as good as any. That keeps the spec from breaking when the upstream
  // LiteLLM catalog renames or reorders its Claude entries.
  const firstClaudeRow = page.getByRole('menuitem').filter({ hasText: /claude/i }).first();

  await test.step('UI: clicking a keyless model opens the keys drawer instead of selecting it', async () => {
    // The page mounts on the Agents tab, whose model pill is the same shared
    // `model` state — so on EE it already reads "Turing Large". Switch to the
    // judge tab this flow is actually about before touching the picker.
    await page.getByRole('tab', { name: 'LLM-As-A-Judge' }).click();
    await modelPill(page).first().click();
    await modelSearch.fill(MODEL_SEARCH);
    await expect(firstClaudeRow).toBeVisible({ timeout: 15_000 });
    await firstClaudeRow.click();

    await expect(page.getByText('Configure API keys')).toBeVisible({ timeout: 15_000 });
    // Nothing was selected, and the picker is still open underneath.
    await expect(modelPill(page).first()).toBeVisible();
    await expect(modelSearch).toBeVisible();
  });

  await test.step('UI: add a key for the provider', async () => {
    const drawer = page.locator('.MuiDrawer-root');
    await drawer.getByPlaceholder('Search API provider').fill('anthropic');
    // One card survives the filter, but scope + first() keeps this immune to
    // a second provider whose display_name also contains "anthropic".
    await drawer.getByLabel('API Key').first().fill(fakeKey);
    // The button READS "Add" while the provider has no key and "Save" once it
    // does (KeyCard.jsx:406) — but it also carries aria-label="save" (:401),
    // and an aria-label overrides text content when computing the accessible
    // name. So the role name is always "save" and never "Add": filtering on
    // "Add" matched nothing and burned the full test timeout.
    // Dismiss the picker BEFORE clicking Save. The model Popover is still
    // open behind the drawer, and its INVISIBLE backdrop covers the viewport
    // — so it, not the drawer's paper, wins the hit test and swallows every
    // click. (`fill()` above is unaffected: it sets a value without a hit
    // test, which is why only the click ever broke.) A real user's first
    // click lands on that backdrop and closes the picker; do the same.
    // Escape is not an option — KeysDrawer sets `disableEscapeKeyDown`.
    // Top-left corner, not the default centre: the backdrop spans the whole
    // viewport, so its centre point sits under the picker's own popover paper
    // and the click is intercepted by that instead.
    await page.locator('.MuiPopover-root .MuiBackdrop-root')
      .click({ position: { x: 5, y: 5 } });
    await drawer.getByRole('button', { name: 'save', exact: true }).first().click();
    await expect(page.getByText('API Key created successfully')).toBeVisible({ timeout: 15_000 });
  });

  await test.step('UI: dismiss the drawer and the stale picker, then select the model', async () => {
    // The drawer's close control is an icon-only IconButton with no
    // accessible name; it is the first button in the drawer's DOM order,
    // ahead of the filter chips, the search box and "Create custom model".
    await page.locator('.MuiDrawer-root').getByRole('button').first().click();
    await expect(page.getByText('Configure API keys')).toBeHidden();

    // Reload, don't just re-open the picker. Re-opening the Popover serves the
    // same cached pre-key rows, so the claude row still routes back into the
    // keys drawer instead of selecting — which is how this step failed. The
    // draft is autosaved, so reloading it refetches the model list without
    // losing the name or eval type entered above.
    await page.goto(`/dashboard/evaluations/create/${draftId}`);
    await expect(modelSearch).toBeHidden();

    // Name and eval type are set HERE, after the reload, not before it.
    // EvalCreatePage's autosave payload carries no `name` — only the final
    // Save sends it — and the type resets to the Agents default, so anything
    // entered before the reload is gone and Save Evaluation stays disabled.
    await page.getByPlaceholder('Eg: Hallucination detector').fill(evalName);
    await page.getByRole('tab', { name: 'LLM-As-A-Judge' }).click();

    await modelPill(page).first().click();
    await modelSearch.fill(MODEL_SEARCH);
    await expect(firstClaudeRow).toBeVisible({ timeout: 15_000 });
    await firstClaudeRow.click();

    // Selected this time: the Popover closed and the pill took the model's
    // name, so it no longer renders its `!model` fallback.
    await expect(modelSearch).toBeHidden();
    await expect(modelPill(page)).toBeHidden();
  });

  await test.step('UI: write judge instructions and publish (no live model call)', async () => {
    await page.locator('.ql-editor').click();
    // {{output}} is required, not decorative: canSaveSingle gates Save on
    // `extractVariables(instructions).length > 0` (EvalCreatePage.jsx:676-680),
    // so a variable-free instruction leaves the button permanently disabled.
    // Nothing runs it here — this flow publishes without a test run.
    await page.keyboard.type(
      'Reply with exactly this JSON: {"result": "Pass", "explanation": "saw {{output}}"}',
      { delay: 10 });
    await page.keyboard.press('Escape');

    await page.getByRole('button', { name: 'Save Evaluation' }).click();
    await expect(page.getByText('Evaluation saved successfully')).toBeVisible({ timeout: 15_000 });
    // EvalDetailPage appends `?v=<version_number>` once it has loaded the
    // saved version (:417), so anchor on the id followed by end-of-string
    // OR the query string rather than end-of-string alone.
    await expect(page).toHaveURL(new RegExp(`/dashboard/evaluations/${draftId}(\\?|$)`));
  });

  await test.step('API: the published eval is listed', async () => {
    const list = await actor.api.post<EvalListResponse>('/model-hub/eval-templates/list/', { search: evalName });
    expect(list.result.items.map((i) => i.id)).toEqual([draftId]);
  });
});
