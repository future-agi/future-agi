import { request, type APIRequestContext, type BrowserContext, type Locator, type Request } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { freshWindow } from '../../lib/clock';
import { E2E } from '../../lib/env';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';
import { WalkBoundary } from '../../lib/walk/boundary';

// Reuses GW-E2E-011's console navigation, merge-patch wire, mock provider and clock
// helpers without changing that flow. Pins re-read at 3184d9212b:
// frontend/src/sections/gateway/settings/{GatewaySettingsSection,OrgConfigSection,
// OrgConfigEditor}.jsx: Org Config, Edit Config/Create Custom Config, named dialog,
// Rate Limiting tab, Save & Activate. RateLimitingConfigTab.jsx:85-94,134-164:
// enabled switch, RPM per Key, RPM per User; :54-63 maps an empty field to 0.
const ORG_CONFIG_PATH = '/dashboard/gateway/settings/org-config';
// GW-E2E-011:49-56; request_log.py list serializer:28-74 exposes request_id,
// organization, api_key_id, model, status_code and error_message, not a result.table.
const REQUEST_LOGS_PATH = '/dashboard/gateway/logs';
const REQUEST_LOGS = '/agentcc/request-logs/';
// futureagi/agentcc/views/gateway.py:763-799: RFC7386 patch -> result.version,
// result.gateway_synced. Capture the browser's actual POST, not a guessed payload.
const UPDATE_CONFIG = '/agentcc/gateways/default/update-config/';
// agentcc-gateway/internal/plugins/ratelimit/orglimits.go:86-100,151-156,188-190:
// header sets Agent.UserID; the user counter is org:<org>:user:<user>:rpm, shared
// across keys, NOT across orgs. ratelimit.go:95-101,134 uses the authenticated key
// ID's own RPM counter when the org supplies per_key_rpm and the key has no override.
const USER_HEADER = 'x-agentcc-user-id';
// orglimits.go:41,47 and ratelimit.go:rateLimitExceeded; GW-E2E-011:29-35.
const LIMIT_FIELD = /\(([a-z_.]+) = \d+\)/;
const UI_READY = 60_000;
const UI_STEP = UI_READY + 15_000;
const CONFIG_VISIBLE = POLL.GATEWAY_SYNC; // periodic config/key sync, including push fallback
const LOG_VISIBLE = POLL.ASYNC_JOB; // gateway request-log flush -> Django ingest
const FRESH_WINDOW = 35_000; // lib/clock.ts: wait at most 30 s, plus clock HTTP reads
const CALL_GROUP = 20_000; // less than freshWindow's minimum 25 s remaining
// Fixture 120 + seed 60 + readiness 75 + 3 UI steps 225 + 2 config polls 150 +
// 2 fresh-window waits 70 + 2 call groups 40 + 2 log polls 120 + headroom 140 = 1000 s.
const FLOW_TIMEOUT = 1_000_000;

type Result<T> = { result: T };
type Key = { key: string; gateway_key_id: string };
type Limits = { enabled: boolean; per_key_rpm: number; per_user_rpm?: number };
type ConfigRow = { id: string; version: number; rate_limiting: Limits };
type Outcome = { status: number; code?: string; param?: string; retryAfter?: boolean };
type Call = { requestId: string; key: 'A1' | 'A2' | 'B'; user: string | null; marker: string };
type LogRow = { request_id: string; organization: string; api_key_id: string; model: string;
  status_code: number; error_message: string };

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-032: console per-key and per-user request limits isolate keys, users and organizations',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-032',
      area: 'gateway',
      userGoal: 'An owner sets per-key and per-user request limits in the console and only the exhausted counter is refused; other keys, users and organizations keep working',
      steps: [
        'seed two organizations with mock providers, keys A1/A2 in A and key B in B; B has the same limits as an isolation control',
        'in A open Org Config, Rate Limiting, enable per-org rate limiting, set RPM per Key to 2 and Save & Activate',
        'in one fresh gateway window call A1 three times, A2 once and B once',
        'in A clear RPM per Key, set RPM per User to 1 and Save & Activate',
        'in one fresh gateway window call u1 on A1, u1 on A2, u2 on A1 and the same u1 in B',
        'open A Request Logs and read exactly the seven calls, newest first',
      ],
      backendChecks: [
        'both console POSTs send the exact rate-limit patches, each creates one new active org config version with gateway_synced true, and the gateway carries that version and its limits',
        'per_key_rpm 2 admits A1 twice then refuses A1 with 429 rate_limit_exceeded naming per_key_rpm and Retry-After, while A2 and B each return 200',
        'per_key_rpm 0 and per_user_rpm 1 admit u1 once and refuse u1 on another key with 429 naming per_user_rpm and Retry-After, while u2 on the exhausted key and the same u1 in B return 200',
        'both organizations request logs contain exactly their calls in order with the minted model, request and key IDs; only A1 per_key_rpm and A2 u1 per_user_rpm are 429 rows, each naming its limit',
      ],
      browserChecks: [
        'the Rate Limiting tab reports enforcement, and A Request Logs shows the minted model on all seven rows with statuses newest first 200 429 200 200 429 200 200',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const model = `e2e-gw32-model-${suffix}`;
    const [u1, u2] = [`e2e-gw32-u1-${suffix}`, `e2e-gw32-u2-${suffix}`];
    const upstream = new MockUpstream();
    const providers: { name: string; id: string; organizationId: string }[] = [];
    // Same serializer/body as GW-E2E-011:122-128. Public writes only; no new actors
    // assembled here. The shared model/user spelling makes B a counter-scope control.
    for (const [label, actor] of [['a', owner], ['b', scopes.ownerB]] as const) {
      const name = `e2e-gw32-${label}-${suffix}`;
      const created = await actor.api.post<Result<{ id: string }>>('/agentcc/provider-credentials/', {
        provider_name: name, credentials: { api_key: `sk-e2e-gw32-${label}-${suffix}` },
        base_url: upstream.baseUrl(name), api_format: 'openai', models_list: [model],
        extra_config: { api_path_prefix: '/v1' },
      });
      providers.push({ name, id: created.result.id, organizationId: actor.organizationId });
    }
    const keys = {} as Record<Call['key'], Key>;
    for (const label of ['A1', 'A2', 'B'] as const) {
      keys[label] = (await (label === 'B' ? scopes.ownerB : owner).api.post<Result<Key>>(
        '/agentcc/api-keys/', { name: `e2e-gw32-${label}-${suffix}` })).result;
    }
    await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({
      suffix, model, users: [u1, u2], providers, organizationA: owner.organizationId,
      organizationB: scopes.ownerB.organizationId,
      keys: Object.fromEntries(Object.entries(keys).map(([label, key]) => [label, key.gateway_key_id])),
    }, null, 2) });

    const boundary = new WalkBoundary('GW-E2E-032');
    const calls: Call[] = [];
    let openedAdmin: GatewayAdmin | undefined;
    let openedCaller: APIRequestContext | undefined;
    let openedContext: BrowserContext | undefined;
    let failure: { error: unknown } | undefined;
    try {
      const admin = openedAdmin = await GatewayAdmin.open();
      const caller = openedCaller = await request.newContext();
      const context = openedContext = await scopes.openContext(browser, owner, { options: { serviceWorkers: 'block' },
        guard: (ctx) => boundary.install(ctx, 'owner-A') });
      const page = await context.newPage();
      const patches: Request[] = [];
      page.on('request', (r) => {
        if (r.method() === 'POST' && new URL(r.url()).pathname === UPDATE_CONFIG) patches.push(r);
      });
      // futureagi/agentcc/models/org_config.py:19-44,96,107-118: immutable org-scoped
      // versions, one nondeleted active row. config_push.py:1360-1369 sends meta.version
      // and meta.config_id; tenant/config.go:35-36 exposes meta in the admin read.
      const active = () => probe.pg<ConfigRow>(
        'SELECT id, version, rate_limiting FROM agentcc_org_config WHERE organization_id=$1 AND is_active AND NOT deleted',
        [owner.organizationId]);
      const openEditor = async (): Promise<Locator> => {
        await page.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
        await page.getByRole('button', { name: /^(Edit Config|Create Custom Config)$/ }).click({ timeout: UI_READY });
        const dialog = page.getByRole('dialog', { name: 'Edit Organization Config' });
        await expect(dialog).toBeVisible({ timeout: UI_READY });
        await dialog.getByRole('tab', { name: 'Rate Limiting', exact: true }).click();
        return dialog;
      };
      const save = async (dialog: Locator) => {
        const response = page.waitForResponse(r => r.request().method() === 'POST' &&
          new URL(r.url()).pathname === UPDATE_CONFIG, { timeout: UI_READY });
        await dialog.getByRole('button', { name: 'Save & Activate', exact: true }).click();
        const saved = await response;
        expect(saved.status()).toBe(200);
        await expect(dialog).toBeHidden({ timeout: UI_READY });
        return (await saved.json() as Result<{ version: number; gateway_synced: boolean }>).result;
      };
      const configReachedGateway = async (version: number, limits: Limits) => {
        await expect.poll(async () => {
          const rows = await active();
          const cfg = await admin.orgConfig(owner.organizationId);
          return { rows: rows.map(row => ({ version: row.version, limits: row.rate_limiting })),
            version: cfg?.meta?.version, sameConfigId: rows.length === 1 && cfg?.meta?.config_id === rows[0].id,
            limits: cfg?.rate_limiting };
        }, CONFIG_VISIBLE).toEqual({ rows: [{ version, limits }], version, sameConfigId: true,
          limits: Object.fromEntries(Object.entries(limits).filter(([, value]) => value !== 0)) });
      };
      const call = async (key: Call['key'], user?: string): Promise<Outcome> => {
        const marker = `e2e-gw32-${suffix}-${calls.length + 1}`;
        const res = await caller.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
          headers: { Authorization: `Bearer ${keys[key].key}`, ...(user ? { [USER_HEADER]: user } : {}) },
          data: { model, messages: [{ role: 'user', content: marker }] }, timeout: 5_000,
        });
        const requestId = res.headers()['x-agentcc-request-id'];
        expect(requestId).toBeTruthy();
        expect(calls.map(c => c.requestId)).not.toContain(requestId);
        calls.push({ requestId, key, user: user ?? null, marker });
        if (res.status() === 200) {
          expect((await res.json()).choices[0].message.content).toBe(`echo: ${marker}`);
          return { status: 200 };
        }
        const body = await res.json();
        return { status: res.status(), code: body.error.code, param: body.error.param,
          retryAfter: Number(res.headers()['retry-after']) > 0 };
      };

      // Seed the second org under the same policy, so omitting org from the user
      // counter cannot pass just because B has no per-user limit enabled.
      await scopes.ownerB.api.post(UPDATE_CONFIG, {
        rate_limiting: { enabled: true, per_key_rpm: 2, per_user_rpm: 1 },
      });
      await expect.poll(async () => ({
        providers: await Promise.all(providers.map(async p =>
          Boolean((await admin.orgConfig(p.organizationId))?.providers?.[p.name]))),
        keys: await Promise.all(Object.values(keys).map(async key => (await admin.key(key.gateway_key_id)).status)),
        control: (await admin.orgConfig(scopes.ownerB.organizationId))?.rate_limiting,
      }), CONFIG_VISIBLE).toEqual({ providers: [true, true], keys: [200, 200, 200],
        control: { enabled: true, per_key_rpm: 2, per_user_rpm: 1 } });
      const before = await active();
      expect(before).toHaveLength(1);
      const firstVersion = before[0].version + 1;

      await test.step('UI: enable RPM per Key 2 and capture Save & Activate', async () => {
        const dialog = await openEditor();
        await expect(dialog.getByText('This gateway enforces these limits.', { exact: true })).toBeVisible({ timeout: UI_READY });
        await dialog.getByLabel('Enable per-org rate limiting', { exact: true }).check();
        await dialog.getByLabel('RPM per Key', { exact: true }).fill('2');
        expect(await save(dialog)).toMatchObject({ version: firstVersion, gateway_synced: true });
        expect(patches.map(p => p.postDataJSON())).toEqual([
          { rate_limiting: { enabled: true, per_key_rpm: 2 } },
        ]);
      }, { timeout: UI_STEP });
      await test.step('config: per-key version active and pushed', () =>
        configReachedGateway(firstVersion, { enabled: true, per_key_rpm: 2 }));
      await test.step('clock: fresh key-limit window', () => freshWindow(caller), { timeout: FRESH_WINDOW });
      await test.step('per-key: only the third A1 call is refused', async () => {
        const outcomes = [await call('A1'), await call('A1'), await call('A1'), await call('A2'), await call('B')];
        expect(outcomes).toEqual([{ status: 200 }, { status: 200 },
          { status: 429, code: 'rate_limit_exceeded', param: 'per_key_rpm', retryAfter: true },
          { status: 200 }, { status: 200 }]);
      }, { timeout: CALL_GROUP });

      await test.step('UI: clear RPM per Key and save RPM per User 1', async () => {
        const dialog = await openEditor();
        await dialog.getByLabel('RPM per Key', { exact: true }).fill('');
        await dialog.getByLabel('RPM per User', { exact: true }).fill('1');
        expect(await save(dialog)).toMatchObject({ version: firstVersion + 1, gateway_synced: true });
        expect(patches.map(p => p.postDataJSON())).toEqual([
          { rate_limiting: { enabled: true, per_key_rpm: 2 } },
          { rate_limiting: { per_key_rpm: 0, per_user_rpm: 1 } },
        ]);
      }, { timeout: UI_STEP });
      await test.step('config: per-user version active and pushed', () =>
        configReachedGateway(firstVersion + 1, { enabled: true, per_key_rpm: 0, per_user_rpm: 1 }));
      await test.step('clock: fresh user-limit window', () => freshWindow(caller), { timeout: FRESH_WINDOW });
      await test.step('per-user: u1 is shared across keys but not organizations', async () => {
        const outcomes = [await call('A1', u1), await call('A2', u1), await call('A1', u2), await call('B', u1)];
        expect(outcomes).toEqual([{ status: 200 },
          { status: 429, code: 'rate_limit_exceeded', param: 'per_user_rpm', retryAfter: true },
          { status: 200 }, { status: 200 }]);
      }, { timeout: CALL_GROUP });

      await test.step('logs: exact identities, ordering and refusal reasons in both organizations', async () => {
        // Independent status/reason oracle: never derive expected status from the
        // response under test. The request IDs join these rows to sent key/user pairs.
        const expected = [[200, null], [200, null], [429, 'per_key_rpm'], [200, null],
          [200, null], [200, null], [429, 'per_user_rpm'], [200, null], [200, null]];
        for (const actor of [owner, scopes.ownerB] as ScopeActor[]) {
          const belongs = (c: Call) => (c.key === 'B' ? scopes.ownerB : owner).organizationId === actor.organizationId;
          await expect.poll(async () => (await actor.api.get<{ results: LogRow[] }>(REQUEST_LOGS,
            { ordering: 'started_at' })).results.map(r => [r.request_id, r.organization, r.api_key_id,
            r.model, r.status_code, r.error_message.match(LIMIT_FIELD)?.[1] ?? null]), LOG_VISIBLE)
            .toEqual(calls.flatMap((c, i) => belongs(c) ? [[c.requestId, actor.organizationId,
              keys[c.key].gateway_key_id, model, ...expected[i]]] : []));
        }
      });
      await test.step('UI: Request Logs contains exactly the seven org A calls', async () => {
        await page.goto(REQUEST_LOGS_PATH, { waitUntil: 'domcontentloaded' });
        const rows = page.locator('table tbody tr');
        await expect(rows).toHaveCount(7, { timeout: UI_READY });
        const headers = await page.locator('table thead th').allInnerTexts();
        expect(headers).toEqual(expect.arrayContaining(['Status', 'Model']));
        await expect(rows.locator(`td:nth-child(${headers.indexOf('Status') + 1})`))
          .toHaveText(['200', '429', '200', '200', '429', '200', '200'], { timeout: UI_READY });
        await expect(rows.locator(`td:nth-child(${headers.indexOf('Model') + 1})`))
          .toHaveText(Array(7).fill(model), { timeout: UI_READY });
      }, { timeout: UI_STEP });
    } catch (error) {
      failure = { error };
    } finally {
      // Attempt every cleanup even if setup, the body or an earlier cleanup failed.
      // Preserve the first error; a boundary failure must not replace its evidence.
      for (const cleanup of [
        () => testInfo.attach('calls', { contentType: 'application/json', body: JSON.stringify(calls, null, 2) }),
        () => openedContext?.close(),
        () => testInfo.attach('browser-boundary', { contentType: 'application/json', body: JSON.stringify(boundary.receipt(), null, 2) }),
        () => openedAdmin?.dispose(),
        () => openedCaller?.dispose(),
      ]) {
        try { await cleanup(); } catch (error) { failure ??= { error }; }
      }
    }
    if (failure) throw failure.error;
    boundary.assert();
  },
);
