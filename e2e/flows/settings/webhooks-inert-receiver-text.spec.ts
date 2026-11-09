import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { WebhookReceiver } from '../../lib/webhook-receiver';

const UI_READY = 60_000;
// Pinned from futureagi/webhooks/urls.py (WH-2).
const ENDPOINTS = '/webhooks/endpoints/';

interface Envelope<T> { status: boolean; result: T }
interface DeliveryDetail {
  id: string; status: string;
  attempts: { n: number; response_status: number | null; response_body_excerpt: string | null }[];
}

test('SET-E2E-003: a receiver that answers with HTML, a javascript: link and an RTLO is shown as inert text', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SET-E2E-003', area: 'settings',
    userGoal: 'An admin reads what their endpoint answered without the answer acting on the page (WT-15c)',
    steps: [
      'open a webhook whose endpoint answers 500 with HTML, a javascript: link and an RTLO character',
      'send a test from the endpoint page',
      'open the delivery from the log and expand "From your endpoint"',
    ],
    backendChecks: [
      'GET /webhooks/deliveries/{id}/ stores the excerpt with the HTML intact and the RTLO removed',
    ],
    browserChecks: [
      'the excerpt is literal text: no <img>, <script> or javascript: link is created and the onerror never runs',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  test.setTimeout(240_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const marker = `e2e-inert-${suffix}`;
  const echo = `<h1>${marker}</h1><img src=x onerror="window.__whInert='${marker}'">` +
    `<a href="javascript:window.__whInert='${marker}'">click me</a> invoice‮fdp.exe <script>window.__whInert='${marker}'</script>`;
  const stored = echo.replace('‮', '');
  const receiver = await WebhookReceiver.start(testInfo.parallelIndex, `/inert-${suffix}`);
  receiver.script([], { status: 500, body: echo, contentType: 'text/html' });

  try {
    // Seed: the endpoint is the setting for this flow, not its goal.
    const created = await actor.api.post<Envelope<{ endpoint: { id: string } }>>(ENDPOINTS, {
      name: marker, url: receiver.url, workspace_id: actor.workspaceId,
      subscriptions: [{ event_type: 'prompt.version.created', filters: {} }],
    });
    const id = created.result.endpoint.id;
    await testInfo.attach('seed', { contentType: 'application/json',
      body: JSON.stringify({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, endpointId: id, url: receiver.url }) });

    await test.step('UI: send a test to the endpoint that answers HTML', async () => {
      await page.goto(`/dashboard/settings/integrations/webhooks/${id}`, { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { name: marker, level: 1 })).toBeVisible({ timeout: UI_READY });
      await page.getByRole('button', { name: 'Send test' }).click();
      await page.getByRole('menuitem', { name: 'prompt.version.created' }).click();
      await expect(page.getByRole('status').filter({ hasText: 'Your endpoint answered' }))
        .toContainText('Your endpoint answered 500', { timeout: UI_READY });
    });

    let deliveryId = '';
    await test.step('API: the excerpt is stored with the HTML intact and the RTLO removed', async () => {
      const list = await actor.api.get<Envelope<{ results: { id: string }[] }>>(`${ENDPOINTS}${id}/deliveries/`);
      expect(list.result.results).toHaveLength(1);
      deliveryId = list.result.results[0].id;
      const d = await actor.api.get<Envelope<DeliveryDetail>>(`/webhooks/deliveries/${deliveryId}/`);
      expect(d.result.attempts.map((a) => [a.n, a.response_status, a.response_body_excerpt])).toEqual([[1, 500, stored]]);
    });

    await test.step('UI: the drawer shows the answer as text', async () => {
      await page.getByRole('table', { name: 'Delivery log' }).getByRole('row').filter({ hasText: 'prompt.version.created' }).click();
      const drawer = page.getByRole('dialog', { name: 'Delivery' });
      await drawer.getByRole('button', { name: 'From your endpoint' }).click({ timeout: UI_READY });
      const excerpt = drawer.getByLabel('Response excerpt, attempt 1');
      await expect(excerpt).toHaveText(stored, { timeout: UI_READY });
      await expect(excerpt.locator('img, script, a, h1')).toHaveCount(0);
      await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);
      // The only h1 naming the marker is the page's own title, not the receiver's.
      await expect(page.locator('h1', { hasText: marker })).toHaveCount(1);
      // The inline test result above the log renders the same answer, also as text.
      await expect(page.getByRole('status').filter({ hasText: 'Your endpoint answered 500' }).locator('img, script, a'))
        .toHaveCount(0);
      expect(await page.evaluate(() => (window as unknown as { __whInert?: string }).__whInert)).toBeUndefined();
    });
  } finally {
    await receiver.stop();
  }
});
