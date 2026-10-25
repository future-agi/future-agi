import { request } from '@playwright/test';
import type { Locator, Page, Request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { freshWindow, gatewayNow, parseGatewayDate } from '../../lib/clock';
import { E2E } from '../../lib/env';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { MailSink, WebhookSink } from '../../lib/sinks';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// Pinned off the source:
// - the key budget: Settings > Org Config, editor tab "Budgets"
//   (settings/BudgetsConfigTab.jsx): switch "Enable per-org budgets", the "Key Budgets"
//   header (expands on click), placeholder "API key name (e.g. ci-key)" (Enter adds
//   { limit: 0 }), the entry's "Limit (USD)" saved as Number(v) || 0 (typed whole with
//   fill(), since a partial "0.0" would save 0), its "Hard" switch untouched (unset
//   is block: tenant/budget_accessors.go LevelAction); saved through
//   update-config's merge patch (hooks/useConfigPatch.js).
// - the org budget: Budgets (/dashboard/gateway/budgets, BudgetRBACSection.jsx) header
//   action "Set Budget", dialog "Set Budget" (budgets/SetBudgetDialog.jsx): select
//   "Budget Level" -> "Organization" (org_limit), "Limit ($)", "Alert Threshold (%)"
//   (default 80), select "When Limit Exceeded" -> "Block" | "Throttle" (each option
//   also carries its description), "Requests per minute" (Throttle only), "Save";
//   wire POST /agentcc/gateways/default/set-budget/ { level, config: { limit,
//   alert_threshold, on_exceed, saved_at, throttle_rpm? } }
//   (providers/hooks/useGatewayConfig.js useSetBudget); Django stores the entry under
//   budgets.org_limit (views/gateway.py _set_budget_level_config) and pushes the one
//   saved last as the flat org budget (services/config_push.py _transform_budgets,
//   _apply_org_budget_entry: org_limit, action, hard_limit, warn_threshold,
//   throttle_rpm); a key level passes through _transform_budget_level.
// - the gateway's copy (GET /-/orgs/{org}/config): tenant/config.go BudgetsConfig and
//   BudgetLevelConfig as marshalled (enabled, org_limit, action, throttle_rpm,
//   keys.<name>.limit).
// - the key's name is the same on both sides: Django creates the key at the gateway
//   with its name (services/auth_bridge.py), GET /-/keys/{id} returns it
//   (auth/keystore.go json "name"), and the budget plugin looks the key level up by
//   it (plugins/auth/auth.go auth_key_name); read back before it is typed.
// - the gateway (plugins/budget/budget.go checkOrgHierarchicalBudgets, checkLevel,
//   takeThrottleSlots): the org level first (org_limit > 0), then the key level; past
//   a limit Block answers 429 org_budget_exceeded, Throttle admits throttle_rpm
//   requests per wall-clock minute of the gateway (now.Truncate(time.Minute)) and
//   refuses the rest with 429 budget_throttled and Retry-After = ceil(next minute -
//   now), at least 1; every refusal stamps the refusal metadata (budget_exceeded_level "org" or
//   "key:<name>", budget_period, budget_period_start RFC 3339 UTC, budget_action),
//   carried into the request log row's metadata.
// - the spend per call: gpt-4o-mini at 1.5e-7 / 6e-7 USD a token
//   (internal/modeldb/litellm.json) and the mock's 7 + 7 tokens is 5.25 micros, which
//   the cost plugin (first among the post plugins, cost.go) rounds half up to 5 micros
//   and hands the budget plugin as "0.000005" (cost/micros.go formatMicros). So key
//   B's 0.000001 is passed by its first call; after step 3 the org has spent
//   1e-5 (B's first and A's); the org limit 0.000015 admits A's next call and refuses
//   the one after, at 1.5000000000000002e-5 in float64 (5e-6 added three times). The
//   first call's billed cost is attached from the request log.
// - announcements (agentcc/services/log_ingestion.py, alert_dispatcher.py): a refusal
//   coded org_budget_exceeded or budget_throttled is a budget.exceeded event; each
//   webhook and each email alert claims it once per key level|<level>|<period
//   start> (_budget_dedupe_key), so the key level and the org level are one each, and
//   Throttle after Block in the same period adds nothing; the webhook payload adds
//   budget_level, budget_period, budget_period_start, budget_action
//   (_webhook_payload); an email alert sends its pending events as one email per
//   event type (email_sender.py send_alert_digest, subject "[Agentcc] Alert: Budget
//   Exceeded"), so the flow waits for the key level's email before the org level can
//   refuse; dispatch_state holds pending and budget_keys (agentcc_email_alert).
// - Budget Config (/dashboard/gateway/budgets/config): one card per level
//   (BudgetRBACSection.jsx BudgetConfigTab, budgetLevels.js budgetRows), ConfigLines
//   as two <p>, the limit by utils/formatters.js formatCost ("$0.0000150"); the
//   Delivery Log (/dashboard/gateway/webhooks/delivery, webhooks/WebhookEventLog.jsx).
// - wire bodies: the provider, key and webhook as GW-E2E-009; the email alert with the
//   console's body (settings/EmailAlertDialog.jsx handleSave + buildProviderConfig,
//   harness/smtp-sink.spec.ts) on the stack's smtp-sink.
// Once per level per period is proven for two levels within one period; a second
// period needs clock control.
const ORG_CONFIG_PATH = '/dashboard/gateway/settings/org-config';
const BUDGETS_PATH = '/dashboard/gateway/budgets';
const BUDGET_CONFIG_PATH = '/dashboard/gateway/budgets/config';
const DELIVERY_LOG_PATH = '/dashboard/gateway/webhooks/delivery';
const UPDATE_CONFIG = /\/agentcc\/gateways\/[^/]+\/update-config\/$/;
const SET_BUDGET = /\/agentcc\/gateways\/[^/]+\/set-budget\/$/;
const MODEL = 'gpt-4o-mini';
const BUDGET_EXCEEDED = '[Agentcc] Alert: Budget Exceeded';
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// Spec-local names for what each wait waits on.
const PUSH_VISIBLE = POLL.ASYNC_JOB; // Django's push on save reaching the gateway's copy
const LOG_VISIBLE = POLL.ASYNC_JOB; // the gateway's log flush reaching Django's ingest
const WEBHOOK_DELIVERED = POLL.ASYNC_JOB; // a webhook event delivered after commit
const MAIL_SENT = POLL.SCHEDULE_TICK; // an email alert's on-demand send, the 60 s evaluator behind it
// provisioning 120 + seeding 15 + key-name precondition PUSH_VISIBLE 60 + Budgets
// editor step 75 + #1 60 + four calls 5 + #3 MAIL_SENT 150 + Block dialog step 75 +
// #4 60 + three calls 5 + Throttle dialog step 75 + #6 60 + fresh window at most 30 +
// two calls 5 + Retry-After wait at most 56 + one call 2 + #9 LOG_VISIBLE 60 + #10
// WEBHOOK_DELIVERED 60 + #11 MAIL_SENT 150 + two UI steps 2 x 75 (150) + headroom 30
// = 1303 s, rounded up.
const FLOW_TIMEOUT = 1_320_000;

type Created<T> = { result: T };
type Call = { status: number; code?: string; retryAfter: string | null; date: string | null };
type Budgets = { enabled?: boolean; org_limit?: number; action?: string; throttle_rpm?: number;
  keys?: Record<string, { limit?: number }> };

const outcome = (c: Call) => (c.status === 200 ? 200 : `${c.status}:${c.code}`);

/** Each Budget Config card: its title, its chip and its ConfigLines (two <p>) as label -> value. */
const readBudgetCards = (page: Page) => page.locator('.MuiCard-root').evaluateAll((cards) => cards.map((card) => ({
  title: card.querySelector('h6')?.textContent ?? '',
  chip: card.querySelector('.MuiChip-label')?.textContent ?? '',
  lines: Object.fromEntries(Array.from(card.querySelectorAll('.MuiStack-root'))
    .map((s) => Array.from(s.children))
    .filter((c) => c.length === 2 && c.every((e) => e.tagName === 'P'))
    .map(([label, value]) => [label.textContent ?? '', value.textContent ?? ''])),
})));

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-012: a key budget and an organization budget refuse past their limits, Block then Throttle, and each level is announced once by email and webhook',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-012',
      area: 'gateway',
      userGoal:
        'An org admin sets a budget on one API key and an organization budget that first blocks and then throttles spending past its limit, and is told once per budget level in this period, by email and by webhook, that the budget was exceeded. Once per level per period is proven for two levels (key, org) within one period; a second period needs clock control',
      steps: [
        'seed the org\'s own provider on the mock serving gpt-4o-mini, org keys A and B, a webhook and an SMTP email alert subscribed to Budget Exceeded; read key B\'s name back from the gateway',
        'open Org Config, open the config editor, Budgets: enable per-org budgets, add a Key Budget for key B with Limit (USD) 0.000001, Save & Activate',
        'call with key B three times, then with key A once',
        'open Budgets, Set Budget: Organization, Limit 0.000015, When Limit Exceeded Block, Save',
        'call with key A three times',
        'Set Budget again: Organization, Limit 0.000015, Throttle, Requests per minute 1, Save',
        'in a fresh minute of the gateway\'s clock, call with key A twice',
        'wait out the returned Retry-After plus 1 s, then call with key A once',
        'open Budget Config, then Webhooks > Delivery Log',
      ],
      backendChecks: [
        'the key budget save reaches the gateway: per-org budgets on, key B\'s limit as sent',
        'past its key budget a key is refused while the org\'s other key passes: B 200, 429 org_budget_exceeded, 429 org_budget_exceeded; A 200',
        'the key level is announced once though it refused twice: one budget.exceeded webhook for level key:<B>, action block, and one Budget Exceeded email',
        'the Block save reaches the gateway as the org budget, beside the key budget: org_limit and action as the dialog sent',
        'past the org limit Block refuses: A 200, then 429 org_budget_exceeded twice',
        'the Throttle save reaches the gateway: action throttle with throttle_rpm as sent',
        'past the limit Throttle admits one request in the gateway\'s minute and refuses the next with budget_throttled, whose Retry-After is the seconds left in that minute',
        'once that minute is over, Throttle admits the next request',
        'every refusal is logged under the org with its refusal metadata, oldest first: key level twice, org Block twice, org Throttle once, all monthly from this month\'s start',
        'each level is announced to the webhook exactly once in the period: two budget.exceeded deliveries, key level then org level, and two event rows',
        'each level is emailed exactly once in the period: two Budget Exceeded emails, and the alert holds two budget keys with nothing pending',
        'the saves sent exactly the console\'s bodies: the key budget patch, org_limit 0.000015 block with alert threshold 80, then the same with throttle and throttle_rpm 1',
      ],
      browserChecks: [
        'Budget Config shows exactly the Organization card (Limit $0.0000150, Period monthly, Alert Threshold 80%, Throttle 1 requests a minute per gateway replica past the limit) and key B\'s card',
        'the Delivery Log lists exactly two budget.exceeded deliveries, both delivered',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Minted per run: key B's name anchors the key level (#2, #3, #9, #10); the
    // recipient is mailed by nothing else (#3, #11); channel and scenario are this
    // flow's alone (#3, #10); the fresh org makes #9 an exact list.
    const scenario = `gw12-${suffix}`;
    const channel = `gw12-${suffix}`;
    const providerName = `e2e-gw12-${suffix}`;
    const nameA = `e2e-gw12-${suffix}-a`;
    const nameB = `e2e-gw12-${suffix}-b`;
    const recipient = `e2e-gw12-${suffix}@e2e.invalid`;

    const upstream = new MockUpstream();
    const sink = new WebhookSink();
    const mail = new MailSink();
    const provider = await owner.api.post<Created<{ id: string }>>('/agentcc/provider-credentials/', {
      provider_name: providerName, credentials: { api_key: `sk-e2e-gw12-${suffix}` }, base_url: upstream.baseUrl(scenario),
      api_format: 'openai', models_list: [MODEL], extra_config: { api_path_prefix: '/v1' },
    });
    const keyA = await owner.api.post<Created<{ key: string; gateway_key_id: string }>>('/agentcc/api-keys/', { name: nameA });
    const keyB = await owner.api.post<Created<{ key: string; gateway_key_id: string }>>('/agentcc/api-keys/', { name: nameB });
    const webhook = await owner.api.post<Created<{ id: string }>>('/agentcc/webhooks/', {
      name: `e2e-gw12-${suffix}`, url: sink.url(channel), secret: `e2e-gw12-secret-${suffix}`,
      events: ['budget.exceeded'], is_active: true,
    });
    const alert = await owner.api.post<Created<{ id: string }>>('/agentcc/email-alerts/', {
      name: `e2e-gw12-${suffix}`, recipients: [recipient], events: ['budget.exceeded'], thresholds: {}, provider: 'smtp',
      provider_config: { host: mail.host, port: mail.port, from_email: 'alerts@e2e.invalid', use_tls: true },
      cooldown_minutes: 0, is_active: true,
    });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, scenario, channel, recipient, organizationId: owner.organizationId,
        providerId: provider.result.id, keyAGatewayId: keyA.result.gateway_key_id, keyBGatewayId: keyB.result.gateway_key_id,
        webhookId: webhook.result.id, emailAlertId: alert.result.id }, null, 2),
    });

    const admin = await GatewayAdmin.open();
    const caller = await request.newContext();
    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const patches: Request[] = [];
    const budgetSaves: Request[] = [];
    page.on('request', (r) => {
      if (r.method() !== 'POST') return;
      const path = new URL(r.url()).pathname;
      if (UPDATE_CONFIG.test(path)) patches.push(r);
      if (SET_BUDGET.test(path)) budgetSaves.push(r);
    });
    let n = 0;
    const call = async (key: 'A' | 'B'): Promise<Call> => {
      n += 1;
      const res = await caller.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
        headers: { Authorization: `Bearer ${(key === 'A' ? keyA : keyB).result.key}` },
        data: { model: MODEL, messages: [{ role: 'user', content: `e2e-gw12-${suffix}-${n}` }] },
      });
      const body = (res.status() === 200 ? {} : await res.json().catch(() => ({}))) as { error?: { code?: string } };
      return { status: res.status(), code: body.error?.code,
        retryAfter: res.headers()['retry-after'] ?? null, date: res.headers()['date'] ?? null };
    };
    const budgets = async (): Promise<Budgets | null> =>
      ((await admin.orgConfig(owner.organizationId))?.budgets as Budgets | undefined) ?? null;
    const openBudgetsTab = async (p: Page): Promise<Locator> => {
      await p.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
      await p.getByRole('button', { name: /^(Edit Config|Create Custom Config)$/ }).click({ timeout: UI_READY });
      const dialog = p.getByRole('dialog', { name: 'Edit Organization Config' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('tab', { name: 'Budgets', exact: true }).click();
      return dialog;
    };
    const setOrgBudget = async (p: Page, onExceed: 'Block' | 'Throttle', throttleRpm?: string) => {
      await p.goto(BUDGETS_PATH, { waitUntil: 'domcontentloaded' });
      await p.getByRole('button', { name: 'Set Budget', exact: true }).click({ timeout: UI_READY });
      const dialog = p.getByRole('dialog', { name: 'Set Budget' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('combobox', { name: 'Budget Level' }).click();
      await p.getByRole('option', { name: 'Organization', exact: true }).click();
      await dialog.getByLabel('Limit ($)').fill('0.000015');
      await dialog.getByRole('combobox', { name: 'When Limit Exceeded' }).click();
      await p.getByRole('option', { name: new RegExp(`^${onExceed}`) }).click();
      if (throttleRpm !== undefined) await dialog.getByLabel('Requests per minute').fill(throttleRpm);
      const saved = p.waitForResponse(
        (r) => r.request().method() === 'POST' && SET_BUDGET.test(new URL(r.url()).pathname), { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Save', exact: true }).click();
      expect((await saved).status()).toBe(200);
      await expect(dialog).toBeHidden({ timeout: UI_READY });
    };
    const announced = async (keys: ('budget_level' | 'budget_period' | 'budget_action')[]) =>
      (await sink.received(channel)).map((r) => {
        const body = (r.body ?? {}) as Record<string, unknown>;
        return Object.fromEntries([['event', body.event], ...keys.map((k) => [k, body[k]])]);
      });
    const subjects = async () => (await mail.messagesTo(recipient)).map((m) => m.subject);

    try {
      await test.step('gateway: the org provider is pushed and key B is named as typed below (preconditions)', async () => {
        await expect.poll(async () => ({
          provider: Object.keys((await admin.orgConfig(owner.organizationId))?.providers ?? {}).includes(providerName),
          keyB: ((await admin.key(keyB.result.gateway_key_id)).body as { name?: string } | null)?.name ?? null,
        }), PUSH_VISIBLE).toEqual({ provider: true, keyB: nameB });
      });

      await test.step('UI: Budgets, per-org budgets on, a Key Budget for key B at 0.000001, Save & Activate', async () => {
        const dialog = await openBudgetsTab(page);
        await dialog.getByLabel('Enable per-org budgets', { exact: true }).check();
        await dialog.getByText('Key Budgets', { exact: true }).click();
        const keyName = dialog.getByPlaceholder('API key name (e.g. ci-key)');
        await keyName.fill(nameB);
        await keyName.press('Enter');
        await dialog.getByLabel('Limit (USD)', { exact: true }).fill('0.000001');
        const saved = page.waitForResponse(
          (r) => r.request().method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname), { timeout: UI_READY });
        await dialog.getByRole('button', { name: 'Save & Activate', exact: true }).click();
        expect((await saved).status()).toBe(200);
        await expect(dialog).toBeHidden({ timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      const keyPatch = () => patches[0]?.postDataJSON() as { budgets: Budgets };
      await test.step('gateway: the key budget reached the gateway as sent', async () => {
        const sent = keyPatch().budgets;
        await expect.poll(async () => {
          const b = await budgets();
          return b && { enabled: b.enabled, keyLimit: b.keys?.[nameB]?.limit ?? null };
        }, PUSH_VISIBLE).toEqual({ enabled: sent.enabled, keyLimit: sent.keys?.[nameB]?.limit });
      });

      await test.step('gateway: past its budget key B is refused, key A passes', async () => {
        const b = [outcome(await call('B')), outcome(await call('B')), outcome(await call('B'))];
        const a = [outcome(await call('A'))];
        expect({ b, a }).toEqual({ b: [200, '429:org_budget_exceeded', '429:org_budget_exceeded'], a: [200] });
      });

      await test.step('sink + mail: the key level is announced once, by webhook and by email', async () => {
        const keyLevelOnce = {
          deliveries: [{ event: 'budget.exceeded', budget_level: `key:${nameB}`, budget_action: 'block' }],
          subjects: [BUDGET_EXCEEDED],
        };
        await expect.poll(async () => ({ deliveries: await announced(['budget_level', 'budget_action']), subjects: await subjects() }),
          MAIL_SENT).toEqual(keyLevelOnce);
      });

      await test.step('UI: Set Budget, Organization, 0.000015, Block', async () => {
        await setOrgBudget(page, 'Block');
      }, { timeout: STEP_BUDGET });

      await test.step('gateway: the Block budget reached the gateway beside the key budget', async () => {
        const block = budgetSaves[0]?.postDataJSON() as { config: { limit: number; on_exceed: string } };
        await expect.poll(async () => {
          const b = await budgets();
          return b && { org_limit: b.org_limit ?? null, action: b.action ?? null, keyLimit: b.keys?.[nameB]?.limit ?? null };
        }, PUSH_VISIBLE).toEqual({ org_limit: block.config.limit, action: block.config.on_exceed,
          keyLimit: keyPatch().budgets.keys?.[nameB]?.limit });
      });

      await test.step('gateway: past the org limit Block refuses', async () => {
        const blocked = [outcome(await call('A')), outcome(await call('A')), outcome(await call('A'))];
        expect(blocked).toEqual([200, '429:org_budget_exceeded', '429:org_budget_exceeded']);
      });

      await test.step('UI: Set Budget, Organization, 0.000015, Throttle at 1 request a minute', async () => {
        await setOrgBudget(page, 'Throttle', '1');
      }, { timeout: STEP_BUDGET });

      await test.step('gateway: the Throttle budget reached the gateway as sent', async () => {
        const throttle = budgetSaves[1]?.postDataJSON() as { config: { limit: number; on_exceed: string; throttle_rpm?: number } };
        await expect.poll(async () => {
          const b = await budgets();
          return b && { org_limit: b.org_limit ?? null, action: b.action ?? null, throttle_rpm: b.throttle_rpm ?? null };
        }, PUSH_VISIBLE).toEqual({ org_limit: throttle.config.limit, action: throttle.config.on_exceed,
          throttle_rpm: throttle.config.throttle_rpm });
      });

      let retryAfter = 0;
      await test.step('gateway: Throttle admits one request in the gateway\'s minute, the next waits out the minute', async () => {
        const window = await freshWindow(caller);
        const first = await call('A');
        const second = await call('A');
        retryAfter = Number(second.retryAfter);
        const dateSecond = second.date ? parseGatewayDate(second.date).getUTCSeconds() : null;
        await testInfo.attach('throttle', { contentType: 'application/json',
          body: JSON.stringify({ windowSecond: window.second, first: first.status, second: second.status,
            retryAfter: second.retryAfter, date: second.date, dateSecond }) });
        expect([{ status: first.status }, { status: second.status, code: second.code,
          retryAfterIsSecondsLeft: dateSecond !== null && [0, 1].includes(retryAfter - (60 - dateSecond)) }])
          .toEqual([{ status: 200 }, { status: 429, code: 'budget_throttled', retryAfterIsSecondsLeft: true }]);
      });

      await test.step('gateway: once that minute is over, Throttle admits the next request', async () => {
        await new Promise((resolve) => setTimeout(resolve, (retryAfter + 1) * 1000));
        expect({ afterTheMinute: (await call('A')).status }).toEqual({ afterTheMinute: 200 });
      });

      await test.step('storage: every refusal is logged with its refusal metadata, oldest first', async () => {
        const now = await gatewayNow(caller);
        const monthStart = `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, '0')}-01T00:00:00Z`;
        const k = { level: `key:${nameB}`, period: 'monthly', start: monthStart, action: 'block' };
        const o = { level: 'org', period: 'monthly', start: monthStart, action: 'block' };
        await expect.poll(async () => probe.pg<Record<string, string>>(
          `SELECT metadata->>'budget_exceeded_level' AS level, metadata->>'budget_period' AS period,
                  metadata->>'budget_period_start' AS start, metadata->>'budget_action' AS action
             FROM agentcc_request_log WHERE organization_id = $1 AND status_code = 429 ORDER BY started_at`,
          [owner.organizationId]), LOG_VISIBLE).toEqual([k, k, o, o, { ...o, action: 'throttle' }]);
        const cost = await probe.pg<{ cost: string }>(
          `SELECT cost::text AS cost FROM agentcc_request_log WHERE organization_id = $1 AND status_code = 200
            ORDER BY started_at LIMIT 1`, [owner.organizationId]);
        await testInfo.attach('first-call-cost', { contentType: 'application/json', body: JSON.stringify(cost) });
      });

      await test.step('sink + storage: each level reached the webhook exactly once', async () => {
        const eachLevelOnceByWebhook = {
          deliveries: [
            { event: 'budget.exceeded', budget_level: `key:${nameB}`, budget_period: 'monthly', budget_action: 'block' },
            { event: 'budget.exceeded', budget_level: 'org', budget_period: 'monthly', budget_action: 'block' },
          ],
          events: 2,
        };
        await expect.poll(async () => ({
          deliveries: await announced(['budget_level', 'budget_period', 'budget_action']),
          events: (await probe.pg<{ n: number }>(
            `SELECT count(*)::int AS n FROM agentcc_webhook_event WHERE organization_id = $1 AND event_type = 'budget.exceeded'`,
            [owner.organizationId]))[0].n,
        }), WEBHOOK_DELIVERED).toEqual(eachLevelOnceByWebhook);
      });

      await test.step('mail + storage: each level was emailed exactly once', async () => {
        const eachLevelOnceByEmail = { subjects: [BUDGET_EXCEEDED, BUDGET_EXCEEDED], keys: 2, pending: 0 };
        await expect.poll(async () => {
          const [state] = await probe.pg<{ keys: number; pending: number }>(
            `SELECT CASE WHEN jsonb_typeof(dispatch_state->'budget_keys') = 'object'
                         THEN (SELECT count(*) FROM jsonb_object_keys(dispatch_state->'budget_keys')) ELSE 0 END::int AS keys,
                    CASE WHEN jsonb_typeof(dispatch_state->'pending') = 'object'
                         THEN (SELECT count(*) FROM jsonb_object_keys(dispatch_state->'pending')) ELSE 0 END::int AS pending
               FROM agentcc_email_alert WHERE id = $1`, [alert.result.id]);
          return { subjects: await subjects(), keys: state?.keys ?? null, pending: state?.pending ?? null };
        }, MAIL_SENT).toEqual(eachLevelOnceByEmail);
      });

      await test.step('UI wire: the saves sent exactly the console\'s bodies', async () => {
        expect({ patches: patches.map((r) => r.postDataJSON()), budgets: budgetSaves.map((r) => r.postDataJSON()) }).toEqual({
          patches: [{ budgets: { enabled: true, keys: { [nameB]: { limit: 0.000001 } } } }],
          budgets: [
            { level: 'org_limit', config: { limit: 0.000015, alert_threshold: 80, on_exceed: 'block', saved_at: expect.stringMatching(ISO) } },
            { level: 'org_limit', config: { limit: 0.000015, alert_threshold: 80, on_exceed: 'throttle', saved_at: expect.stringMatching(ISO), throttle_rpm: 1 } },
          ],
        });
      });

      await test.step('UI: Budget Config shows the Organization budget throttled and key B\'s budget', async () => {
        await page.goto(BUDGET_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('heading', { name: 'Organization', exact: true })).toBeVisible({ timeout: UI_READY });
        await expect.poll(() => readBudgetCards(page), { timeout: UI_READY }).toEqual([
          { title: 'Organization', chip: 'throttle 1/min', lines: { Limit: '$0.0000150', Period: 'monthly',
            'Alert Threshold': '80%', Throttle: '1 requests a minute per gateway replica past the limit' } },
          { title: `Key ${nameB}`, chip: 'block', lines: { Limit: '$0.0000010', Period: 'monthly' } },
        ]);
      }, { timeout: STEP_BUDGET });

      await test.step('UI: the Delivery Log lists the two budget.exceeded deliveries', async () => {
        await page.goto(DELIVERY_LOG_PATH, { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('columnheader', { name: 'Event Type', exact: true })).toBeVisible({ timeout: UI_READY });
        const rows = page.locator('tbody tr');
        await expect(rows).toHaveCount(2, { timeout: UI_READY });
        await expect(rows.locator('td:nth-child(1)')).toHaveText(['budget.exceeded', 'budget.exceeded'], { timeout: UI_READY });
        await expect(rows.locator('td:nth-child(2)')).toHaveText(['delivered', 'delivered'], { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
    } finally {
      await admin.dispose();
      await caller.dispose();
      await context.close();
    }
  },
);
