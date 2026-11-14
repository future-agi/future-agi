import { request } from '@playwright/test';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import pg from 'pg';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { authInitScript } from '../../lib/auth';
import { provisionActor } from '../../lib/provisioning';
import { resolveBackendContainer } from '../../lib/simulate-seed';

// GatewaySettingsSection.jsx: the Settings tab is the section's default.
const SETTINGS_ROUTE = '/dashboard/gateway/settings';
// agentcc/urls.py: RequestLogRetentionView, AgentccAPIKeyViewSet, AgentccRequestLogViewSet.
const RETENTION = '/agentcc/request-log-retention/';
const API_KEYS = '/agentcc/api-keys/';
const REQUEST_LOGS = '/agentcc/request-logs/';
// GatewayWebhookView, and the secret docker-compose.e2e.yml gives the backend.
const LOGS_WEBHOOK_PATH = '/agentcc/webhook/logs/';
const WEBHOOK_SECRET = process.env.AGENTCC_WEBHOOK_SECRET || 'e2e-agentcc-webhook-secret';
// model_hub/apps.py: the purge is an operator command, run only with this authorization.
const OPERATOR_ENV = ['-e', 'SERVICE_TYPE=bootstrap', '-e', 'STARTUP_DB_MUTATION_MODE=operator', '-e', 'NO_STARTUP_DB_MUTATIONS=false'];
const UI_READY = 60_000;
const DAY = 24 * 60 * 60 * 1000;

type Key = { result: { gateway_key_id: string } };
type Retention = { result: { retention_days: number | null; estimated_rows: number; next_run_at: string } };
type Paginated = { count: number };

test('GW-E2E-008: an admin keeps one day of Request Logs and older rows are removed while logging continues', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-008',
    area: 'gateway',
    userGoal: 'An organization admin chooses to keep one day of Request Logs, and the cleanup removes older rows without interrupting logging',
    steps: [
      'seed request logs three days old and an hour old for the admin\'s organization, and three days old for a second organization at the default',
      'soft-delete one old row of the admin\'s organization',
      'open Gateway Settings, choose Delete after 1 day and save',
      'read the next cleanup time and the estimate of rows to remove',
      'run the retention purge in the backend while new log batches keep arriving',
      'reload Settings and read the last run',
    ],
    backendChecks: [
      'the saved retention reads back as 1 day with an estimate equal to the old rows, soft-deleted included',
      'every log batch posted during the purge answers 2xx and its rows land in PG agentcc_request_log',
      'after the purge the organization\'s old rows, live and soft-deleted, are gone and its recent rows remain; the list endpoint agrees',
      'the second organization, which kept the default, still has all its old rows',
    ],
  }),
}, async ({ browser, probe }, testInfo) => {
  test.setTimeout(6 * UI_READY + 60_000);
  // Orgs of this test's own: a retention on the worker's shared actor would
  // reach the request logs every later spec on the worker seeds.
  const accounts = await request.newContext({ baseURL: E2E.apiUrl });
  const label = `ret-${testInfo.workerIndex}`;
  const admin = await provisionActor(accounts, label);
  const bystander = await provisionActor(accounts, `${label}-b`);
  const run = `e2e-ret-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const keyOf = async (actor: typeof admin) =>
    (await actor.api.post<Key>(API_KEYS, { name: run })).result.gateway_key_id;
  const adminKey = await keyOf(admin);
  const bystanderKey = await keyOf(bystander);

  const req = await request.newContext({ baseURL: E2E.apiUrl });
  const post = (key: string, ids: string[], age: number) => req.post(LOGS_WEBHOOK_PATH, {
    headers: { 'X-Webhook-Secret': WEBHOOK_SECRET },
    data: {
      logs: ids.map(id => ({
        request_id: id, auth_key_id: key, model: 'gpt-4o-mini', provider: 'openai',
        status_code: 200, latency_ms: 50, timestamp: new Date(Date.now() - age).toISOString(),
      })),
    },
  });
  const ids = (prefix: string, n: number) => Array.from({ length: n }, (_, i) => `${run}-${prefix}-${i}`);
  const old = ids('old', 3);
  const softDeleted = `${run}-old-deleted`;
  const recent = ids('recent', 2);
  const bystanderOld = ids('b-old', 2);
  for (const [key, batch, age] of [
    [adminKey, [...old, softDeleted], 3 * DAY], [adminKey, recent, 60 * 60 * 1000], [bystanderKey, bystanderOld, 3 * DAY],
  ] as const) {
    const res = await post(key, [...batch], age);
    expect(res.status(), await res.text()).toBe(200);
  }
  await testInfo.attach('seeded-ids', {
    body: JSON.stringify({ admin: admin.organizationId, bystander: bystander.organizationId, run, old, softDeleted, recent, bystanderOld }),
    contentType: 'application/json',
  });

  // A soft-deleted row, as a deleted request log is kept. The probe stays
  // read-only; this one seeding write has its own connection.
  const seedClient = new pg.Client({ connectionString: E2E.pgUrl });
  await seedClient.connect();
  try {
    const marked = await seedClient.query(
      'UPDATE agentcc_request_log SET deleted = true, deleted_at = now() WHERE organization_id = $1 AND request_id = $2',
      [admin.organizationId, softDeleted]);
    expect(marked.rowCount).toBe(1);
  } finally {
    await seedClient.end();
  }

  const stored = async (orgId: string) => (await probe.pg<{ request_id: string }>(
    'SELECT request_id FROM agentcc_request_log WHERE organization_id = $1 AND request_id LIKE $2 ORDER BY request_id',
    [orgId, `${run}-%`])).map(r => r.request_id);
  expect(await stored(admin.organizationId)).toEqual([...old, softDeleted, ...recent].sort());

  const context = await browser.newContext({ baseURL: E2E.appUrl });
  await context.addInitScript(authInitScript, {
    access: admin.tokens.access, refresh: admin.tokens.refresh,
    organizationId: admin.organizationId, workspaceId: admin.workspaceId,
  });
  const page = await context.newPage();

  await test.step('the admin keeps one day', async () => {
    await page.goto(SETTINGS_ROUTE);
    await expect(page.getByRole('radio', { name: 'Keep forever' })).toBeChecked({ timeout: UI_READY });
    await page.getByRole('radio', { name: 'Delete after N days' }).check();
    await page.locator('#retention-days').fill('1');
    const saved = page.waitForResponse(
      res => res.url().endsWith(RETENTION) && res.request().method() === 'PUT', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    expect((await saved).status()).toBe(200);
    // Three old rows and the soft-deleted one.
    await expect(page.getByText('About 4 rows', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText('Next cleanup')).toBeVisible();
    const state = await admin.api.get<Retention>(RETENTION);
    expect(state.result).toMatchObject({ retention_days: 1, estimated_rows: 4 });
    expect(new Date(state.result.next_run_at).getUTCHours()).toBe(3);
  });

  await test.step('the purge runs while logging continues', async () => {
    const container = resolveBackendContainer();
    // Fresh batches keep arriving for as long as the purge runs.
    let purging = true;
    const statuses: number[] = [];
    const during: string[] = [];
    const posting = (async () => {
      while (purging || during.length < 2) {
        const id = `${run}-during-${during.length}`;
        during.push(id);
        statuses.push((await post(adminKey, [id], 0)).status());
      }
    })();
    const purge = promisify(execFile)('docker', ['exec', ...OPERATOR_ENV, container, 'sh', '-c',
      `cd /app/backend && python manage.py purge_agentcc_request_logs --org ${admin.organizationId}`],
    { encoding: 'utf-8' });
    const out = (await purge.finally(() => { purging = false; })).stdout;
    await posting;
    await testInfo.attach('purge-output', { body: out, contentType: 'text/plain' });
    expect(out).toContain('Deleted 4 request log rows across 1 organizations.');
    expect(statuses.length).toBeGreaterThan(0);
    expect(statuses.every(s => s >= 200 && s < 300), JSON.stringify(statuses)).toBe(true);
    const landed = (await stored(admin.organizationId)).filter(id => id.includes('-during-'));
    expect(landed).toEqual([...during].sort());
  });

  await test.step('old rows are gone, recent rows and the other organization remain', async () => {
    const left = await stored(admin.organizationId);
    expect(left.filter(id => id.includes('-old'))).toEqual([]);
    expect(left.filter(id => id.includes('-recent-'))).toEqual([...recent].sort());
    expect(await stored(bystander.organizationId)).toEqual([...bystanderOld].sort());
    // The organization has only this run's rows; the list endpoint agrees.
    const listed = await admin.api.get<Paginated>(REQUEST_LOGS, { page_size: 100 });
    expect(listed.count).toBe(left.length);
  });

  await test.step('Settings shows the last run', async () => {
    await page.reload();
    await expect(page.getByText(/4 rows\s+deleted/)).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('radio', { name: 'Delete after N days' })).toBeChecked();
    await expect(page.locator('#retention-days')).toHaveValue('1');
  });
  await context.close();
});
