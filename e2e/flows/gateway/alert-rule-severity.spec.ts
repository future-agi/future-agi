import { request } from '@playwright/test';
import type { Locator, Page, Request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { WebhookSink } from '../../lib/sinks';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// Pinned off the source:
// - the console's editor: Settings > Org Config, "Create Custom Config" | "Edit
//   Config", dialog "Edit Organization Config", tabs "Alerting" and "Rate Limiting",
//   "Save & Activate" (OrgConfigEditor.jsx); the Alerting tab
//   (settings/AlertingConfigTab.jsx): switch "Enable per-org alerting" (channels and
//   rules render only while it is on; the draft keeps them when it is switched off),
//   "Channel name" + "Add Channel" (a webhook channel), its "URL"; "Add Rule" (name
//   rule_<n>, error_count, >=, 10, 5m, 15m, warning); per rule "Rule Name", select
//   "Metric" (option "Request Count" = request_count), "Threshold" (Number(v)),
//   "Window", "Cooldown", select "Severity" ("Critical", "Info"), and a clickable chip
//   per channel ("Notify:"); constants/alerting.js.
// - the wire: POST /agentcc/gateways/default/update-config/, an RFC 7386 merge patch
//   per changed section (hooks/useConfigPatch.js mergePatch: an absent section is sent
//   whole; enabled false is kept); captured with page.on('request'). The sink URL's
//   path segment gw17-<suffix> carries digits, so Django stores it encrypted and reads
//   it back masked (org_config_secrets.py alert_url_carries_credential, GW-E2E-008):
//   the reopened editor shows it as a SecretField, the second save leaves it out of
//   its patch, and the push still sends it in clear.
// - the gateway's copy (GET /-/orgs/{org}/config): alerting as tenant/config.go
//   AlertingConfig marshals it, after Django's projection (config_push.py
//   _normalize_alerting; severity in the contract's lower case).
// - the gateway (internal/alerting/alerting.go Evaluate, WebhookChannel.Send;
//   plugins/alerting ProcessResponse, InvalidateOrg, orgCooldowns): after each request
//   of an org with alerting enabled each rule is evaluated; a rule fires at most once
//   per cooldown (an explicit 0s fires on every request past its threshold), a config
//   push rebuilds the org's rules but keeps the cooldown of a rule whose name is
//   unchanged; the webhook body is { alert, severity, metric, value, threshold,
//   condition, window, fired_at, service }. The org channel is dialed through the
//   guarded egress, so the sink is the webhook sink, on the public-classified network.
// - Monitoring > Alert Rules (/dashboard/gateway/monitoring/rules,
//   monitoring/AlertingMonitoringSection.jsx TAB_SLUGS "rules"): columns Rule, Metric,
//   Condition, Threshold, Window, Cooldown, Severity, Status; the Status chip
//   (constants/alerting.js alertRuleStatus): "Alerting off" while the org's
//   alerting.enabled is not true, "Active" when the gateway lists alerting in
//   org_sections_enforced; both inputs come from the one config read
//   (GET /agentcc/gateways/default/config/, providers/hooks/useGatewayConfig.js,
//   staleTime 60000), so every Monitoring step opens with a full page.goto.
// - the push the cooldown is held across goes through Rate Limiting (the Cache tab is
//   disabled on a stock node): Requests per Minute (RPM) 1000, far above the four
//   requests.
// - wire bodies: the provider and key as GW-E2E-009.
// No real node without alerting is reachable
// end to end; the chip's dependence on the gateway's reported list is proven by a
// route rewrite of the config read, not by such a node.
const ORG_CONFIG_PATH = '/dashboard/gateway/settings/org-config';
const RULES_PATH = '/dashboard/gateway/monitoring/rules';
const UPDATE_CONFIG = /\/agentcc\/gateways\/[^/]+\/update-config\/$/;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// Spec-local names for what each wait waits on.
const PUSH_VISIBLE = POLL.ASYNC_JOB; // Django's push on save reaching the gateway's copy
const ALERT_SENT = POLL.ASYNC_JOB; // the gateway's alerting plugin dialing an org channel
// provisioning 120 + seeding 15 + editor step (2) 75 + Monitoring step (3) 75 + enable
// step (4) 75 + #1 PUSH_VISIBLE 60 + Monitoring step (Active) 75 + request 1 2 + #2
// ALERT_SENT 60 + requests 2 and 3 2 + Rate Limiting save step 75 + push precondition
// PUSH_VISIBLE 60 + request 4 1 + "every" precondition ALERT_SENT 60 + the closing
// Monitoring step (B2) 75 + headroom 30 = 860 s, rounded up. B2 runs last, so a
// perturbed severity or cooldown fails at #2 and #3 first.
const FLOW_TIMEOUT = 900_000;

type Created<T> = { result: T };
type AlertBody = { alert?: string; severity?: string; metric?: string };

/** The cells of one column of the Alert Rules table, found by its header. */
const ruleColumn = async (p: Page, header: string): Promise<Locator> => {
  const headers = await p.locator('table thead th').allInnerTexts();
  return p.locator('table tbody tr').locator(`td:nth-child(${headers.indexOf(header) + 1})`);
};

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-017: a critical alert rule fires once per cooldown with its severity, across a config change, and shows Active only once per-org alerting is on',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-017',
      area: 'gateway',
      userGoal:
        'An org admin creates a critical alert rule with a cooldown on a webhook channel and gets exactly one critical alert while the condition holds within the cooldown, even across a config change, and the console shows the rule active once per-org alerting is on and only while the gateway reports it enforces alerting. No real "not installed" node is reachable end to end; the chip\'s dependence on the reported list is proven by a route rewrite of the console\'s config read',
      steps: [
        'seed the org\'s own provider on the mock and an org key',
        'open Org Config, open the config editor, Alerting: switch on per-org alerting, add a webhook channel to the sink, add rule critical (Request Count >= 1, window 1m, cooldown 1h, Critical, on the channel) and rule every (the same with cooldown 0s, Info), switch per-org alerting off again, Save & Activate',
        'open Monitoring > Alert Rules with a full navigation',
        'Alerting: switch per-org alerting on, Save & Activate; open Alert Rules again with a full navigation',
        'call the gateway with the org key once',
        'call it twice more',
        'Rate Limiting: switch on per-org rate limiting, Requests per Minute (RPM) 1000, Save & Activate',
        'call it a fourth time',
        'open Alert Rules again with a full navigation',
      ],
      backendChecks: [
        'the saves reach the gateway: per-org alerting on, both rules with their severity and cooldown, the channel URL in clear',
        'the critical rule\'s first alert is one webhook naming the rule, severity critical and metric request_count',
        'inside its cooldown the critical rule sends nothing more, across more requests and a config push, while the 0s rule fires on every request: critical 1, every 4',
        'the saves sent exactly the console\'s patches: the alerting draft with enabled false, then alerting enabled true, then rate limiting on with global_rpm 1000',
      ],
      browserChecks: [
        'before per-org alerting is on both rules\' Status chips read "Alerting off"; after, both read "Active"',
        'the Severity cells read critical and info, the Cooldown cells 1h and 0s',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Minted per run: the rule names anchor #2 and #3 (the sink's entries are counted
    // by alert), the sink channel is this flow's alone, the model its upstream.
    const scenario = `gw17-${suffix}`;
    const sinkChannel = `gw17-${suffix}`;
    const providerName = `e2e-gw17-${suffix}`;
    const model = `e2e-gw17-model-${suffix}`;
    const channelName = `e2e-gw17-${suffix}`;
    const critical = `e2e-gw17-critical-${suffix}`;
    const every = `e2e-gw17-every-${suffix}`;

    const upstream = new MockUpstream();
    const sink = new WebhookSink();
    const url = sink.url(sinkChannel);
    const provider = await owner.api.post<Created<{ id: string }>>('/agentcc/provider-credentials/', {
      provider_name: providerName, credentials: { api_key: `sk-e2e-gw17-${suffix}` }, base_url: upstream.baseUrl(scenario),
      api_format: 'openai', models_list: [model], extra_config: { api_path_prefix: '/v1' },
    });
    const key = await owner.api.post<Created<{ key: string; gateway_key_id: string }>>('/agentcc/api-keys/', { name: `e2e-gw17-${suffix}` });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, scenario, sinkChannel, channelName, rules: [critical, every], model,
        organizationId: owner.organizationId, providerId: provider.result.id, keyGatewayId: key.result.gateway_key_id }, null, 2),
    });

    const admin = await GatewayAdmin.open();
    const caller = await request.newContext();
    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const patches: Request[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname)) patches.push(r);
    });
    let n = 0;
    const callGateway = async () => {
      n += 1;
      const res = await caller.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
        headers: { Authorization: `Bearer ${key.result.key}` },
        data: { model, messages: [{ role: 'user', content: `e2e-gw17-${suffix}-${n}` }] },
      });
      expect(res.status(), `request ${n}`).toBe(200);
    };
    const alerts = async () => (await sink.received(sinkChannel)).map((r) => r.body as AlertBody);
    const openTab = async (p: Page, tab: string): Promise<Locator> => {
      await p.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
      await p.getByRole('button', { name: /^(Edit Config|Create Custom Config)$/ }).click({ timeout: UI_READY });
      const dialog = p.getByRole('dialog', { name: 'Edit Organization Config' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('tab', { name: tab, exact: true }).click();
      return dialog;
    };
    const saveAndWait = async (p: Page, dialog: Locator) => {
      const saved = p.waitForResponse(
        (r) => r.request().method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname), { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Save & Activate', exact: true }).click();
      expect((await saved).status()).toBe(200);
      await expect(dialog).toBeHidden({ timeout: UI_READY });
    };
    const fillRule = async (p: Page, dialog: Locator, i: number, rule: { name: string; cooldown: string; severity: string }) => {
      const box = dialog.getByLabel('Rule Name', { exact: true }).nth(i)
        .locator('xpath=ancestor::div[contains(@class, "MuiPaper-root")][1]');
      await box.getByLabel('Rule Name', { exact: true }).fill(rule.name);
      await box.getByRole('combobox', { name: 'Metric' }).click();
      await p.getByRole('option', { name: 'Request Count', exact: true }).click();
      await box.getByLabel('Threshold', { exact: true }).fill('1');
      await box.getByLabel('Window', { exact: true }).fill('1m');
      await box.getByLabel('Cooldown', { exact: true }).fill(rule.cooldown);
      await box.getByRole('combobox', { name: 'Severity' }).click();
      await p.getByRole('option', { name: rule.severity, exact: true }).click();
      await box.getByRole('button', { name: channelName, exact: true }).click();
    };
    const openRules = async (p: Page) => {
      await p.goto(RULES_PATH, { waitUntil: 'domcontentloaded' });
      await expect(p.getByRole('columnheader', { name: 'Status', exact: true })).toBeVisible({ timeout: UI_READY });
    };

    try {
      await test.step('gateway: the org provider is pushed (precondition)', async () => {
        await expect.poll(async () => Object.keys((await admin.orgConfig(owner.organizationId))?.providers ?? {}),
          PUSH_VISIBLE).toContain(providerName);
      });

      await test.step('UI: Alerting, a webhook channel and two rules, per-org alerting left off, Save & Activate', async () => {
        const dialog = await openTab(page, 'Alerting');
        await dialog.getByLabel('Enable per-org alerting', { exact: true }).check();
        await dialog.getByPlaceholder('Channel name').fill(channelName);
        await dialog.getByRole('button', { name: 'Add Channel', exact: true }).click();
        await dialog.getByLabel('URL', { exact: true }).fill(url);
        await dialog.getByRole('button', { name: 'Add Rule', exact: true }).click();
        await dialog.getByRole('button', { name: 'Add Rule', exact: true }).click();
        await fillRule(page, dialog, 0, { name: critical, cooldown: '1h', severity: 'Critical' });
        await fillRule(page, dialog, 1, { name: every, cooldown: '0s', severity: 'Info' });
        await dialog.getByLabel('Enable per-org alerting', { exact: true }).uncheck();
        await saveAndWait(page, dialog);
      }, { timeout: STEP_BUDGET });

      await test.step('UI: Alert Rules, opened afresh, shows both rules Alerting off', async () => {
        await openRules(page);
        await expect(await ruleColumn(page, 'Status')).toHaveText(['Alerting off', 'Alerting off'], { timeout: UI_READY });
        await expect(await ruleColumn(page, 'Rule')).toHaveText([critical, every], { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('UI: Alerting, per-org alerting on, Save & Activate', async () => {
        const dialog = await openTab(page, 'Alerting');
        await dialog.getByLabel('Enable per-org alerting', { exact: true }).check();
        await saveAndWait(page, dialog);
      }, { timeout: STEP_BUDGET });

      await test.step('gateway: the saves reached it: alerting on, both rules, the channel URL in clear', async () => {
        const [draft, enable] = patches.map((r) => r.postDataJSON()?.alerting);
        await expect.poll(async () => (await admin.orgConfig(owner.organizationId))?.alerting ?? null, PUSH_VISIBLE)
          .toEqual({ ...draft, ...enable });
      });

      await test.step('UI: Alert Rules, opened afresh, shows both rules Active', async () => {
        await openRules(page);
        await expect(await ruleColumn(page, 'Status')).toHaveText(['Active', 'Active'], { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('caller + sink: the first request sends one critical alert', async () => {
        await callGateway();
        await expect.poll(async () => (await alerts()).filter((a) => a.alert === critical)
          .map(({ alert, severity, metric }) => ({ alert, severity, metric })), ALERT_SENT)
          .toEqual([{ alert: critical, severity: 'critical', metric: 'request_count' }]);
      });

      await test.step('caller: two more requests', async () => {
        await callGateway();
        await callGateway();
      });

      await test.step('UI: Rate Limiting on at 1000 RPM, Save & Activate (a config push)', async () => {
        const dialog = await openTab(page, 'Rate Limiting');
        await dialog.getByLabel('Enable per-org rate limiting', { exact: true }).check();
        await dialog.getByLabel('Requests per Minute (RPM)', { exact: true }).fill('1000');
        await saveAndWait(page, dialog);
        // Precondition: the push reached the gateway.
        await expect.poll(async () => (await admin.orgConfig(owner.organizationId))?.rate_limiting?.global_rpm ?? null,
          PUSH_VISIBLE).toBe(1000);
      }, { timeout: STEP_BUDGET + PUSH_VISIBLE.timeout });

      await test.step('caller + sink: inside its cooldown the critical rule sent nothing more; the 0s rule fired on all four', async () => {
        await callGateway();
        const count = async () => {
          const all = await alerts();
          return { critical: all.filter((a) => a.alert === critical).length, every: all.filter((a) => a.alert === every).length };
        };
        // Precondition: the 0s rule's fourth alert has arrived.
        await expect.poll(async () => (await count()).every, ALERT_SENT).toBe(4);
        const counted = await count();
        await testInfo.attach('alerts', { contentType: 'application/json', body: JSON.stringify(await alerts(), null, 2) });
        expect(counted).toEqual({ critical: 1, every: 4 });
      });

      await test.step('UI wire: the saves sent exactly the console\'s patches', async () => {
        const rule = (name: string, cooldown: string, severity: string) => ({ name, metric: 'request_count', condition: '>=',
          threshold: 1, window: '1m', cooldown, severity, channels: [channelName] });
        expect(patches.map((r) => r.postDataJSON())).toEqual([
          { alerting: { enabled: false, channels: [{ name: channelName, type: 'webhook', url }],
            rules: [rule(critical, '1h', 'critical'), rule(every, '0s', 'info')] } },
          { alerting: { enabled: true } },
          { rate_limiting: { enabled: true, global_rpm: 1000 } },
        ]);
      });

      // Display only (B2), last: the severity and cooldown claims are #2's and #3's, and
      // a perturbed severity or cooldown must fail at those checks first.
      await test.step('UI: Alert Rules, opened afresh, shows each rule\'s severity and cooldown', async () => {
        await openRules(page);
        await expect(await ruleColumn(page, 'Rule')).toHaveText([critical, every], { timeout: UI_READY });
        await expect(await ruleColumn(page, 'Severity')).toHaveText(['critical', 'info'], { timeout: UI_READY });
        await expect(await ruleColumn(page, 'Cooldown')).toHaveText(['1h', '0s'], { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
    } finally {
      await admin.dispose();
      await caller.dispose();
      await context.close();
    }
  },
);
