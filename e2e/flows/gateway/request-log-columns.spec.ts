import { request } from '@playwright/test';
import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { authInitScript } from '../../lib/auth';
import { provisionActor } from '../../lib/provisioning';

// Pinned off the running system, like request-log-tags.spec.ts: the logs
// webhook the gateway flushes to, the request-logs list endpoint, the
// org-scoped custom-property declaration endpoint the Columns picker reads
// (AgentccCustomPropertySchemaViewSet) and the browser-local preference key
// the picker writes (frontend/src/sections/gateway/logs/columns/columnPrefsStorage.js).
const LOGS_WEBHOOK_PATH = '/agentcc/webhook/logs/';
const WEBHOOK_SECRET =
  process.env.AGENTCC_WEBHOOK_SECRET || 'e2e-agentcc-webhook-secret';
const REQUEST_LOGS_PATH = '/agentcc/request-logs/';
const CUSTOM_PROPERTIES_PATH = '/agentcc/custom-properties/';
const STORAGE_PREFIX = 'agentcc.requestLogs.columns.v1';
const DEFAULT_HEADERS = [
  'Timestamp',
  'Model',
  'Provider',
  'Application',
  'Service',
  'Status',
  'Latency',
  'Cost',
  'Tokens',
  'Session ID',
];
const LOG_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;

interface RequestLogRow {
  request_id: string;
  metadata: Record<string, string>;
}
interface Paginated<T> {
  count: number;
  results: T[];
}
interface CustomProperty {
  id: string;
  name: string;
  organization: string;
}
interface UserInfo {
  id: string;
}

const headerTexts = (page: Page) =>
  page.locator('table thead th').allInnerTexts();

const tenantCells = (page: Page) =>
  page.locator('table tbody td[data-column="metadata:tenant"]');

test(
  'GW-E2E-003: a platform engineer adds a declared custom property as a Request Logs column',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-003',
      area: 'gateway',
      userGoal:
        'A platform engineer shows a declared custom property beside the built-in Request Logs columns, keeps that choice across reloads in this browser, and never sees it under another organization',
      steps: [
        'declare a custom property `tenant` for the org',
        'mint a gateway API key and deliver three gateway requests on the logs webhook, two carrying tenant metadata',
        'open Request Logs and open the Columns picker',
        'check `tenant`, hide `Provider` and move `Model` down',
        'reload the page',
        'reset to default',
        'open Request Logs as a second organization in the same browser',
        'delete the declaration and reopen the picker',
      ],
      backendChecks: [
        'the declaration is stored in PG agentcc_custom_property_schema under the org',
        'the table renders the tenant value per row and `-` where the row carries none',
        'selecting and reordering columns issues no per-row request-log detail call and at most one declaration list call',
        'the selection and order survive a reload for the same user, org and browser',
        'reset restores the ten default headers and removes only this preference record',
        'the second org never sees the first org\u2019s tenant column, declaration or saved record',
        'after the declaration is deleted the saved column is listed as no longer declared and is not rendered',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);
    const req = await request.newContext();
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const application = `e2e-app-${suffix}`;

    const declared = await actor.api.post<{ result: CustomProperty }>(
      CUSTOM_PROPERTIES_PATH,
      {
        name: 'tenant',
        description: `e2e ${suffix}`,
        property_type: 'string',
        required: false,
        allowed_values: [],
      },
    );
    expect(declared.result.organization).toBe(actor.organizationId);

    const created = await actor.api.post<{ result: { gateway_key_id: string } }>(
      '/agentcc/api-keys/',
      { name: `e2e-gw-${suffix}` },
    );
    const gatewayKeyId = created.result.gateway_key_id;

    const deliver = async (index: number, metadata: Record<string, string>) => {
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
              timestamp: new Date(Date.now() + index * 1000).toISOString(),
              metadata: { application, ...metadata },
            },
          ],
        },
      });
      expect(res.status(), await res.text()).toBe(200);
    };

    await deliver(1, { tenant: 'acme' });
    await deliver(2, {});
    await deliver(3, { tenant: 'globex' });

    const me = await actor.api.get<UserInfo>('/accounts/user-info/');
    const storageKey = `${STORAGE_PREFIX}:${me.id}:${actor.organizationId}:requests`;

    await test.step('API: the declaration and the tagged requests are stored under the org', async () => {
      await expect
        .poll(
          async () =>
            (await actor.api.get<Paginated<RequestLogRow>>(REQUEST_LOGS_PATH, { application }))
              .count,
          LOG_VISIBLE,
        )
        .toBe(3);
      const stored = await probe.pg<{ name: string }>(
        'SELECT name FROM agentcc_custom_property_schema WHERE organization_id = $1 AND deleted = false',
        [actor.organizationId],
      );
      expect(stored.map((row) => row.name)).toContain('tenant');
    });

    await test.step('UI: defaults are unchanged and the tenant column can be added', async () => {
      await page.goto(`/dashboard/gateway/logs?application=${application}`, {
        waitUntil: 'domcontentloaded',
      });
      await expect(page.locator('table tbody tr')).toHaveCount(3, { timeout: UI_READY });
      expect(await headerTexts(page)).toEqual(DEFAULT_HEADERS);
      expect(await page.evaluate((key) => localStorage.getItem(key), storageKey)).toBeNull();

      const detailCalls: string[] = [];
      const declarationCalls: string[] = [];
      const listCalls: string[] = [];
      const onRequest = (r: { url: () => string }) => {
        const url = r.url();
        if (/\/agentcc\/request-logs\/[0-9a-f-]{36}\/?(\?|$)/.test(url)) detailCalls.push(url);
        if (url.includes(CUSTOM_PROPERTIES_PATH)) declarationCalls.push(url);
        if (/\/agentcc\/request-logs\/?\?/.test(url)) listCalls.push(url);
      };
      page.on('request', onRequest);
      const listCallsBefore = listCalls.length;

      const columnsButton = page.getByRole('button', { name: 'Columns' });
      await columnsButton.click();
      const dialog = page.getByRole('dialog', { name: 'Choose columns' });
      await expect(dialog).toBeVisible();
      await expect(dialog.getByRole('checkbox', { name: 'Timestamp (always visible)' })).toBeDisabled();

      await dialog.getByRole('checkbox', { name: 'tenant' }).check();
      await dialog.getByRole('checkbox', { name: 'Provider' }).uncheck();
      await dialog.getByRole('button', { name: 'Move Model down' }).click();
      await dialog.getByRole('button', { name: 'Move Model down' }).click();
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
      await expect(columnsButton).toBeFocused();

      expect(await headerTexts(page)).toEqual([
        'Timestamp',
        'Application',
        'Model',
        'Service',
        'Status',
        'Latency',
        'Cost',
        'Tokens',
        'Session ID',
        'tenant',
      ]);
      await expect(tenantCells(page)).toHaveText(['globex', '-', 'acme']);
      // Custom headers carry no sort control and the URL keeps its filters.
      expect(page.url()).toContain(`application=${application}`);
      expect(page.url()).not.toContain('sort=');

      page.off('request', onRequest);
      expect(detailCalls).toEqual([]);
      expect(declarationCalls.length).toBeLessThanOrEqual(1);
      expect(listCalls.length - listCallsBefore).toBe(0);

      const record = JSON.parse(
        (await page.evaluate((key) => localStorage.getItem(key), storageKey)) || 'null',
      );
      expect(record).toMatchObject({ v: 1, hidden: ['builtin:provider'] });
      expect(record.columns.map((c: { id: string }) => c.id)).toContain('metadata:tenant');
      expect(JSON.stringify(record)).not.toContain('acme');
    });

    await test.step('UI: the selection survives a reload and reset clears only this record', async () => {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(tenantCells(page)).toHaveText(['globex', '-', 'acme'], { timeout: UI_READY });
      expect((await headerTexts(page)).slice(0, 3)).toEqual(['Timestamp', 'Application', 'Model']);

      const foreignKey = `${STORAGE_PREFIX}:other-user:${actor.organizationId}:requests`;
      await page.evaluate(
        ([key, value]) => localStorage.setItem(key, value),
        [foreignKey, '{"v":1,"columns":[{"id":"builtin:startedAt"}],"hidden":[]}'],
      );

      await page.getByRole('button', { name: 'Columns' }).click();
      await page.getByRole('dialog', { name: 'Choose columns' }).getByRole('button', { name: 'Reset to default' }).click();
      await page.keyboard.press('Escape');
      expect(await headerTexts(page)).toEqual(DEFAULT_HEADERS);
      expect(await page.evaluate((key) => localStorage.getItem(key), storageKey)).toBeNull();
      expect(await page.evaluate((key) => localStorage.getItem(key), foreignKey)).not.toBeNull();

      // Put the tenant column back for the remaining steps.
      await page.getByRole('button', { name: 'Columns' }).click();
      await page.getByRole('dialog', { name: 'Choose columns' }).getByRole('checkbox', { name: 'tenant' }).check();
      await page.keyboard.press('Escape');
      await expect(tenantCells(page)).toHaveCount(3);
    });

    await test.step('UI: a second organization in the same browser never sees the first org\u2019s column', async () => {
      const other = await provisionActor(req, `cols-b-${testInfo.workerIndex}`);
      const otherMe = await other.api.get<UserInfo>('/accounts/user-info/');
      const otherKey = `${STORAGE_PREFIX}:${otherMe.id}:${other.organizationId}:requests`;
      const otherContext = await page.context().browser()!.newContext();
      await otherContext.addInitScript(authInitScript, {
        access: other.tokens.access,
        refresh: other.tokens.refresh,
        organizationId: other.organizationId,
        workspaceId: other.workspaceId,
      });
      const otherPage = await otherContext.newPage();
      // Seed the first identity's record into this browser profile to prove
      // the key, not the profile, scopes the preference.
      await otherPage.goto('/', { waitUntil: 'domcontentloaded' });
      const firstRecord =
        (await page.evaluate((key) => localStorage.getItem(key), storageKey)) ?? '';
      expect(firstRecord).toContain('metadata:tenant');
      await otherPage.evaluate(
        ([key, value]) => localStorage.setItem(key, value),
        [storageKey, firstRecord],
      );

      const declarationRequests: string[] = [];
      otherPage.on('request', (r) => {
        if (r.url().includes(CUSTOM_PROPERTIES_PATH)) {
          declarationRequests.push(r.headers()['x-organization-id'] || '');
        }
      });
      await otherPage.goto('/dashboard/gateway/logs', { waitUntil: 'domcontentloaded' });
      await expect(otherPage.getByRole('button', { name: 'Columns' })).toBeVisible({ timeout: UI_READY });
      expect(await headerTexts(otherPage)).toEqual(DEFAULT_HEADERS);
      await otherPage.getByRole('button', { name: 'Columns' }).click();
      const otherDialog = otherPage.getByRole('dialog', { name: 'Choose columns' });
      await expect(otherDialog.getByText('No custom properties declared')).toBeVisible();
      await expect(otherDialog.getByText('tenant')).toHaveCount(0);
      expect(await otherPage.evaluate((key) => localStorage.getItem(key), otherKey)).toBeNull();
      expect(new Set(declarationRequests)).toEqual(new Set([other.organizationId]));
      await otherContext.close();
    });

    await test.step('UI: a deleted declaration is reported as no longer declared and not rendered', async () => {
      await actor.api.delete(`${CUSTOM_PROPERTIES_PATH}${declared.result.id}/`);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page.locator('table tbody tr')).toHaveCount(3, { timeout: UI_READY });
      expect(await headerTexts(page)).toEqual(DEFAULT_HEADERS);
      await page.getByRole('button', { name: 'Columns' }).click();
      const dialog = page.getByRole('dialog', { name: 'Choose columns' });
      await expect(dialog.getByText('No longer declared')).toBeVisible();
      await dialog.getByRole('button', { name: 'Remove tenant' }).click();
      await expect(dialog.getByText('No longer declared')).toHaveCount(0);
      const record = JSON.parse(
        (await page.evaluate((key) => localStorage.getItem(key), storageKey)) || 'null',
      );
      expect(JSON.stringify(record)).not.toContain('metadata:tenant');
    });

    await req.dispose();
  },
);
