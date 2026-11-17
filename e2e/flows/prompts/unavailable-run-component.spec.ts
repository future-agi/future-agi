import type { Request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedSource } from './unavailable-replay/seed';

// r2.js useRunConfigs -> endpoints.develop.datasetRuns.list; R1 is absent
// on this branch, so the real server answers route-level 404, not a stub.
const runsPath = (id: string) => `/model-hub/datasets/${id}/runs/`;
// model_hub/urls.py and ExperimentDetailV2Serializer.
const detailPath = (id: string) => `/model-hub/experiments/v2/${id}/`;
// Test-only entry built by unavailable-replay/build.mjs; never a product route.
const REPLAY = '/';
const UI_READY = 60_000;
const CYCLES = 20;
// This opt-in suite requires its separate image; ordinary product routes are not substitutes.
test.skip(process.env.E2E_COMPONENT_REPLAY !== '1', 'Requires the isolated component-replay image');
interface Detail { id: string; name: string; dataset_id: string; prompt_configs: { id: string; name: string; prompt_id: string; prompt_version: string; model: string }[] }

test('PROMPT-E2E-003: isolated component replay bounds unavailable run reads', {
  tag: ['@flow', '@component-replay'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-003', area: 'prompts',
    userGoal: 'In an isolated component replay, see an unavailable run configuration without automatic request storms; this is not public-route acceptance',
    steps: [
      'seed one real experiment in each of two synthetic organizations using public APIs',
      'open the test-only source component with its real experiment and unavailable run-config endpoint',
      'unmount and remount the observer twenty times while delivering focus and reconnect events',
      'read the unavailable state, press Retry once and verify one additional request',
      'verify another organization cannot read the experiment and retained configuration identities are unchanged',
    ],
    backendChecks: [
      'each real run-config endpoint returns 404 exactly once across twenty observer remount/focus/reconnect cycles and once more after explicit Retry',
      'each owner can read its persisted experiment while the other organization receives 404; read-only replay preserves exact configuration identities',
    ],
    browserChecks: [
      'the production ExperimentSourceGate shows Experiment unavailable and withholds editable children in Chromium and Firefox component replays',
      'this test-only mount does not establish public Edit entry-point reachability or Results acceptance',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // Two fixtures, two navigations and error-state waits, 40 cycles, two retries.
  test.setTimeout(600_000);
  const prefix = `e2e-unavailable-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  for (const index of [0, 1]) {
    const actor = index === 0 ? scopeActors.ownerA : scopeActors.ownerB;
    const other = index === 0 ? scopeActors.ownerB : scopeActors.ownerA;
    const seeded = await seedSource(scopeActors, scopeProbe, index, `${prefix}-${index}`);
    await testInfo.attach(`seeded-source-${index}`, { body: JSON.stringify(seeded), contentType: 'application/json' });
    const path = detailPath(seeded.experimentId);
    const read = async () => (await actor.api.get<{ result: Detail }>(path)).result;
    const identities = (detail: Detail) => ({ id: detail.id, name: detail.name, dataset_id: detail.dataset_id,
      configs: detail.prompt_configs.map(({ id, name, prompt_id, prompt_version, model }) => ({ id, name, prompt_id, prompt_version, model })) });
    const original = identities(await read());
    expect(original.id).toBe(seeded.experimentId);
    expect(original.configs).toHaveLength(1);
    const rows = await scopeProbe.pg<{ name: string; organization_id: string; workspace_id: string }>(
      'SELECT name,organization_id,workspace_id FROM model_hub_dataset WHERE id=$1 AND NOT deleted', [seeded.datasetId]);
    expect(rows).toEqual([{ name: `${seeded.name}-dataset`, organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
    const context = await scopeActors.openContext(browser, actor);
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (['localhost', '127.0.0.1'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)) await route.continue();
      else await route.abort('blockedbyclient');
    });
    const requests: { method: string; path: string; params: string; at: number; status?: number }[] = [];
    const tracked = new Map<Request, (typeof requests)[number]>();
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.pathname !== runsPath(seeded.datasetId)) return;
      const row = { method: request.method(), path: url.pathname, params: url.search, at: Date.now() };
      requests.push(row); tracked.set(request, row);
    });
    page.on('response', response => { const row = tracked.get(response.request()); if (row) row.status = response.status(); });
    try {
      await page.goto(`${REPLAY}?datasetId=${seeded.datasetId}&experimentId=${seeded.experimentId}`, { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { name: 'Isolated component replay', exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByLabel('Experiment identity', { exact: true })).toHaveText(seeded.experimentId, { timeout: UI_READY });
      const unavailable = page.getByText('Experiment unavailable', { exact: true });
      await expect(unavailable).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText('Editable source ready', { exact: true })).toHaveCount(0);
      await expect(page.getByLabel('Configuration request state')).toHaveText('idle', { timeout: UI_READY });
      expect(requests, 'one initial unavailable configuration read').toHaveLength(1);
      const cycleCounts = [];
      for (let cycle = 0; cycle < CYCLES; cycle += 1) {
        await page.getByRole('button', { name: 'Unmount source', exact: true }).click();
        await expect(page.getByRole('region', { name: 'Experiment source' })).toHaveCount(0);
        await page.getByRole('button', { name: 'Mount source', exact: true }).click();
        // Headless browsers do not consistently change visibility. Deliver the
        // platform events used by Query's real focus/online event subscriptions.
        await page.evaluate(() => {
          Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
          window.dispatchEvent(new Event('visibilitychange'));
          Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
          window.dispatchEvent(new Event('visibilitychange'));
          window.dispatchEvent(new Event('offline'));
          window.dispatchEvent(new Event('online'));
        });
        await expect(unavailable).toBeVisible({ timeout: UI_READY });
        await expect(page.getByLabel('Configuration request state')).toHaveText('idle', { timeout: UI_READY });
        // Deliberate observation window: let asynchronous request scheduling run.
        await page.waitForTimeout(100);
        cycleCounts.push(requests.length);
      }
      // Exceeds the application's default retry delay; catches a queued retry.
      await page.waitForTimeout(2_000);
      expect(cycleCounts).toEqual(Array(CYCLES).fill(1));
      expect(requests, 'twenty cycles must not create another request').toHaveLength(1);
      expect(requests[0].status).toBe(404);
      expect(requests[0].method).toBe('GET');
      expect(new URLSearchParams(requests[0].params).get('include')).toBe('metrics,config,lineage');
      const retried = page.waitForResponse(response => new URL(response.url()).pathname === runsPath(seeded.datasetId), { timeout: UI_READY });
      await page.getByRole('button', { name: 'Retry', exact: true }).click();
      expect((await retried).status()).toBe(404);
      await expect(page.getByLabel('Configuration request state')).toHaveText('idle', { timeout: UI_READY });
      await page.waitForTimeout(2_000);
      expect(requests, 'explicit Retry adds exactly one request').toHaveLength(2);
      await expect(unavailable).toBeVisible({ timeout: UI_READY });
      expect((await scopeActors.send(other, 'GET', path)).status).toBe(404);
      expect(identities(await read())).toEqual(original);
      await testInfo.attach(`cycles-${index}`, { body: JSON.stringify({ kind: 'isolated-component-replay', publicRouteAcceptance: false,
        cycles: CYCLES, cycleCounts, requests, experiment: original, provider: 'loopback LLM STANDIN; no provider-quality claim' }), contentType: 'application/json' });
      await testInfo.attach(`unavailable-${index}`, { body: await page.screenshot(), contentType: 'image/png' });
    } finally {
      await testInfo.attach(`request-counts-${index}`, { body: JSON.stringify({ kind: 'isolated-component-replay', requests }), contentType: 'application/json' });
      await context.close();
    }
  }
});
