import { test, expect } from '../../lib/fixtures';
import { ApiError } from '../../lib/api-client';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';
import { WebhookReceiver } from '../../lib/webhook-receiver';

const UI_READY = 60_000;
// Pinned from futureagi/webhooks/urls.py (WH-2) and integrations/urls.py (WH-3).
const ENDPOINTS = '/webhooks/endpoints/';
const DESTINATIONS = '/integrations/destinations/';
// Copy pinned from frontend/src/sections/settings/integrations/webhooks/DeliveriesCard.jsx and form.js.
const NO_MATCH = 'No deliveries match these filters.';
const RETYPE = 'Type the value of X-Literal again. Values are hidden after saving, and saving the headers replaces them all.';
// Copy pinned from frontend/src/sections/settings/integrations/webhooks/copy.js URL_KEPT_HINT (WH-3 review S11).
const URL_KEPT = 'Its query values are hidden. Type the full URL to change it, or leave this empty to keep it.';
// DESIGN.md section 17.2 WT-15b: a header value that looks like a template arrives byte for byte.
const LITERAL = '${{ env.SECRET_KEY }}';
// Review M2: subscriptions the Settings card cannot show (a deployment filter, a wildcard), made with the API as the
// CLI would; the wire shape is futureagi/webhooks/serializers.py EndpointCreateRequest.
const DEP = 'dep-e2e-targeted';
const API_SUBS = [
  { event_type: 'deployment.run.completed', filters: { deployment_ids: [DEP] } },
  { event_type: 'prompt.*', filters: {} },
];
// Copy pinned from frontend/src/sections/settings/integrations/webhooks/form.js keptLine().
const KEPT_LINES = [`deployment.run.completed · deployments: ${DEP}`, 'prompt.*'];

interface Envelope<T> { status: boolean; result: T }
interface EndpointRow { id: string; name: string; header_names: string[] }
interface EndpointSettings {
  subscriptions: { event_type: string; filters: Record<string, string[]> }[];
  timeout_ms: number; rate_limit_per_s: number; max_in_flight: number; max_age_s: number; accepts_run_targeting: boolean;
}
interface DeliveryRow { id: string; status: string; attempts_count: number }
const subsOf = (subs: EndpointSettings['subscriptions']) => subs.map((sub) => [sub.event_type, {
  ...sub.filters, ...(sub.filters.statuses ? { statuses: [...sub.filters.statuses].sort() } : {}) }]);

test('SET-E2E-005: an admin redelivers the failed deliveries in bulk, edits the headers and deletes the webhook', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SET-E2E-005', area: 'settings',
    userGoal: 'An admin recovers the deliveries an outage failed, changes what the webhook sends, and removes it',
    steps: [
      'open a webhook whose endpoint answers 500 twice and send two tests from the endpoint page',
      'filter the delivery log to Failed, then to Delivered',
      'redeliver the failed deliveries in bulk, confirming the dry-run count, and watch the Delivered view fill in',
      'show the deliveries of the last 24 hours',
      'in Settings, see the subscriptions made with the API listed as kept, and change only the retry window',
      'in Settings, also send failed runs from Production started by the API, cancelled included, and change the delivery settings',
      'in Settings, point the webhook at a URL with a token in its query, change only the timeout, then type the URL again with a new token',
      'add a header in Settings and save it, then send a test',
      'add a second header without typing the first one again',
      'delete the webhook from the endpoint page',
    ],
    backendChecks: [
      'GET /webhooks/endpoints/{id}/deliveries/?status=failed has exactly the two tests',
      'the receiver gets both again with the same webhook-ids as attempt 2, and both deliveries end succeeded',
      'the save of the retry window sends PATCH /webhooks/endpoints/{id}/ with max_age_s alone, and GET /webhooks/endpoints/{id}/ still has the API-made subscriptions exactly',
      'GET /webhooks/endpoints/{id}/ has the deployment.run.failed subscription with exactly those filters next to the API-made ones, and the timeout, rate, in-flight, max age and run targeting saved',
      'while the token is hidden the timeout save sends PATCH /webhooks/endpoints/{id}/ with timeout_ms alone; the URL save sends the full URL; GET /webhooks/endpoints/{id}/ masks the token, and the next request reaches the receiver with the new one',
      'GET /webhooks/endpoints/{id}/ lists the header name and never its value; the receiver gets the value byte for byte',
      'a save that would blank the kept header is refused before it is sent: the header names are unchanged',
      'after Delete, GET /webhooks/endpoints/{id}/ is 404 and the registry no longer lists it',
    ],
    browserChecks: [
      'with the last 24 hours shown, the log is read a few times at most, never in a loop',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // Twelve UI stages, each bounded by UI_READY, plus the bulk redelivery round trip.
  test.setTimeout(480_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-bulk-${suffix}`;
  const receiver = await WebhookReceiver.start(testInfo.parallelIndex, `/bulk-${suffix}`);
  receiver.script([{ status: 500, body: 'down' }, { status: 500, body: 'down' }]);

  try {
    // Seed: the endpoint is the setting for this flow, not its goal (SET-E2E-010 adds one from the UI).
    const created = await actor.api.post<Envelope<{ endpoint: { id: string } }>>(ENDPOINTS, {
      name, url: receiver.url, workspace_id: actor.workspaceId,
      subscriptions: [{ event_type: 'prompt.version.created', filters: {} }, ...API_SUBS],
    });
    const id = created.result.endpoint.id;
    await testInfo.attach('seed', { contentType: 'application/json',
      body: JSON.stringify({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, endpointId: id, url: receiver.url }) });

    const sendTest = async (answer: RegExp) => {
      await page.getByRole('button', { name: 'Send test' }).click({ timeout: UI_READY });
      await page.getByRole('menuitem', { name: 'prompt.version.created' }).click();
      await expect(page.getByRole('status').filter({ hasText: 'Your endpoint answered' }))
        .toHaveText(answer, { timeout: UI_READY });
    };
    const deliveries = page.getByRole('region', { name: 'Deliveries' });
    const filterStatus = async (label: string) => {
      await deliveries.getByRole('combobox', { name: /^Status/ }).click();
      await page.getByRole('option', { name: label, exact: true }).click();
    };

    await test.step('UI: two tests to an endpoint that answers 500', async () => {
      await page.goto(`/dashboard/settings/integrations/webhooks/${id}`, { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { name, level: 1 })).toBeVisible({ timeout: UI_READY });
      await sendTest(/^Your endpoint answered 500/);
      await expect.poll(() => receiver.requests.length, POLL.ASYNC_JOB).toBe(1);
      await sendTest(/^Your endpoint answered 500/);
      await expect.poll(() => receiver.requests.length, POLL.ASYNC_JOB).toBe(2);
    });

    let failedIds: string[] = [];
    await test.step('UI + API: the log filtered to Failed, then to Delivered', async () => {
      const d = await actor.api.get<Envelope<{ results: DeliveryRow[] }>>(`${ENDPOINTS}${id}/deliveries/`, { status: 'failed' });
      expect(d.result.results.map((r) => [r.status, r.attempts_count])).toEqual([['failed', 1], ['failed', 1]]);
      failedIds = d.result.results.map((r) => r.id);
      await filterStatus('Failed');
      const rows = deliveries.getByRole('table', { name: 'Delivery log' }).getByRole('row').filter({ hasText: 'prompt.version.created' });
      await expect(rows).toHaveCount(2, { timeout: UI_READY });
      for (const r of await rows.all()) await expect(r).toContainText('Failed');
      await filterStatus('Delivered');
      await expect(deliveries.getByText(NO_MATCH)).toBeVisible({ timeout: UI_READY });
    });

    await test.step('UI: redeliver the failed deliveries, confirming the count', async () => {
      await deliveries.getByRole('button', { name: 'Redeliver failed (2)' }).click({ timeout: UI_READY });
      const dialog = page.getByRole('dialog', { name: 'Redeliver failed (2)' });
      await expect(dialog.getByText('2 failed deliveries will be sent again, with the same ids.')).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Redeliver 2' }).click();
      await expect(deliveries.getByRole('status')).toHaveText('2 deliveries queued again.', { timeout: UI_READY });
    });

    await test.step('Receiver + API: both arrive again as attempt 2 and succeed', async () => {
      await expect.poll(() => receiver.requests.length, POLL.ASYNC_JOB).toBe(4);
      const firstIds = receiver.requests.slice(0, 2).map((r) => String(r.headers['webhook-id'])).sort();
      const againIds = receiver.requests.slice(2, 4).map((r) => String(r.headers['webhook-id'])).sort();
      expect(againIds).toEqual(firstIds);
      expect(receiver.requests.slice(2, 4).map((r) => r.headers['x-futureagi-attempt'])).toEqual(['2', '2']);
      expect(receiver.requests.slice(2, 4).map((r) => String(r.headers['x-futureagi-delivery'])).sort())
        .toEqual([...failedIds].sort());
      await expect.poll(async () => {
        const d = await actor.api.get<Envelope<{ results: DeliveryRow[] }>>(`${ENDPOINTS}${id}/deliveries/`);
        return d.result.results.map((r) => [r.status, r.attempts_count]);
      }, POLL.ASYNC_JOB).toEqual([['succeeded', 2], ['succeeded', 2]]);
      // The Delivered view, open since before the bulk run, fills in by itself.
      const rows = deliveries.getByRole('table', { name: 'Delivery log' }).getByRole('row').filter({ hasText: 'prompt.version.created' });
      await expect(rows).toHaveCount(2, { timeout: UI_READY });
      for (const r of await rows.all()) await expect(r).toContainText('Delivered');
      // The Failed view, last seen with two rows, shows what is true now.
      await filterStatus('Failed');
      await expect(deliveries.getByText(NO_MATCH)).toBeVisible({ timeout: UI_READY });
    });

    await test.step('UI: the deliveries of the last 24 hours, read once', async () => {
      await filterStatus('Any status');
      const reads: string[] = [];
      const onRequest = (r: { method(): string; url(): string }) => {
        if (r.method() === 'GET' && r.url().includes(`${ENDPOINTS}${id}/deliveries/`)) reads.push(r.url());
      };
      page.on('request', onRequest);
      try {
        await deliveries.getByRole('combobox', { name: /^Time/ }).click();
        await page.getByRole('option', { name: 'Last 24 hours', exact: true }).click();
        const rows = deliveries.getByRole('table', { name: 'Delivery log' }).getByRole('row').filter({ hasText: 'prompt.version.created' });
        await expect(rows).toHaveCount(2, { timeout: UI_READY });
        await expect.poll(() => reads.some((u) => new URL(u).searchParams.has('since')), POLL.ASYNC_JOB).toBe(true);
        // Review M1: a start time computed while rendering made every render a new query, read at network speed.
        // Once shown, the view is read again only by the 2.5 s refresh that follows a bulk run.
        const settled = reads.length;
        await page.waitForTimeout(5_000);
        expect(reads.length - settled).toBeLessThanOrEqual(4);
      } finally {
        page.off('request', onRequest);
      }
    });

    const settings = page.getByRole('region', { name: 'Settings' });
    await test.step('UI + API: change only the retry window; the API-made subscriptions are kept', async () => {
      const kept = settings.getByRole('group', { name: 'Subscriptions set through the API' });
      await expect(kept.getByRole('listitem')).toHaveText(KEPT_LINES, { timeout: UI_READY });
      const runsComplete = settings.getByRole('checkbox', { name: 'A run completes' });
      await expect(runsComplete).toBeChecked();
      await expect(runsComplete).toBeDisabled();
      await settings.getByRole('button', { name: 'Delivery', exact: true }).click();
      await settings.getByRole('spinbutton', { name: 'Keep retrying for (h)' }).fill('48');
      const patch = page.waitForRequest((r) => r.method() === 'PATCH' && r.url().endsWith(`${ENDPOINTS}${id}/`),
        { timeout: UI_READY });
      await settings.getByRole('button', { name: 'Save changes' }).click();
      expect((await patch).postDataJSON()).toEqual({ max_age_s: 172800 });
      await expect(settings.getByRole('status')).toHaveText('Saved.', { timeout: UI_READY });
      const detail = await actor.api.get<Envelope<EndpointSettings>>(`${ENDPOINTS}${id}/`);
      expect(subsOf(detail.result.subscriptions)).toEqual([
        ['deployment.run.completed', { deployment_ids: [DEP] }],
        ['prompt.*', {}],
        ['prompt.version.created', {}],
      ]);
      expect(detail.result.max_age_s).toBe(172800);
    });

    await test.step('UI + API: subscribe to failed runs with filters and change the delivery settings', async () => {
      await settings.getByRole('checkbox', { name: 'A run fails or is interrupted' }).check();
      await settings.getByRole('checkbox', { name: 'Production', exact: true }).check();
      await settings.getByRole('checkbox', { name: 'API', exact: true }).check();
      await settings.getByRole('checkbox', { name: 'Cancelled', exact: true }).check();
      // The Delivery fold is still open from the step before.
      await settings.getByRole('spinbutton', { name: 'Timeout (s)' }).fill('20');
      await settings.getByRole('spinbutton', { name: 'At most, requests a second' }).fill('5');
      await settings.getByRole('spinbutton', { name: 'At a time' }).fill('2');
      await settings.getByRole('spinbutton', { name: 'Keep retrying for (h)' }).fill('24');
      await settings.getByRole('checkbox', { name: 'Runs can ask to send their result here' }).check();
      await settings.getByRole('button', { name: 'Save changes' }).click();
      await expect(settings.getByRole('status')).toHaveText('Saved.', { timeout: UI_READY });
      const detail = await actor.api.get<Envelope<EndpointSettings>>(`${ENDPOINTS}${id}/`);
      expect(subsOf(detail.result.subscriptions)).toEqual([
        ['deployment.run.completed', { deployment_ids: [DEP] }],
        ['deployment.run.failed', { environments: ['production'], trigger_kinds: ['api'], statuses: ['cancelled', 'failed', 'interrupted'] }],
        ['prompt.*', {}],
        ['prompt.version.created', {}],
      ]);
      const { timeout_ms, rate_limit_per_s, max_in_flight, max_age_s, accepts_run_targeting } = detail.result;
      expect({ timeout_ms, rate_limit_per_s, max_in_flight, max_age_s, accepts_run_targeting })
        .toEqual({ timeout_ms: 20000, rate_limit_per_s: 5, max_in_flight: 2, max_age_s: 86400, accepts_run_targeting: true });
    });

    // WH-3 review S11: the API masks every query value of a saved URL (url_display, "?token=…"), text the URL rule
    // refuses, so the field starts empty and the URL is sent only once it is typed in full.
    const urlField = settings.getByRole('textbox', { name: 'Endpoint URL' });
    const isEndpoint = (method: string) => (r: { method(): string; url(): string }) =>
      r.method() === method && r.url().endsWith(`${ENDPOINTS}${id}/`);
    // Save, check the PATCH body, and wait for the endpoint's re-read: the card resets its form to the saved
    // endpoint in an effect once that read renders (WebhookEndpointPage.jsx), so nothing is typed before it.
    const saveSends = async (body: Record<string, unknown>) => {
      const patch = page.waitForRequest(isEndpoint('PATCH'), { timeout: UI_READY });
      const reread = page.waitForResponse((r) => isEndpoint('GET')(r.request()), { timeout: UI_READY });
      await settings.getByRole('button', { name: 'Save changes' }).click();
      expect((await patch).postDataJSON()).toEqual(body);
      await reread;
      await expect(settings.getByRole('status')).toHaveText('Saved.', { timeout: UI_READY });
      await page.waitForTimeout(500);
    };
    await test.step('UI + API: a URL with a token in its query is kept while the timeout changes, then changed when typed in full', async () => {
      await expect(urlField).toHaveValue(receiver.url, { timeout: UI_READY });
      await urlField.fill(`${receiver.url}?token=first`);
      await saveSends({ url: `${receiver.url}?token=first` });
      await expect(urlField).toHaveValue('');
      await expect(urlField).toHaveAttribute('placeholder', /\?token=…$/);
      await expect(settings.getByText(URL_KEPT)).toBeVisible();
      // The Delivery fold is still open.
      await settings.getByRole('spinbutton', { name: 'Timeout (s)' }).fill('25');
      await saveSends({ timeout_ms: 25000 });
      await expect(urlField).toHaveValue('');
      await urlField.fill(`${receiver.url}?token=second`);
      await saveSends({ url: `${receiver.url}?token=second` });
      const detail = await actor.api.get<Envelope<{ url: string; timeout_ms: number }>>(`${ENDPOINTS}${id}/`);
      expect(detail.result.url).toMatch(/\?token=…$/);
      expect(detail.result.url).not.toContain('second');
      expect(detail.result.timeout_ms).toBe(25000);
    });

    await test.step('UI + API: add a header and save it', async () => {
      await settings.getByRole('button', { name: 'Headers' }).click();
      await settings.getByRole('button', { name: 'Add header' }).click();
      await settings.getByRole('textbox', { name: 'Header name' }).last().fill('X-Literal');
      await settings.getByLabel('Value', { exact: true }).last().fill(LITERAL);
      await settings.getByRole('button', { name: 'Save changes' }).click();
      await expect(settings.getByRole('status')).toHaveText('Saved.', { timeout: UI_READY });
      const detail = await actor.api.get<Envelope<EndpointRow>>(`${ENDPOINTS}${id}/`);
      expect(detail.result.header_names).toEqual(['X-Literal']);
      expect(JSON.stringify(detail)).not.toContain('SECRET_KEY');
    });

    await test.step('Receiver: the header arrives byte for byte', async () => {
      await sendTest(/^Your endpoint answered 200/);
      expect(receiver.requests).toHaveLength(5);
      expect(receiver.requests[4].headers['x-literal']).toBe(LITERAL);
      // Sent to the URL typed last, its token included.
      expect(receiver.requests[4].path).toBe(`${new URL(receiver.url).pathname}?token=second`);
    });

    await test.step('UI + API: a save that would blank the kept header is refused', async () => {
      await expect(settings.getByLabel('Value', { exact: true }).first()).toHaveAttribute('placeholder', 'Hidden after saving', { timeout: UI_READY });
      await settings.getByRole('button', { name: 'Add header' }).click();
      await settings.getByRole('textbox', { name: 'Header name' }).last().fill('X-Second');
      await settings.getByLabel('Value', { exact: true }).last().fill('2');
      await settings.getByRole('button', { name: 'Save changes' }).click();
      await expect(settings.getByRole('alert').filter({ hasText: RETYPE })).toBeVisible({ timeout: UI_READY });
      await expect(settings.getByText('Saved.')).toHaveCount(0);
      const detail = await actor.api.get<Envelope<EndpointRow>>(`${ENDPOINTS}${id}/`);
      expect(detail.result.header_names).toEqual(['X-Literal']);
    });

    await test.step('UI + API: delete the webhook', async () => {
      await page.getByRole('button', { name: 'More actions' }).click();
      await page.getByRole('menuitem', { name: 'Delete' }).click();
      const dialog = page.getByRole('dialog', { name: `Delete ${name}?` });
      await dialog.getByRole('button', { name: 'Delete', exact: true }).click();
      await expect(page).toHaveURL(/\/dashboard\/settings\/integrations$/, { timeout: UI_READY });
      await expect(page.getByRole('heading', { name: 'Webhooks', exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByRole('row').filter({ hasText: name })).toHaveCount(0, { timeout: UI_READY });
      const gone = await actor.api.get(`${ENDPOINTS}${id}/`).then(() => 200, (e: unknown) => {
        if (e instanceof ApiError) return e.status;
        throw e;
      });
      expect(gone).toBe(404);
      const reg = await actor.api.get<Envelope<{ destinations: { id: string }[] }>>(DESTINATIONS);
      expect(reg.result.destinations.filter((d) => d.id === id)).toEqual([]);
    });
  } finally {
    await receiver.stop();
  }
});
