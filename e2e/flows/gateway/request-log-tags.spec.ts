import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned off the running system: the endpoint the gateway flushes its request
// logs to and the secret it signs them with (FormatLogsWebhookURL in
// agentcc-gateway/internal/plugins/logging/flusher.go, GatewayWebhookView), the
// list endpoint the Request Logs table calls, the picker's placeholder, and the
// Application column's place in that table (RequestTable.jsx COLUMNS).
//
// The traffic is delivered on that webhook rather than by calling the gateway
// itself: a key minted by an org may only use providers the org configured
// (resolveProvider in agentcc-gateway/internal/server/handlers.go rejects a
// non-internal key on a globally configured provider), and this stack's only
// provider is the shared mock. The webhook is the same entry point the gateway
// posts to, carrying the caller metadata it parsed off `x-agentcc-metadata`.
const LOGS_WEBHOOK_PATH = '/agentcc/webhook/logs/';
const WEBHOOK_SECRET =
  process.env.AGENTCC_WEBHOOK_SECRET || 'e2e-agentcc-webhook-secret';
const REQUEST_LOGS_PATH = '/agentcc/request-logs/';
const APPLICATION_PLACEHOLDER = 'Select applications...';
const APPLICATION_CELL = 'tbody tr td:nth-child(4)';
// Ingestion is synchronous, but the poll keeps a slow runner from flaking.
const LOG_VISIBLE = POLL.ASYNC_JOB;
// Browser-side waits, sized like the other flows: the local stack slows
// several-fold when specs run in parallel.
const UI_READY = 60_000;

interface RequestLogRow {
  request_id: string;
  metadata: Record<string, string>;
}
interface Paginated<T> {
  count: number;
  results: T[];
}
interface UsagePoint {
  bucket: string;
  request_count: number;
}

test(
  'GW-E2E-001: gateway traffic is filtered by the application that sent it',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-001',
      area: 'gateway',
      userGoal:
        'A platform engineer finds the gateway requests one application made, out of everything the org sent',
      steps: [
        'mint a gateway API key from the app',
        'deliver three gateway requests on the logs webhook, each tagged with an application, a service and a team',
        'open Request Logs',
        'pick one application in the Filters panel and apply it',
        'read the filtered table',
      ],
      backendChecks: [
        'each request stored in PG agentcc_request_log under the key\'s org with the caller metadata the gateway parsed',
        'the list endpoint returns only the rows of the filtered application',
        'two applications in one filter return both, a service filter and a team tag filter narrow the same way',
        'metadata-values offers exactly the two applications the org sent',
        'usage analytics grouped by application counts each application on its own',
        'the filtered UI row set equals the API result for the same filter',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    // Ingestion, then navigation and two UI waits: past the config's 120s
    // default, so a slow run ends on the assertion that ran out rather than a
    // bare timeout.
    test.setTimeout(240_000);
    const req = await request.newContext();
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const checkout = `e2e-checkout-${suffix}`;
    const search = `e2e-search-${suffix}`;
    const team = `e2e-team-${suffix}`;

    const created = await actor.api.post<{ result: { gateway_key_id: string } }>(
      '/agentcc/api-keys/',
      { name: `e2e-gw-${suffix}` },
    );
    const gatewayKeyId = created.result.gateway_key_id;

    const deliver = async (application: string, service: string, index: number) => {
      const res = await req.post(`${E2E.apiUrl}${LOGS_WEBHOOK_PATH}`, {
        headers: { 'X-Webhook-Secret': WEBHOOK_SECRET },
        data: {
          logs: [
            {
              request_id: `e2e-gw-${suffix}-${index}`,
              auth_key_id: gatewayKeyId,
              model: 'gpt-4o-mini',
              provider: 'openai',
              status_code: 200,
              latency_ms: 120,
              total_tokens: 42,
              cost: '0.000420',
              timestamp: new Date().toISOString(),
              metadata: { application, service, team },
            },
          ],
        },
      });
      expect(res.status(), await res.text()).toBe(200);
    };

    await deliver(checkout, 'recommendations', 1);
    await deliver(checkout, 'fraud-check', 2);
    await deliver(search, 'answer', 3);

    const listFor = (params: Record<string, string | number>) =>
      actor.api.get<Paginated<RequestLogRow>>(REQUEST_LOGS_PATH, params);

    await test.step('API: each request is stored under the application it named', async () => {
      await expect
        .poll(async () => (await listFor({ application: checkout })).count, LOG_VISIBLE)
        .toBe(2);

      const stored = await probe.pg<{ metadata: Record<string, string> }>(
        "SELECT metadata FROM agentcc_request_log WHERE organization_id = $1 AND metadata->>'application' = $2",
        [actor.organizationId, checkout],
      );
      expect(stored.map((row) => row.metadata.service).sort()).toEqual([
        'fraud-check',
        'recommendations',
      ]);
    });

    await test.step('API: the filters narrow by application, service and team', async () => {
      expect((await listFor({ application: `${checkout},${search}` })).count).toBe(3);
      expect((await listFor({ application: search })).count).toBe(1);
      expect((await listFor({ service: 'answer' })).count).toBe(1);
      expect((await listFor({ tags: `team:${team}` })).count).toBe(3);

      const options = await actor.api.get<{
        result: { application: string[]; service: string[] };
      }>('/agentcc/request-logs/metadata-values/');
      expect([...options.result.application].sort()).toEqual([checkout, search].sort());
      expect([...options.result.service].sort()).toEqual([
        'answer',
        'fraud-check',
        'recommendations',
      ]);

      const usage = await actor.api.get<{
        result: { groups: Record<string, UsagePoint[]> };
      }>('/agentcc/analytics/usage-timeseries/', { group_by: 'application' });
      const totals = Object.fromEntries(
        Object.entries(usage.result.groups).map(([name, points]) => [
          name,
          points.reduce((sum, point) => sum + point.request_count, 0),
        ]),
      );
      expect(totals).toEqual({ [checkout]: 2, [search]: 1 });
    });

    const applicationCells = page.locator(APPLICATION_CELL);

    await test.step('UI: filter Request Logs down to one application', async () => {
      await page.goto('/dashboard/gateway/logs', { waitUntil: 'domcontentloaded' });
      await expect(applicationCells).toHaveCount(3, { timeout: UI_READY });

      const filtered = page.waitForResponse(
        (r) =>
          r.url().includes(REQUEST_LOGS_PATH) &&
          r.ok() &&
          r.url().includes(`application=${checkout}`),
        { timeout: UI_READY },
      );
      await page.getByRole('button', { name: 'Filters' }).click();
      // The picker's options are the metadata-values endpoint's answer.
      await page.getByPlaceholder(APPLICATION_PLACEHOLDER).click();
      await page.getByRole('option', { name: checkout, exact: true }).click();
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: 'Apply' }).click();
      await filtered;

      await expect(applicationCells).toHaveText([checkout, checkout], { timeout: UI_READY });
    });

    await test.step('API: the same filter returns the same rows the table shows', async () => {
      const rows = (await listFor({ application: checkout })).results;
      expect(await applicationCells.allInnerTexts()).toEqual(
        rows.map((row) => row.metadata.application),
      );
    });

    await req.dispose();
  },
);
