import { request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { WebhookSink } from '../../lib/sinks';
import { POLL } from '../../lib/state-probe';
import { TemporalProbe } from '../../lib/temporal-probe';
import { MockUpstream } from '../../lib/upstream';

// Pinned off the source:
// - the three gateway schedules and what each runs
//   (futureagi/tfc/temporal/schedules/agentcc_alerts.py AGENTCC_ALERT_SCHEDULES:
//   agentcc-alerts-evaluator -> evaluate_agentcc_email_alerts_activity every 60 s,
//   agentcc-webhook-delivery -> deliver_agentcc_webhook_events_activity every 30 s;
//   agentcc_batches.py AGENTCC_BATCH_SCHEDULES: agentcc-batches ->
//   poll_agentcc_batches_activity every 60 s; all on queue "default"), in
//   tfc/temporal/schedules/__init__.py ALL_SCHEDULES, registered on a Standalone
//   boot by deploy/standalone/bin/bootstrap.py (register_temporal_schedules) and the
//   embedded worker (tfc/temporal/embedded.py a_register_schedules), and on a
//   Distributed boot by the postgres-schema-bootstrap job (docker-compose.distributed.yml
//   REGISTER_TEMPORAL_SCHEDULES "true"; futureagi/entrypoint.sh
//   should_register_temporal_schedules); each action starts TaskRunnerWorkflow with a
//   TaskRunnerInput naming the activity (tfc/temporal/schedules/manager.py
//   _build_schedule_for_config, task_queue = config.queue). Read through
//   e2e/lib/temporal-probe.ts with the Temporal CLI inside each stack's Temporal
//   container (Standalone `app`, Distributed `temporal`), so the flow holds on both.
// - the retry a delivery schedule performs: a refused delivery leaves the event
//   pending with next_retry_at = now + 30 s (agentcc/services/webhook_delivery.py
//   retry_delay, _record_failure); only agentcc-webhook-delivery delivers it then
//   (_due); success sets status "delivered" and counts the attempt (_deliver_one).
//   The first attempt is the immediate one log ingestion makes for a new
//   request.completed event (agentcc/services/log_ingestion.py
//   _dispatch_webhook_events). Table agentcc_webhook_event (models/webhook.py,
//   max_attempts 5).
// - wire bodies: AgentccProviderCredentialCreateSerializer (GW-E2E-002, harness/mock-upstream.spec.ts), POST /agentcc/api-keys/ { name } -> result.key / gateway_key_id
//   (GW-E2E-007), AgentccWebhookSerializer { name, url, secret, events, is_active }
//   (harness/webhook-sink.spec.ts).
// - the console: Webhooks, tab "Delivery Log" at /dashboard/gateway/webhooks/delivery
//   (frontend/src/sections/gateway/webhooks/WebhookManagementSection.jsx TAB_SLUGS),
//   the org's events in one table (webhooks/WebhookEventLog.jsx: Event Type, Status
//   chip, Attempts "n/max", Response Code, Last Attempt, Error "—" when none, Retry
//   only for failed or dead-letter events).
const SCHEDULES = {
  'agentcc-alerts-evaluator': { activityName: 'evaluate_agentcc_email_alerts_activity', intervalSeconds: 60, taskQueue: 'default' },
  'agentcc-batches': { activityName: 'poll_agentcc_batches_activity', intervalSeconds: 60, taskQueue: 'default' },
  'agentcc-webhook-delivery': { activityName: 'deliver_agentcc_webhook_events_activity', intervalSeconds: 30, taskQueue: 'default' },
} as const;
const SCHEDULE_IDS = Object.keys(SCHEDULES).sort();
const DELIVERY_LOG_PATH = '/dashboard/gateway/webhooks/delivery';
const DASH = '—';
// Django's push on save reaching the gateway's copy (org-config-secrets.spec.ts PUSH_VISIBLE).
const PUSH_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + probe calls 3 x 10 (30) + #3 SCHEDULE_TICK 150 +
// seeding 15 (+ the push precondition, PUSH_VISIBLE, inside it in practice) + one call 5 +
// #4 SCHEDULE_TICK 150 + Delivery Log step 75 + headroom 30 = 575 s, rounded up.
const FLOW_TIMEOUT = 600_000;

type Created<T> = { result: T };
type EventRow = { event_type: string; status: string; attempts: number };

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-013: the gateway\'s background schedules are registered and running on a fresh install, so a refused webhook delivery is retried on its own',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-013',
      area: 'gateway',
      userGoal:
        'On a fresh install, the gateway\'s background jobs (alert evaluation, webhook delivery retries and console batch reporting) are scheduled and running without anyone opening the console, so a webhook delivery that failed is retried on its own',
      steps: [
        'on a freshly booted stack, before anyone opens the console, look the gateway\'s schedules up in Temporal',
        'seed an org provider on the echo mock, an org key, and a webhook subscribed to Request Completed whose receiver refuses its first delivery',
        'send one request through the gateway with the org key',
        'open Webhooks, then Delivery Log',
      ],
      backendChecks: [
        'the three gateway schedules are registered in Temporal: agentcc-alerts-evaluator, agentcc-webhook-delivery and agentcc-batches',
        'each runs its activity on its interval on the default queue: evaluator 60 s, webhook delivery 30 s, batches 60 s',
        'none is paused and each has started at least one run since the boot',
        'a webhook delivery the receiver refused is delivered by the delivery schedule with no one acting: the event ends delivered on its second attempt, and the receiver saw exactly a refused then an accepted request for it',
      ],
      browserChecks: [
        'the Delivery Log shows the one Request Completed event delivered on attempt 2 of 5, with no Retry action and no error',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const temporal = new TemporalProbe();
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const scenario = `gw13-${suffix}`;
    const channel = `gw13-${suffix}`;
    const providerName = `e2e-gw13-${suffix}`;
    const model = `e2e-gw13-model-${suffix}`;
    const prompt = `e2e-gw13-${suffix}`;

    await test.step('Temporal: the three schedules are registered', async () => {
      const registered = (await temporal.listSchedules()).filter((id) => id in SCHEDULES);
      expect(registered).toEqual(SCHEDULE_IDS);
    });

    await test.step('Temporal: each runs its activity on its interval, on the default queue', async () => {
      const described: Record<string, unknown> = {};
      for (const id of SCHEDULE_IDS) {
        const d = await temporal.describeSchedule(id);
        described[id] = { activityName: d.activityName, intervalSeconds: d.intervalSeconds, taskQueue: d.taskQueue };
      }
      expect(described).toEqual(SCHEDULES);
    });

    await test.step('Temporal: none is paused, and each has started a run since the boot', async () => {
      await expect.poll(async () => {
        const state: Record<string, unknown> = {};
        for (const id of SCHEDULE_IDS) {
          const d = await temporal.describeSchedule(id);
          state[id] = { paused: d.paused, ran: d.actionCount >= 1 };
        }
        return state;
      }, POLL.SCHEDULE_TICK).toEqual(Object.fromEntries(SCHEDULE_IDS.map((id) => [id, { paused: false, ran: true }])));
    });

    // Seeding: the org's own provider on the mock, an org key, and a webhook on the
    // webhook sink whose channel refuses the first delivery.
    const upstream = new MockUpstream();
    const sink = new WebhookSink();
    const provider = await owner.api.post<Created<{ id: string }>>('/agentcc/provider-credentials/', {
      provider_name: providerName, credentials: { api_key: `sk-e2e-gw13-${suffix}` }, base_url: upstream.baseUrl(scenario),
      api_format: 'openai', models_list: [model], extra_config: { api_path_prefix: '/v1' },
    });
    const key = await owner.api.post<Created<{ key: string; gateway_key_id: string }>>('/agentcc/api-keys/',
      { name: `e2e-gw13-${suffix}` });
    const webhook = await owner.api.post<Created<{ id: string }>>('/agentcc/webhooks/', {
      name: `e2e-gw13-${suffix}`, url: sink.url(channel, { failFirst: 1 }), secret: `e2e-gw13-secret-${suffix}`,
      events: ['request.completed'], is_active: true,
    });
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, scenario, channel, model, organizationId: owner.organizationId,
        providerId: provider.result.id, gatewayKeyId: key.result.gateway_key_id, webhookId: webhook.result.id }, null, 2),
    });

    const admin = await GatewayAdmin.open();
    const caller = await request.newContext();
    let requestId = '';
    try {
      await test.step('caller: one request through the gateway with the org key', async () => {
        // Precondition: the provider create's push reached the gateway.
        await expect.poll(async () => Object.keys((await admin.orgConfig(owner.organizationId))?.providers ?? {}),
          PUSH_VISIBLE).toContain(providerName);
        const res = await caller.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
          headers: { Authorization: `Bearer ${key.result.key}` },
          data: { model, messages: [{ role: 'user', content: prompt }] },
        });
        const body = await res.json();
        expect(res.status(), JSON.stringify(body)).toBe(200);
        expect(body.choices[0].message.content).toBe(`echo: ${prompt}`);
        requestId = res.headers()['x-agentcc-request-id'] ?? '';
        expect(requestId).not.toBe('');
        await testInfo.attach('call', { contentType: 'application/json', body: JSON.stringify({ requestId }) });
      });

      await test.step('storage + sink: the refused delivery is retried by the schedule and delivered', async () => {
        await expect.poll(async () => {
          const events = await probe.pg<EventRow>(
            'SELECT event_type, status, attempts FROM agentcc_webhook_event WHERE webhook_id = $1 ORDER BY created_at',
            [webhook.result.id]);
          const received = (await sink.received(channel)).map((r) => ({ status: r.status,
            event: (r.body as { event?: string })?.event, request_id: (r.body as { request_id?: string })?.request_id }));
          return { events, received };
        }, POLL.SCHEDULE_TICK).toEqual({
          events: [{ event_type: 'request.completed', status: 'delivered', attempts: 2 }],
          received: [{ status: 500, event: 'request.completed', request_id: requestId },
            { status: 200, event: 'request.completed', request_id: requestId }],
        });
      });
    } finally {
      await admin.dispose();
      await caller.dispose();
    }

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    try {
      await test.step('UI: the Delivery Log shows the event delivered on its second attempt', async () => {
        await page.goto(DELIVERY_LOG_PATH, { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('columnheader', { name: 'Event Type', exact: true })).toBeVisible({ timeout: UI_READY });
        const rows = page.locator('tbody tr');
        await expect(rows).toHaveCount(1, { timeout: UI_READY });
        await expect(rows.first().locator('td'))
          .toHaveText(['request.completed', 'delivered', '2/5', '200', /\S/, DASH, ''], { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
    } finally {
      await context.close();
    }
  },
);
