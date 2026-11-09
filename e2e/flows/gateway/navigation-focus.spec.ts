import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// agentcc/views/api_key.py: create and list return { result: ... };
// AgentccAPIKeyCreateSerializer accepts a name. Never retain the creation secret.
const API_KEYS = '/agentcc/api-keys/';
// frontend/src/layouts/dashboard/config-navigation.jsx and routes/paths.js.
const OVERVIEW = '/dashboard/gateway';
const KEYS_PAGE = '/dashboard/gateway/keys';
const UI_READY = 60_000;
// A fixed ceiling, not a retry or an escape hatch from a focus trap.
const TAB_BUDGET = 96;
interface KeyIdentity { id: string; name: string }

test('GW-E2E-030: a keyboard user reaches the Gateway API-key grid from the top bar', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-030', area: 'gateway',
    userGoal: 'Reach the Gateway API-key grid using only forward Tab and Enter from the top bar.',
    steps: [
      'seed one uniquely named API key through the signed-in actor API',
      'open Gateway overview and focus the first top-bar control once',
      'use forward Tab to reach API keys without a nested or repeated nav stop',
      'press Enter on API keys, then Tab into the grid and read the seeded name',
    ],
    backendChecks: [
      'the scoped API-key list contains the exact seeded id and name immediately after acknowledged creation',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  test.setTimeout(120_000);
  const name = `e2e-gw30-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const created = await actor.api.post<{ result: KeyIdentity }>(API_KEYS, { name });
  const id = created.result.id;
  expect(typeof id).toBe('string');
  expect(created.result.name).toBe(name);
  await testInfo.attach('seeded-key-identity', {
    contentType: 'application/json', body: JSON.stringify({ id, name }),
  });
  // An acknowledged create is synchronous: no eventual-consistency poll here.
  const listed = await actor.api.get<{ result: KeyIdentity[] }>(API_KEYS);
  expect(listed.result.filter((key) => key.id === id).map((key) => ({ id: key.id, name: key.name })))
    .toEqual([{ id, name }]); // Fallibility changes ONLY this expected name.

  // Runner-owned projects can compare the two GatewayRail layouts. This sets
  // the persisted user preference before loading the page, never moves focus.
  const mini = testInfo.project.metadata.navLayout === 'mini';
  await page.addInitScript((themeLayout) => {
    const settings = JSON.parse(localStorage.getItem('settings') || '{}');
    localStorage.setItem('settings', JSON.stringify({ ...settings, themeLayout }));
  }, mini ? 'mini' : 'vertical');
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto(OVERVIEW, { waitUntil: 'domcontentloaded' });
  // GatewayRail.jsx supplies the existing semantic region and item links.
  const rail = page.getByRole('navigation', { name: 'AI Gateway', exact: true });
  await expect(rail.getByRole('link', { name: 'API keys', exact: true }))
    .toBeVisible({ timeout: UI_READY });
  // NavBrandRow.jsx starts with ProductSwitcher; nav-mini.jsx starts with
  // Expand sidebar. These are each layout's actual first top-bar control.
  const firstTopControl = mini
    ? page.getByRole('button', { name: 'Expand sidebar', exact: true })
    : page.getByTestId('omega-nav-brand-row').getByRole('button').first();
  await expect(firstTopControl).toBeVisible({ timeout: UI_READY });
  await firstTopControl.focus();

  const encountered: { path: string; stops: number; focusedLink: boolean }[] = [];
  let reachedKeys = false;
  for (let step = 0; step < TAB_BUDGET; step += 1) {
    await page.keyboard.press('Tab');
    const item = await page.evaluate(() => {
      const focused = document.activeElement;
      const anchor = focused?.closest<HTMLAnchorElement>('a[href]');
      if (!anchor || !anchor.closest('[data-testid="gateway-rail"]')) return null;
      const candidates = [anchor, ...anchor.querySelectorAll<HTMLElement>(
        'a[href], button, input, select, textarea, [tabindex]',
      )];
      return {
        path: new URL(anchor.href).pathname,
        stops: candidates.filter((node) => node.tabIndex >= 0 &&
          !node.hasAttribute('disabled') && node.getClientRects().length > 0).length,
        focusedLink: focused === anchor,
      };
    });
    if (item) encountered.push(item);
    if (item?.path === KEYS_PAGE && item.focusedLink) {
      reachedKeys = true;
      break;
    }
  }
  await testInfo.attach('forward-tab-navigation', {
    contentType: 'application/json', body: JSON.stringify({ budget: TAB_BUDGET, encountered, reachedKeys }),
  });
  expect(reachedKeys, `API keys must be reachable within ${TAB_BUDGET} forward Tabs`).toBe(true);
  expect(encountered.length).toBeGreaterThan(0);
  // Check after traversal so the red run also records the actual Firefox cycle.
  expect(encountered.every((item) => item.stops === 1 && item.focusedLink)).toBe(true);
  expect(new Set(encountered.map((item) => item.path)).size).toBe(encountered.length);
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL((url) => url.pathname === KEYS_PAGE, { timeout: UI_READY });

  // keys/KeyManagementSection.jsx uses this accessible name on the name cell.
  const keyName = page.getByRole('button', { name: `View details for ${name}`, exact: true });
  const keyCell = page.getByRole('gridcell').filter({ has: keyName });
  await expect(keyCell).toBeVisible({ timeout: UI_READY });
  let reachedGrid = false;
  for (let step = 0; step < TAB_BUDGET; step += 1) {
    await page.keyboard.press('Tab');
    reachedGrid = await page.evaluate(() => !!document.activeElement?.closest('[role="grid"]'));
    if (reachedGrid) break;
  }
  expect(reachedGrid, `API-key grid must be reachable within ${TAB_BUDGET} forward Tabs`).toBe(true);
  await expect(keyName).toHaveText(name, { timeout: UI_READY });
});
