import { request } from '@playwright/test';
import type { APIRequestContext, APIResponse } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { MANAGED_PROVIDER, MANAGED_SETTINGS } from '../../lib/managed-mock';
import { GatewayGridReader, GRID_VIEWPORT } from '../../lib/gateway-grid';
import { NEW_ORG_DEFAULT_TAG } from '../../lib/profile';

// Pinned off the running system. The stack declares the new-org default on
// (E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS=1, lib/profile.ts), so the first gateway
// key of a new organization writes its first org config with managed_models
// {"enabled": true} and pushes it, which creates its Future AGI credit account
// at zero (agentcc/views/api_key.py create -> services/managed_models_default.py
// ensure_first_org_config -> org_config_writer -> config_push.push_org_config ->
// _ensure_managed_account). Nobody turns the "Future AGI credits" switch on.
//
// The model names are the ones SDKs send: "gpt-4o" on chat completions and a
// dated Claude id on /v1/messages, as Claude Code sends it. The gateway reads
// each as the catalogue id it names (internal/server/managed_names.go, catalog
// Canonical) and calls e2e/stack/gateway.e2e.yaml's fagi-managed provider, the
// deterministic mock (e2e/stack/mock-llm/server.mjs: "echo: <last user
// message>", usage 7 prompt and 7 completion tokens, no reported cost). With no
// reported cost the gateway prices the call from the catalogue
// (plugins/cost/card.go) and bills it at that price + the 5% markup.
const KEYS_PATH = '/agentcc/api-keys/';
const STATUS_PATH = '/agentcc/managed-models/';
// managed_credits.py credit_summary and billing/useManagedCredits.js SUMMARY:
// Standalone has no cloud checkout, so this capability must be false.
const CREDIT_SUMMARY_PATH = '/agentcc/managed-credits/';
const CATALOG_PATH = '/agentcc/managed-models/catalog/';
const REQUEST_LOGS_PATH = '/agentcc/request-logs/';
const MODELS_PATH = '/dashboard/gateway/models';
const LOGS_PATH = '/dashboard/gateway/logs';
const CHAT_MODEL = 'gpt-4o';
const CHAT_CATALOGUE_ID = 'openai/gpt-4o';
// A dated Claude id as Anthropic spells it (catalog/canonical.go dotVersions:
// "5-1" reads as "5.1", the date names the snapshot's canonical_slug). Chosen
// because every endpoint the catalogue lists for it has one price
// (catalog/endpoints_snapshot.json), so the price a call is billed at when the
// upstream names no endpoint (plugins/cost/card.go endpointSetCard: the
// highest in the set) is the catalogue's own price.
const MESSAGES_MODEL = 'claude-fable-5-1-20260831';
const MESSAGES_CATALOGUE_ID = 'anthropic/claude-fable-5.1';
// mock-llm/server.mjs: the usage every chat completion reports.
const MOCK_USAGE = { prompt: 7, completion: 7 };
const MARKUP = MANAGED_SETTINGS.markup_percent / 100;
// The gateway counts money in micro-dollars and rounds each amount once.
const MICRO = 1e-6;
// refusals.go refusal(ReserveExhausted), never granted credit: the 402's message.
const EXHAUSTED_MESSAGE =
  'Your organization has no Future AGI credit. Credit purchase is not available on this self-hosted install, so credit is granted by its operator: ask them to grant Future AGI credit, or use your own provider keys on the Gateway Providers page.';
// ManagedModelsCard.jsx CARD_TITLE: the card's title and its switch's name.
const CARD_TITLE = 'Future AGI credits';
// The Future AGI credit seeded at the gateway, in micro-dollars: 1 USD.
const SEED_MICROS = 1_000_000;
// Above any version Django pushes (ManagedCreditAccount.version counts grants
// from 0), so a later push from Django leaves the seed in place.
const SEED_VERSION = 1_000_000_000;
// logs/columns/gatewayColumns.js, the Agent & billing columns this flow ticks.
// A request's cost is the built-in Cost column alone, the charged amount: no
// Upstream cost or Markup column splits it.
const BILLING_COLUMNS = ['Billing mode'];
const TABLE_COLUMNS = ['Model', 'Status', 'Cost', 'Tokens', ...BILLING_COLUMNS];
// utils/providerLabel.js MANAGED_MODE_LABEL.
const BILLING_LABEL = 'Future AGI credits';
// The config push on key creation, with the gateway's 60 s sync behind it.
const PUSH_VISIBLE = POLL.ASYNC_JOB;
// The gateway flushes request logs to Django in batches.
const LOG_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + seeding 15 + PUSH_VISIBLE 60 + LOG_VISIBLE 60
// + two UI steps of STEP_BUDGET 75 (150) + headroom 45.
const FLOW_TIMEOUT = 450_000;

interface CatalogModel { id: string; input_per_mtok?: number; output_per_mtok?: number }
interface GatewayError { error?: { code: string; type: string; message: string; metadata?: Record<string, unknown> } }
interface LogRow {
  id: string; model: string; status_code: number; billing_mode: string | null; is_error: boolean;
  error_message: string; cost: string | null; cost_upstream: string | null; cost_markup: string | null;
  input_tokens: number | null; output_tokens: number | null;
}

// utils/formatters.js formatCostCell: two significant figures under a dollar.
function formatCost(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? '-' : '';
  if (abs > 0 && abs < 0.0001) return `${sign}<$0.0001`;
  if (abs === 0) return '$0.00';
  if (abs >= 1) return `${sign}$${abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return `${sign}$${abs.toFixed(Math.max(2, Math.ceil(-Math.log10(abs)) + 1))}`;
}
const micros = (usd: string | number | null) => Math.round(Number(usd ?? 0) / MICRO);

// scopeActors signs its users up before the body runs, so its setup falls
// under the file's timeout, not the body's test.setTimeout: without this
// line it gets the 120 s project default.
test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-027: a new organization calls Future AGI models from two vendors with one key, billed at provider price plus 5%',
  {
    tag: ['@flow', NEW_ORG_DEFAULT_TAG],
    annotation: flowAnnotation({
      id: 'GW-E2E-027',
      area: 'gateway',
      userGoal:
        'A developer in a new organization calls models from two vendors with one Future AGI key, by the names their SDKs send, and sees each call billed at the provider price plus 5%',
      steps: [
        'create the organization\'s first gateway API key, with no provider added and no switch turned',
        'call gpt-4o on chat completions with the key while the organization has no Future AGI credit',
        'seed Future AGI credit for the organization at the gateway',
        'call gpt-4o on chat completions and a dated Claude id on /v1/messages with the same key',
        'open Request Logs, tick Billing mode, and read the rows',
        'open Models and read the Future AGI credits card',
      ],
      backendChecks: [
        'before the key the organization has no PG agentcc_org_config row; creating the key writes exactly one version, active, with managed_models.enabled true, and its managed-credit account at zero in PG usage_managedcreditaccount',
        'with no credit the key\'s gpt-4o call is refused with 402 managed_credits_exhausted and operator-grant or BYOK guidance, from the agentcc_credits limit, not as a model the key may not use; the credit summary reports billing_available false on Standalone',
        'with credit gpt-4o on chat and the dated Claude id on /v1/messages both answer 200 in their own API\'s format with the mock\'s echo, served by the Future AGI provider, chat as openai/gpt-4o',
        'each served call is stored in PG agentcc_request_log under the organization as billing mode managed, its upstream cost the catalogue price of openai/gpt-4o or anthropic/claude-fable-5.1 times the mock\'s usage, its cost the upstream cost times 1.05 and its markup the difference, equal to the x-agentcc-cost the call returned',
        'the request-logs API the table reads returns exactly the refused calls and the two served calls, and the organization\'s balance fell by exactly what they were billed',
        'Request Logs show exactly those rows, each with Future AGI credits billing and one cost, the billed amount, with no Upstream cost or Markup column',
        'the Models page shows the Future AGI credits switch on for the organization',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const org = owner.organizationId;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const chatPrompt = `e2e-gw27-chat-${suffix}`;
    const messagesPrompt = `e2e-gw27-messages-${suffix}`;

    const configRows = () => probe.pg<{ version: number; is_active: boolean; managed_models: Record<string, unknown> }>(
      `SELECT version, is_active, managed_models FROM agentcc_org_config
        WHERE organization_id = $1 AND NOT deleted ORDER BY version`, [org]);

    await test.step('storage: a new organization starts with no gateway org config', async () => {
      expect(await configRows()).toEqual([]);
    });

    const created = await owner.api.post<{ result: { key: string; gateway_key_id: string } }>(
      KEYS_PATH, { name: `e2e-gw27-${suffix}` });
    const rawKey = created.result.key;
    const catalog = (await owner.api.get<{ result: { models: CatalogModel[] } }>(CATALOG_PATH)).result.models;
    const price = (id: string) => {
      const m = catalog.find((model) => model.id === id);
      expect(m, `catalogue model ${id}`).toBeTruthy();
      return m!;
    };
    // The upstream cost the catalogue prices one mock call of `id` at.
    const upstreamUsd = (id: string) => {
      const m = price(id);
      return (MOCK_USAGE.prompt * m.input_per_mtok! + MOCK_USAGE.completion * m.output_per_mtok!) / 1e6;
    };
    const expectedUpstream: Record<string, number> = {
      [CHAT_MODEL]: upstreamUsd(CHAT_CATALOGUE_ID), [MESSAGES_MODEL]: upstreamUsd(MESSAGES_CATALOGUE_ID) };

    const gw: APIRequestContext = await request.newContext();
    const admin = await GatewayAdmin.open();
    const chat = () => gw.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
      headers: { Authorization: `Bearer ${rawKey}` },
      data: { model: CHAT_MODEL, messages: [{ role: 'user', content: chatPrompt }] },
    });
    const messages = () => gw.post(`${E2E.gatewayUrl}/v1/messages`, {
      headers: { Authorization: `Bearer ${rawKey}`, 'anthropic-version': '2023-06-01' },
      data: { model: MESSAGES_MODEL, max_tokens: 64, messages: [{ role: 'user', content: messagesPrompt }] },
    });
    const served: Record<string, { cost: string; modelUsed: string; provider: string }> = {};
    const evidence = (res: APIResponse) => ({ cost: res.headers()['x-agentcc-cost'] ?? '',
      modelUsed: res.headers()['x-agentcc-model-used'] ?? '', provider: res.headers()['x-agentcc-provider'] ?? '' });

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    await page.setViewportSize(GRID_VIEWPORT);
    try {
      await testInfo.attach('seeded', {
        contentType: 'application/json',
        body: JSON.stringify({ suffix, organizationId: org, gatewayKeyId: created.result.gateway_key_id,
          models: { [CHAT_MODEL]: CHAT_CATALOGUE_ID, [MESSAGES_MODEL]: MESSAGES_CATALOGUE_ID },
          expectedUpstream, seedMicros: SEED_MICROS, seedVersion: SEED_VERSION }, null, 2),
      });

      await test.step('storage: the first key turned Future AGI models on, with an account at zero', async () => {
        expect((await configRows()).map((row) => ({ version: row.version, is_active: row.is_active,
          enabled: row.managed_models.enabled })))
          .toEqual([{ version: 1, is_active: true, enabled: true }]);
        const accounts = await probe.pg<{ granted_total_micros: string; settled_spend_micros: string }>(
          `SELECT granted_total_micros, settled_spend_micros FROM usage_managedcreditaccount
            WHERE organization_id = $1`, [org]);
        expect(accounts.map((a) => [Number(a.granted_total_micros), Number(a.settled_spend_micros)])).toEqual([[0, 0]]);
      });

      // Every status the poll below saw, in order: the 402s among them are logged.
      const refusals: number[] = [];
      await test.step('gateway: with no credit the key\'s call is a 402 that explains operator grants or BYOK', async () => {
        const credits = (await owner.api.get<{ result: { billing_available: boolean } }>(CREDIT_SUMMARY_PATH)).result;
        expect(credits.billing_available).toBe(false);
        await expect
          .poll(async () => {
            const res = await chat();
            refusals.push(res.status());
            const body = (await res.json()) as GatewayError;
            return { status: res.status(), code: body.error?.code, message: body.error?.message,
              limit_source: body.error?.metadata?.limit_source };
          }, PUSH_VISIBLE)
          .toEqual({ status: 402, code: 'managed_credits_exhausted', message: EXHAUSTED_MESSAGE,
            limit_source: 'agentcc_credits' });
        await testInfo.attach('refusals', { contentType: 'application/json', body: JSON.stringify(refusals) });
      });
      // Logged refusals: every status the poll saw but a 403, which is refused
      // before the request is logged (GW-E2E-007). A 503 account_state_missing
      // between the config push and the account push would be logged too.
      const loggedRefusals = refusals.filter((status) => status !== 403);

      await test.step('seed: Future AGI credit for the organization at the gateway', async () => {
        const pushed = await admin.pushGrantedCredits(org,
          { granted_total_micros: SEED_MICROS, version: SEED_VERSION, settled_spend_floor_micros: 0 });
        expect({ applied: pushed.applied, available: pushed.available_micros })
          .toEqual({ applied: true, available: SEED_MICROS });
      });

      await test.step('gateway: one key serves gpt-4o on chat and a dated Claude id on /v1/messages', async () => {
        const c = await chat();
        const cBody = await c.json();
        expect({ status: c.status(), content: cBody.choices?.[0]?.message?.content,
          prompt: cBody.usage?.prompt_tokens, completion: cBody.usage?.completion_tokens })
          .toEqual({ status: 200, content: `echo: ${chatPrompt}`, prompt: MOCK_USAGE.prompt,
            completion: MOCK_USAGE.completion });
        served[CHAT_MODEL] = evidence(c);

        const m = await messages();
        const mBody = await m.json();
        expect({ status: m.status(), type: mBody.type, role: mBody.role, content: mBody.content,
          input: mBody.usage?.input_tokens, output: mBody.usage?.output_tokens })
          .toEqual({ status: 200, type: 'message', role: 'assistant',
            content: [{ type: 'text', text: `echo: ${messagesPrompt}` }],
            input: MOCK_USAGE.prompt, output: MOCK_USAGE.completion });
        served[MESSAGES_MODEL] = evidence(m);

        // Both served by the Future AGI provider; chat names the catalogue id it
        // sent upstream (x-agentcc-model-used). Which catalogue model served the
        // /v1/messages call is proved by its price in the next step.
        expect({ chat: [served[CHAT_MODEL].provider, served[CHAT_MODEL].modelUsed],
          messages: served[MESSAGES_MODEL].provider })
          .toEqual({ chat: [MANAGED_PROVIDER, CHAT_CATALOGUE_ID], messages: MANAGED_PROVIDER });
        await testInfo.attach('served', { contentType: 'application/json',
          body: JSON.stringify({ served, messagesAnswerModel: mBody.model }, null, 2) });
      });

      let logged: LogRow[] = [];
      await test.step('storage + API: each call is billed at the catalogue price plus 5%', async () => {
        await expect
          .poll(async () => (await owner.api.get<{ count: number }>(REQUEST_LOGS_PATH)).count, LOG_VISIBLE)
          .toBe(loggedRefusals.length + 2);
        logged = (await owner.api.get<{ results: LogRow[] }>(REQUEST_LOGS_PATH)).results;
        // One batch can carry them all, so compare them as a multiset: every
        // logged refusal the poll above got, and the two served calls.
        expect(logged.map((row) => JSON.stringify([row.model, row.status_code])).sort())
          .toEqual([...loggedRefusals.map((status) => [CHAT_MODEL, status]), [CHAT_MODEL, 200], [MESSAGES_MODEL, 200]]
            .map((row) => JSON.stringify(row)).sort());
        expect(logged.filter((row) => row.status_code === 402).map((row) =>
          [row.billing_mode, row.is_error, row.error_message]))
          .toEqual(loggedRefusals.filter((status) => status === 402).map(() => ['managed', true, EXHAUSTED_MESSAGE]));
        expect(logged.filter((row) => row.status_code === 200).map((row) => [row.billing_mode, row.is_error]))
          .toEqual([['managed', false], ['managed', false]]);

        const stored = await probe.pg<{ model: string; billing_mode: string; cost: string; cost_upstream: string;
          cost_markup: string }>(
          `SELECT model, billing_mode, cost, cost_upstream, cost_markup FROM agentcc_request_log
            WHERE organization_id = $1 AND status_code = 200 ORDER BY model`, [org]);
        expect(stored.map((row) => row.model)).toEqual([CHAT_MODEL, MESSAGES_MODEL].sort());
        for (const row of stored) {
          const upstream = micros(row.cost_upstream);
          const billed = micros(row.cost);
          const facts = {
            billing_mode: row.billing_mode,
            // Within one micro-dollar of the catalogue price times the mock's usage.
            upstreamIsCatalogue: Math.abs(upstream - expectedUpstream[row.model] / MICRO) <= 1,
            // The billed cost is the upstream cost times 1.05; each amount is
            // rounded to the micro-dollar on its own, so allow one.
            billedIsUpstreamPlus5: Math.abs(billed - upstream * (1 + MARKUP)) <= 1,
            markupIsTheDifference: Math.abs(micros(row.cost_markup) - (billed - upstream)) <= 1,
            headerIsTheBilledCost: Math.abs(micros(served[row.model].cost) - billed) <= 1,
          };
          expect(facts, `${row.model}: upstream ${row.cost_upstream}, billed ${row.cost}, markup ${row.cost_markup}`)
            .toEqual({ billing_mode: 'managed', upstreamIsCatalogue: true, billedIsUpstreamPlus5: true,
              markupIsTheDifference: true, headerIsTheBilledCost: true });
        }

        const spent = stored.reduce((sum, row) => sum + micros(row.cost), 0);
        const status = (await owner.api.get<{ result: { enabled: boolean; balance_usd: number } }>(STATUS_PATH)).result;
        expect({ enabled: status.enabled, balance: micros(status.balance_usd) })
          .toEqual({ enabled: true, balance: SEED_MICROS - spent });
      });

      await test.step('UI: Request Logs show every call with its Future AGI billing', async () => {
        await page.goto(LOGS_PATH, { waitUntil: 'domcontentloaded' });
        const grid = GatewayGridReader.in(page);
        await expect.poll(() => grid.displayedRowCount(), { timeout: UI_READY }).toBe(logged.length);
        await page.getByRole('button', { name: 'Columns', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: 'Choose columns' });
        await expect(dialog).toBeVisible({ timeout: UI_READY });
        for (const name of BILLING_COLUMNS) await dialog.getByRole('checkbox', { name, exact: true }).check();
        await page.keyboard.press('Escape');
        await expect(dialog).toBeHidden({ timeout: UI_READY });

        const headerTexts = await grid.headers();
        for (const name of TABLE_COLUMNS) expect(headerTexts).toContain(name);
        // As a set: rows flushed in one batch share their order with nothing.
        const expected = logged.map((row) => ({
          Model: row.model,
          Status: String(row.status_code),
          Cost: formatCost(Number(row.cost ?? 0)),
          Tokens: `${row.input_tokens ?? 0} / ${row.output_tokens ?? 0}`,
          'Billing mode': BILLING_LABEL,
        })).map((cells) => JSON.stringify(cells)).sort();
        expect(headerTexts).not.toContain('Upstream cost');
        expect(headerTexts).not.toContain('Markup');
        await expect
          .poll(async () => (await Promise.all(logged.map(async (_, index) => {
            const cells = await grid.rowByHeader(index);
            return JSON.stringify(Object.fromEntries(TABLE_COLUMNS.map((name) => [name, cells[name]])));
          }))).sort(), { timeout: UI_READY })
          .toEqual(expected);
      }, { timeout: STEP_BUDGET });

      await test.step('UI: the Future AGI credits switch is on without anyone turning it on', async () => {
        await page.goto(MODELS_PATH, { waitUntil: 'domcontentloaded' });
        await expect(page.getByLabel(CARD_TITLE, { exact: true })).toBeChecked({ timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
    } finally {
      await admin.dispose();
      await gw.dispose();
      await context.close();
    }
  },
);
