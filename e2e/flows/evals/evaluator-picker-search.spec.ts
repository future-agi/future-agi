import type { Response } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// ModelSelector.jsx ordinary-model query and useDecisionModels.js catalog query.
const MODELS = '/model-hub/api/models_list/';
// EvalCreatePage drafts on mount; KeysDrawer/KeyCard read status and write keys.
const TEMPLATES = '/model-hub/eval-templates/';
const PROVIDER_STATUS = '/model-hub/develops/provider-status/';
const API_KEYS = '/model-hub/api-keys/';
const UI_READY = 60_000;
const ui = expect.configure({ timeout: UI_READY });
type ProviderStatus = { result: { providers: { provider: string; has_key: boolean }[] } };
type Bounds = { x: number; y: number; width: number; height: number };
type Sample = Bounds & { loading: boolean };
type SampleWindow = Window & { pickerSamples: Sample[]; stopPickerSamples: () => void };

const catalogResponse = (response: Response, type: string, search?: string) => {
  const url = new URL(response.url());
  return response.request().method() === 'GET' && url.pathname === MODELS &&
    url.searchParams.get('model_type') === type && (search === undefined ||
      (url.searchParams.get('search') === search && url.searchParams.get('page') === '1'));
};

function expectNoKey(status: ProviderStatus): void {
  const providers = status.result.providers.filter(provider => provider.provider === 'typesafe');
  expect(providers).toEqual([{ ...providers[0], provider: 'typesafe', has_key: false }]);
}

test('EVAL-E2E-013: a Jev choice stays under the pointer while ordinary model search loads', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-013', area: 'evals',
    userGoal: 'Choose Jev latest with one gesture during model search and reach the TypeSafe keys drawer without selecting a model or saving a key',
    steps: ['open a real evaluator draft at normal and constrained viewport heights',
      'wait for the initial decision and ordinary-model catalogs, then search Jev',
      'observe the Jev row before and during ordinary-model loading',
      'press the row, release the unmodified search response, and release the pointer at the same position',
      'read the TypeSafe drawer and the still-unselected evaluation model'],
    backendChecks: ['the real provider-status API reports no TypeSafe key before and after each gesture',
      'the browser sends no API-key write during either gesture'],
    browserChecks: ['Jev latest does not move at loading onset or response completion',
      'one native pointer gesture closes the picker and opens one TypeSafe drawer without selecting a model',
      'the picker stays within the viewport at both heights'],
  }),
}, async ({ page, actor }, testInfo) => {
  test.setTimeout(540_000); // 2 viewports × 4 UI_READY stages + 60s API/cleanup headroom.
  page.setDefaultTimeout(UI_READY);
  // Exercise the same geometry in light mode and, at the shorter height, dark.
  await page.addInitScript(() => {
    const settings = JSON.parse(localStorage.getItem('settings') || '{}');
    localStorage.setItem('settings', JSON.stringify({ ...settings,
      themeMode: window.innerHeight < 600 ? 'dark' : 'light' }));
  });
  const keyWrites: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith(API_KEYS) && request.method() !== 'GET') {
      keyWrites.push(`${request.method()} ${new URL(request.url()).pathname}`);
    }
  });

  for (const height of [720, 560]) {
    await page.setViewportSize({ width: 1280, height });
    expectNoKey(await actor.api.get<ProviderStatus>(PROVIDER_STATUS));
    let releaseResponse!: () => void;
    const responseGate = new Promise<void>(resolve => { releaseResponse = resolve; });
    let pointerDown = false;
    try {
      await page.route(url => url.pathname === MODELS && url.searchParams.get('model_type') === 'llm' &&
        url.searchParams.get('search') === 'Jev' && url.searchParams.get('page') === '1', async route => {
        const response = await route.fetch();
        await responseGate;
        // Delay only delivery: status, body and headers are the real backend's.
        await route.fulfill({ response });
      });
      await test.step(`draft at ${height}px`, async () => {
        const draft = page.waitForResponse(response => response.request().method() === 'POST' &&
          new URL(response.url()).pathname === `${TEMPLATES}create-v2/`, { timeout: UI_READY });
        await page.goto('/dashboard/evaluations/create', { waitUntil: 'domcontentloaded' });
        const response = await draft;
        expect(response.status()).toBe(200);
        const draftId = ((await response.json()) as { result: { id: string } }).result.id;
        await testInfo.attach(`draft-${height}`, { contentType: 'application/json',
          body: JSON.stringify({ draftId, organizationId: actor.organizationId, height }) });
        await ui(page).toHaveURL(new RegExp(`/dashboard/evaluations/create/${draftId}$`));
        await page.getByRole('tab', { name: 'LLM-As-A-Judge', exact: true }).click();
      }, { timeout: UI_READY });

      const search = page.getByPlaceholder('Search models...');
      const picker = page.locator('.MuiPopover-paper').filter({ has: search });
      const latest = picker.getByRole('menuitem').filter({ hasText: /^Jev latest/ });
      await test.step(`settle initial catalogs at ${height}px`, async () => {
        const decision = page.waitForResponse(response => catalogResponse(response, 'decision'), { timeout: UI_READY });
        const ordinary = page.waitForResponse(response => catalogResponse(response, 'llm', ''), { timeout: UI_READY });
        await page.getByRole('button', { name: 'Evaluation model: Select model', exact: true }).click();
        for (const response of await Promise.all([decision, ordinary])) {
          expect(response.status()).toBe(200);
          expect(await response.finished()).toBeNull();
        }
        await ui(picker.getByRole('progressbar')).toHaveCount(0);
        await ui(picker.getByRole('combobox', { name: 'Connection for Jev 1.14', exact: true })).toBeAttached();
        await ui(latest).toBeEnabled();
      }, { timeout: UI_READY });

      await test.step(`one gesture across search loading at ${height}px`, async () => {
        // Sample in the browser, not over repeated driver round trips that can
        // miss the real 400ms debounce. Ignore pre-filter and catalog hydration.
        await search.evaluate(input => {
          const win = window as unknown as SampleWindow;
          win.pickerSamples = [];
          let frame = 0;
          const sample = () => {
            const paper = input.closest('.MuiPopover-paper');
            const row = Array.from(paper?.querySelectorAll('[role="menuitem"]') ?? [])
              .find(element => element.textContent?.startsWith('Jev latest'));
            if ((input as HTMLInputElement).value === 'Jev' && row &&
              !Array.from(paper?.querySelectorAll('div') ?? []).some(label => label.textContent === 'Future AGI')) {
              const { x, y, width, height: rowHeight } = row.getBoundingClientRect();
              win.pickerSamples.push({ x, y, width, height: rowHeight,
                loading: Boolean(paper?.querySelector('[role="progressbar"]')) });
            }
            if (input.isConnected) frame = requestAnimationFrame(sample);
          };
          frame = requestAnimationFrame(sample);
          win.stopPickerSamples = () => cancelAnimationFrame(frame);
        });
        const responseReady = page.waitForResponse(response => catalogResponse(response, 'llm', 'Jev'), { timeout: UI_READY });
        await search.fill('Jev');
        await ui(picker.getByRole('progressbar')).toBeVisible();
        await ui(latest).toBeVisible();
        await ui(latest).toBeEnabled();
        await ui(latest.getByText('Add your TypeSafe API key', { exact: true })).toBeVisible();
        // Two paint frames settle layout effects; this is not a timed delay.
        await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        const samples = await page.evaluate(() => {
          const win = window as unknown as SampleWindow;
          win.stopPickerSamples();
          return win.pickerSamples;
        });
        const footprint = (await picker.boundingBox())!;
        // MUI rounds its top position to CSS pixels; tolerate a subpixel edge.
        expect(footprint.y).toBeGreaterThanOrEqual(15.5);
        expect(footprint.y + footprint.height).toBeLessThanOrEqual(height - 15.5);
        await testInfo.attach(`picker-${height}`, { contentType: 'image/png', body: await page.screenshot() });
        const beforeLoading = samples.filter(sample => !sample.loading).at(-1);
        expect(beforeLoading, 'must observe the filtered row before the ordinary search starts').toBeDefined();
        const duringLoading = (await latest.boundingBox())!;
        await page.mouse.move(duringLoading.x + duringLoading.width / 2, duringLoading.y + duringLoading.height / 2);
        await page.mouse.down();
        pointerDown = true;
        releaseResponse();
        const response = await responseReady;
        expect(response.status()).toBe(200);
        expect(await response.finished()).toBeNull();
        await ui(picker.getByRole('progressbar')).toHaveCount(0);
        await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
        const afterLoading = (await latest.boundingBox())!;
        await page.mouse.up();
        pointerDown = false;
        await testInfo.attach(`row-bounds-${height}`, { contentType: 'application/json',
          body: JSON.stringify({ beforeLoading, duringLoading, afterLoading }) });
        // Soft geometry checks retain the independent one-gesture outcome in a
        // failing report; they do not retry or repair a missed selection.
        for (const dimension of ['x', 'y', 'width', 'height'] as const) {
          expect.soft(duringLoading[dimension], `loading onset ${dimension}`).toBeCloseTo(beforeLoading![dimension], 0);
          expect.soft(afterLoading[dimension], `response completion ${dimension}`).toBeCloseTo(duringLoading[dimension], 0);
        }
        await ui(search).toBeHidden();
      }, { timeout: UI_READY });

      await test.step(`TypeSafe drawer and unchanged selection at ${height}px`, async () => {
        const drawer = page.locator('.MuiDrawer-paper').filter({ has: page.getByPlaceholder('Search API provider') });
        await ui(drawer).toHaveCount(1);
        await ui(drawer).toBeVisible();
        await ui(drawer.locator('form').nth(0).getByText('TypeSafe', { exact: true })).toBeVisible();
        // The modal drawer aria-hides the editor, but its model pill must still
        // carry the unselected value underneath it.
        await ui(page.getByRole('button', { name: 'Evaluation model: Select model', exact: true,
          includeHidden: true })).toBeVisible();
        await ui(page.getByText('Jev latest', { exact: true })).toHaveCount(0);
        await testInfo.attach(`drawer-${height}`, { contentType: 'image/png', body: await page.screenshot() });
      }, { timeout: UI_READY });
      expectNoKey(await actor.api.get<ProviderStatus>(PROVIDER_STATUS));
      expect(keyWrites).toEqual([]);
    } finally {
      releaseResponse();
      try {
        if (pointerDown) await page.mouse.up();
      } finally {
        await page.unrouteAll({ behavior: 'wait' });
      }
    }
  }
});
