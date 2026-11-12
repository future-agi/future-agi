import { test, expect } from '../../lib/walk/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { GatewayGridReader } from '../../lib/gateway-grid';
import { grantViaCommand, LedgerSeeder } from '../../lib/ledger-seed';
import { MANAGED_PROVIDER } from '../../lib/managed-mock';
import { walkGatewayProfile, NEW_ORG_DEFAULT_OFF_TAG } from '../../lib/profile';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';
import {
  accountRow,
  API,
  attemptRows,
  checked,
  configVersions,
  entryRows,
  EXACT_PRICING,
  exhaustedMessage,
  KeyCaller,
  logRows,
  MANAGED_CREDITS_ROUTE,
  moneyColumnsPreflight,
  moneySnapshot,
  PATHS,
  refusal,
  settled,
  stackLogs,
  UPDATE_CONFIG,
  usdToMicros,
  type Call,
} from '../../lib/walk/money';
import { billingNoAccessMessage } from '../../lib/walk/money-source';
import {
  BILLING_PAGE,
  CARD_TITLE,
  expectedLogCells,
  formatBalance,
  formatCostCell,
  formatExactCost,
  gridCells,
  horizontalOverflow,
  MODELS_OFF_NOTICE,
  openAttempts,
  openLog,
  OWNER_ADMIN_CAPTION,
  PROFILE_SETTINGS,
  settle,
  tickColumns,
  ui,
  UI_READY,
  walletUi,
  type ApiLogRow,
} from '../../lib/walk/money-ui';
import type { VerdictRow } from '../../lib/walk/receipt';

// Product 651ab5bb8ac3a6c54d82c59bdd5a1f1271049aec. See contract-rows-w01-w02.json.
// Stock memory ledger, real product money path, mock-llm only upstream. No purchase proof.
// Expected money is independent of the fixture inputs. R-LIT: runtime labels and
// BYOK credentials below are visibly synthetic e2e-w01 fixtures, never encoded values.
const CHAT = 'gpt-4o';
const MESSAGE = 'claude-fable-5-1-20260831';
const GOOGLE = 'google/gemma-2-27b-it';
const GEMINI = 'google/gemini-3.5-flash';
const GRANT = '1.000000';
const B_GRANT = '0.500000';
// Upper bounds: provision 150s + B settle 75s + 90 UI actions * 60s +
// 8 gateway syncs * 75s + 4 log polls * 60s + grant/logs 240s + headroom 180s.
const FLOW_TIMEOUT = (150 + 75 + 90 * 60 + 8 * 75 + 4 * 60 + 240 + 180) * 1_000;
test.use({ scopeRoster: { admin: true }, walkName: 'W01' });
test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'WALK-E2E-001: W01 one key spends exact credit, keeps BYOK free and isolates organizations',
  {
    tag: ['@flow', '@walk', '@walk-W01', NEW_ORG_DEFAULT_OFF_TAG],
    annotation: flowAnnotation({
      id: 'WALK-E2E-001',
      area: 'walks',
      userGoal:
        'An organization uses one gateway key for three vendors on fixture credit, with exact micro-dollar charges and no cross-organization access',
      steps: [
        'mint a key in the console, inspect the one-time reveal and its masked prefix after reload',
        'observe credits OFF, enable through the Admin API, observe the zero-credit refusal and grant one fixture dollar',
        'send five managed plain and streamed calls, pin Gemini refusals, and exercise BYOK plus refused cross-mode and allowed same-mode fallback',
        'compare AG Grid logs, attempt drawers, Models balance and Billing figures with API and storage',
        'check Member and Viewer denial in fresh Billing contexts and Owner B isolation',
        'cancel Turn off by keyboard, capture narrow and dark layouts, then disable credits and retain the balance',
      ],
      backendChecks: [
        'OFF is 403 future_agi_credits_off; zero credit is 402 managed_credits_exhausted with the product message',
        'enable adds one active config version; the fixture grant is one entry and one account-version increment',
        'managed charges are exactly 92, 92, 441, 441 and 9 micros; gateway spent is 1075, available 998925 and held zero',
        'memory outbox has five pending settlements and PostgreSQL has only the grant; Redis is explicitly absent',
        'BYOK has zero markup and no ledger change; fallback attempt sets are primary-error only or primary-error plus request_fallback-success',
        'Member config write is 403; Owner B cannot retrieve any A log and its money snapshot is unchanged',
        'stack logs contain neither the minted key, its prefix nor either synthetic BYOK credential',
      ],
      browserChecks: [
        'keys reveal once; log cells and attempt outcomes match the public API',
        'Member and Viewer stay on Billing with the no-access message and no managed-credit requests',
        'Turn off cancellation makes no config write; Models has no horizontal overflow at 390 pixels',
      ],
    }),
  },
  async ({ scopeActors: scopes, scopeProbe: probe, walk }, testInfo) => {
    const owner = scopes.ownerA;
    const admin = scopes.admin!;
    const ownerB = scopes.ownerB;
    const A = owner.organizationId;
    const B = ownerB.organizationId;
    const run = `${testInfo.project.name}-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const prompt = (name: string) => `e2e-w01-${run}-${name}`;
    const { receipt } = walk;
    const check = <T,>(id: string, lane: VerdictRow['lane'], where: string, fn: () => Promise<T> | T) =>
      checked(receipt, { feature: id, lane, where }, fn);
    const gw = await GatewayAdmin.open();
    const seeder = await LedgerSeeder.open();
    const upstream = new MockUpstream();
    const startedAt = new Date().toISOString();
    let caller: KeyCaller | undefined;
    let rawKey = '';
    let keyPrefix = '';
    const byok = {
      provider: `e2e-w01-byok-${run}`.slice(0, 60),
      model: `e2e-w01-byok-model-${run}`,
      key: `e2e-w01-byok-key-${run}`,
      scenario: `w01-byok-${run}`,
    };
    const failing = {
      provider: `e2e-w01-fail-${run}`.slice(0, 60),
      model: `e2e-w01-fail-model-${run}`,
      key: `e2e-w01-fail-key-${run}`,
      scenario: `w01-fail-${run}`,
    };
    const logsOf = async (actor = owner) =>
      (await actor.api.get<{ count: number; results: ApiLogRow[] }>(API.logs, { limit: 200 })).results;
    receipt.extra = {
      profile: walkGatewayProfile(),
      fixtures: ['operator grant; walk fixture, not a purchase'],
      owed: [
        'native managed usage',
        'cloud purchase and webhook',
        'default-ON variant',
        'personal-key contract',
        'Redis profile',
      ],
    };
    try {
      const inspection = walk.inspection();
      expect(inspection.error).toBeUndefined();
      expect(inspection.profile).toBe('stock');
      expect(walkGatewayProfile()).toBe('stock');
      await moneyColumnsPreflight(probe);
      expect((await gw.credits(A)).ledger).toBe('memory');
      expect((await seeder.grant(B, B_GRANT, `w01-b-${run}`)).status).toBe(201);
      const bState = async () => ({
        db: await moneySnapshot(probe, B),
        credits: await gw.credits(B),
        config: await gw.orgConfig(B),
      });
      const bBefore = await settled(bState);

      const keysContext = await walk.open(owner, 'owner-keys');
      const keysPage = await keysContext.newPage();
      await ui('Open Keys', () => keysPage.goto(PATHS.keys, { waitUntil: 'domcontentloaded' }));
      await ui('Create key', () => keysPage.getByRole('button', { name: 'Create key', exact: true }).click());
      const createDialog = keysPage.getByRole('dialog', { name: 'Create API key', exact: true });
      await ui('Name key', () => createDialog.getByRole('textbox', { name: /^Name/ }).fill(`e2e-w01-${run}`));
      const keyResult = await ui('Submit key', async () => {
        const response = keysPage.waitForResponse(
          (r) => r.request().method() === 'POST' && new URL(r.url()).pathname === API.keys,
        );
        await createDialog.getByRole('button', { name: 'Create', exact: true }).click();
        const r = await response;
        expect(r.status()).toBeLessThan(300);
        return (await r.json()).result as { key: string; gateway_key_id: string };
      });
      rawKey = keyResult.key;
      const reveal = keysPage.getByRole('dialog', { name: 'Copy your new key', exact: true });
      await check('W01-N01', 'ui', PATHS.keys, async () => {
        // OneTimeKeyReveal.jsx: Secret uses a code element, NOT a textbox.
        await expect(reveal.getByText('API key', { exact: true }).locator('..').locator('code')).toHaveText(rawKey, {
          timeout: UI_READY,
        });
        await keysPage.keyboard.press('Escape');
        await expect(reveal).toBeVisible();
        return true;
      });
      await ui('Finish key reveal', () => reveal.getByRole('button', { name: 'Done', exact: true }).click());
      const stored = await probe.pg<{ key_prefix: string }>(
        'SELECT key_prefix FROM agentcc_api_key WHERE organization_id = $1 AND gateway_key_id = $2',
        [A, keyResult.gateway_key_id],
      );
      expect(stored.length).toBe(1);
      keyPrefix = stored[0].key_prefix;
      expect(keyPrefix.length).toBeGreaterThanOrEqual(8);
      await ui('Reload Keys', () => keysPage.reload({ waitUntil: 'domcontentloaded' }));
      await check('W01-N01-MASK', 'ui', PATHS.keys, async () => {
        await expect(
          keysPage.getByText(keyPrefix.includes('…') || keyPrefix.endsWith('...') ? keyPrefix : `${keyPrefix}...`, {
            exact: true,
          }),
        ).toHaveCount(1, { timeout: UI_READY });
        expect((await keysPage.content()).includes(rawKey)).toBe(false);
        return true;
      });
      await keysContext.close();
      caller = await KeyCaller.open(rawKey);
      const call = caller;
      await check('W01-C01', 'gateway', 'POST /v1/chat/completions', async () => {
        await expect
          .poll(async () => refusal(await call.chat(CHAT, prompt('before'))), POLL.GATEWAY_SYNC)
          .toEqual({ status: 403, code: 'future_agi_credits_off', limit_source: undefined });
        return 403;
      });
      const versionsBefore = await configVersions(probe, A);
      const modelsContext = await walk.open(admin, 'admin-models');
      const models = await modelsContext.newPage();
      await ui('Open Models while OFF', () => models.goto(PATHS.models, { waitUntil: 'domcontentloaded' }));
      await check('W01-N02', 'ui', PATHS.models, async () => {
        await expect(models.getByText(MODELS_OFF_NOTICE, { exact: true })).toHaveCount(1, { timeout: UI_READY });
        await expect(models.getByLabel(CARD_TITLE, { exact: true })).toHaveCount(0);
        await expect(models.getByRole('dialog')).toHaveCount(0);
        return true;
      });
      expect((await scopes.send(admin, 'POST', API.updateConfig, { managed_models: { enabled: true } })).status).toBe(
        200,
      );
      await check('W01-N03', 'storage', 'agentcc_org_config', async () => {
        const after = await configVersions(probe, A);
        expect(after.length).toBe(versionsBefore.length + 1);
        expect(after.map((v) => v.is_active)).toEqual([...versionsBefore.map(() => false), true]);
        expect(after.at(-1)!.enabled).toBe(true);
        const account = (await accountRow(probe, A))!;
        expect([account.granted_total_micros, account.settled_spend_micros]).toEqual(['0', '0']);
        return after.length;
      });
      await expect.poll(async () => (await gw.orgConfig(A))?.managed_models?.enabled, POLL.GATEWAY_SYNC).toBe(true);
      await expect
        .poll(async () => {
          const account = (await accountRow(probe, A))!;
          return account.gateway_acked_version === account.version;
        }, POLL.GATEWAY_SYNC)
        .toBe(true);
      await ui('Reload enabled Models', () => models.reload({ waitUntil: 'domcontentloaded' }));
      await expect(models.getByLabel(CARD_TITLE, { exact: true })).toBeChecked({ timeout: UI_READY });
      await expect(models.getByText('Enabled', { exact: true })).toBeVisible();
      await modelsContext.close();
      const exhausted = exhaustedMessage();
      const refused: Call[] = [];
      await check('W01-C02', 'gateway', 'POST /v1/chat/completions', async () => {
        await expect
          .poll(async () => {
            const c = await call.chat(CHAT, prompt('empty'));
            refused.push(c);
            return { ...refusal(c), message: c.body?.error?.message };
          }, POLL.GATEWAY_SYNC)
          .toEqual({
            status: 402,
            code: 'managed_credits_exhausted',
            limit_source: 'agentcc_credits',
            message: exhausted,
          });
        return refused.length;
      });
      const beforeGrant = (await accountRow(probe, A))!;
      grantViaCommand(A, GRANT, `w01-a-${run}`);
      await check('W01-N16', 'storage', 'usage_managedcreditentry', async () => {
        const entries = await entryRows(probe, A);
        expect(entries.map((e) => [e.kind, e.amount_micros])).toEqual([['grant', '1000000']]);
        const counts = await probe.pg<{ n: string }>(
          'SELECT count(*)::text AS n FROM usage_managedcreditentry WHERE idempotency_key = $1',
          [entries[0].idempotency_key],
        );
        expect(counts.map((r) => r.n)).toEqual(['1']);
        const after = (await accountRow(probe, A))!;
        expect([
          BigInt(String(after.granted_total_micros)) - BigInt(String(beforeGrant.granted_total_micros)),
          BigInt(String(after.version)) - BigInt(String(beforeGrant.version)),
        ]).toEqual([1000000n, 1n]);
        return 1;
      });
      await expect
        .poll(async () => {
          const account = (await accountRow(probe, A))!;
          const c = await gw.credits(A);
          return {
            acked: account.gateway_acked_version === account.version,
            granted: c.granted_total_micros,
            available: c.available_micros,
          };
        }, POLL.GATEWAY_SYNC)
        .toEqual({ acked: true, granted: 1000000, available: 1000000 });
      receipt.verdict({
        feature: 'W01-C11',
        lane: 'redis',
        where: 'stock memory ledger',
        verdict: 'ABSENT',
        detail: { reason: 'LC6: optional Redis profile not applied; no Redis proof claimed' },
      });
      type Model = {
        id: string;
        state?: string;
        state_reason?: string;
        input_per_mtok?: number;
        output_per_mtok?: number;
        billed_input_per_mtok?: number;
        billed_output_per_mtok?: number;
      };
      const catalog = (await owner.api.get<{ result: { models: Model[] } }>(API.catalog)).result.models;
      await check('W01-N17', 'api', API.catalog, () => {
        const expected = [
          ['openai/gpt-4o', '2500000', '10000000'],
          ['anthropic/claude-fable-5.1', '10000000', '50000000'],
          [GOOGLE, '650000', '650000'],
        ];
        for (const [id, input, output] of expected) {
          const rows = catalog.filter((m) => m.id === id);
          expect(rows.length).toBe(1);
          expect([usdToMicros(rows[0].input_per_mtok), usdToMicros(rows[0].output_per_mtok)]).toEqual([input, output]);
          receipt.verdict({
            feature: `catalog billed prices ${id}`,
            lane: 'api',
            where: API.catalog,
            verdict: 'RECORDED',
            detail: { input: rows[0].billed_input_per_mtok, output: rows[0].billed_output_per_mtok },
          });
        }
        return expected.length;
      });
      const served: { name: string; id: keyof typeof EXACT_PRICING; call: Call }[] = [];
      for (const [name, id, send] of [
        ['chat', 'openai/gpt-4o', () => call.chat(CHAT, prompt('chat'))],
        ['chat-stream', 'openai/gpt-4o', () => call.chat(CHAT, prompt('chat-stream'), { stream: true })],
        ['messages', 'anthropic/claude-fable-5.1', () => call.messages(MESSAGE, prompt('messages'))],
        [
          'messages-stream',
          'anthropic/claude-fable-5.1',
          () => call.messages(MESSAGE, prompt('messages-stream'), { stream: true }),
        ],
        ['google-chat', GOOGLE, () => call.chat(GOOGLE, prompt('google-chat'))],
      ] as const) {
        const c = await send();
        served.push({ name, id, call: c });
        await check(`W01-N05-${name}`, 'gateway', 'managed wire and cost headers', () => {
          const text = c.streamed?.text ?? c.body?.choices?.[0]?.message?.content ?? c.body?.content?.[0]?.text;
          expect({ status: c.status, provider: c.provider, text, cost: c.cost }).toEqual({
            status: 200,
            provider: MANAGED_PROVIDER,
            text: `echo: ${prompt(name)}`,
            cost: name.endsWith('stream') ? '' : EXACT_PRICING[id].header,
          });
          if (c.streamed) expect([c.streamed.prompt, c.streamed.completion]).toEqual([7, 7]);
          return c.status;
        });
      }
      const gemini = await call.chat(GEMINI, prompt('gemini'));
      await check('W01-N06', 'gateway', 'Gemini chat refusal and catalogue', () => {
        expect(refusal(gemini)).toEqual({ status: 403, code: 'permission_denied', limit_source: undefined });
        expect(gemini.cost).toBe('');
        const listed = catalog.filter((m) => m.id === GEMINI);
        expect(listed.length).toBe(1);
        expect({
          state: listed[0].state,
          reason: listed[0].state_reason,
          priced: Object.hasOwn(listed[0], 'input_per_mtok'),
        }).toEqual({
          state: 'blocked',
          reason: 'Not available on Future AGI credits: supported pricing is unavailable. Use your own provider key.',
          priced: false,
        });
        return 403;
      });
      const native = await call.generateContent('gemini-3.5-flash', prompt('native'));
      await check('W01-N07', 'gateway', 'POST /v1beta/models/{model}:generateContent', () => {
        expect(refusal(native)).toEqual({ status: 403, code: 'permission_denied', limit_source: undefined });
        expect(native.cost).toBe('');
        return 403;
      });
      for (const [p, options] of [
        [byok, {}],
        [failing, { status: 503 }],
      ] as const) {
        await owner.api.post(API.providers, {
          provider_name: p.provider,
          credentials: { api_key: p.key },
          base_url: upstream.baseUrl(p.scenario, options),
          api_format: 'openai',
          models_list: [p.model],
          extra_config: { api_path_prefix: '/v1' },
        });
      }
      await expect
        .poll(
          async () =>
            Object.keys((await gw.orgConfig(A))?.providers ?? {})
              .filter((p) => [byok.provider, failing.provider].includes(p))
              .sort(),
          POLL.GATEWAY_SYNC,
        )
        .toEqual([byok.provider, failing.provider].sort());
      const moneyState = async () => ({
        credits: await gw.credits(A),
        account: await accountRow(probe, A),
        entries: await entryRows(probe, A),
      });
      const beforeByok = await moneyState();
      const ownCall = await call.chat(byok.model, prompt('byok'));
      expect(ownCall.status).toBe(200);
      await check('W01-N18', 'api', 'mock-llm scoped requests', async () => {
        expect(
          (await upstream.requests(byok.scenario)).map((r) => ({
            auth: r.headers.authorization,
            model: (r.body as any)?.model,
          })),
        ).toEqual([{ auth: `Bearer ${byok.key}`, model: byok.model }]);
        expect(await moneyState()).toEqual(beforeByok);
        return 1;
      });
      const fallback = await call.chat(failing.model, prompt('refused-fallback'), { models: [failing.model, CHAT] });
      await check('W01-C05', 'gateway', 'mixed-mode fallback', async () => {
        expect({
          ...refusal(fallback),
          provider: fallback.body?.error?.metadata?.provider,
          provider_status: fallback.body?.error?.metadata?.provider_status,
        }).toEqual({
          status: 502,
          code: 'provider_503',
          limit_source: undefined,
          provider: failing.provider,
          provider_status: 503,
        });
        expect(
          (await upstream.requests(failing.scenario)).map((r) => ({
            auth: r.headers.authorization,
            model: (r.body as any)?.model,
          })),
        ).toEqual([{ auth: `Bearer ${failing.key}`, model: failing.model }]);
        expect(await moneyState()).toEqual(beforeByok);
        return 502;
      });
      const allowed = await call.chat(failing.model, prompt('allowed-fallback'), {
        models: [failing.model, byok.model],
      });
      expect({
        status: allowed.status,
        provider: allowed.provider,
        text: allowed.body?.choices?.[0]?.message?.content,
      }).toEqual({ status: 200, provider: byok.provider, text: `echo: ${prompt('allowed-fallback')}` });
      const wantIds = [
        ...refused.filter((c) => c.status === 402),
        ...served.map((s) => s.call),
        ownCall,
        fallback,
        allowed,
        gemini,
        native,
      ].map((c) => c.requestId);
      await expect
        .poll(async () => {
          const ids = new Set((await logRows(probe, A)).map((r) => r.request_id));
          return wantIds.filter((id) => !ids.has(id));
        }, POLL.ASYNC_JOB)
        .toEqual([]);
      const rows = await logRows(probe, A);
      const row = (id: string) => {
        const found = rows.filter((r) => r.request_id === id);
        expect(found.length).toBe(1);
        return found[0];
      };
      const attemptsFor = async (id: string) =>
        (await attemptRows(probe, A))
          .filter((a) => a.request_id === id)
          .map(({ index, kind, outcome, provider }) => ({ index, kind, outcome, provider }));
      await check('W01-C03', 'storage', 'agentcc_request_log and agentcc_request_attempt', async () => {
        for (const c of refused.filter((c) => c.status === 402)) {
          const r = row(c.requestId);
          expect([r.status_code, r.billing_mode, usdToMicros(r.cost), r.error_message]).toEqual([
            402,
            'managed',
            '0',
            exhausted,
          ]);
          expect(await attemptsFor(c.requestId)).toEqual([]);
        }
        return true;
      });
      for (const servedRow of served)
        await check(`W01-N04-${servedRow.name}`, 'storage', 'agentcc_request_log', () => {
          const r = row(servedRow.call.requestId);
          const price = EXACT_PRICING[servedRow.id];
          expect({
            mode: r.billing_mode,
            tokens: [r.input_tokens, r.output_tokens],
            stream: r.is_stream,
            upstream: usdToMicros(r.cost_upstream),
            billed: usdToMicros(r.cost),
            markup: usdToMicros(r.cost_markup),
            clientIp: r.has_client_ip,
          }).toEqual({
            mode: 'managed',
            tokens: [7, 7],
            stream: servedRow.name.endsWith('stream'),
            upstream: price.upstream,
            billed: price.billed,
            markup: price.markup,
            clientIp: true,
          });
          return price;
        });
      await check('W01-C06', 'storage', 'agentcc_request_log', () => {
        const r = row(fallback.requestId);
        expect([
          r.billing_mode,
          r.is_stream,
          r.status_code,
          usdToMicros(r.cost_upstream),
          usdToMicros(r.cost),
          usdToMicros(r.cost_markup),
          fallback.cost,
          r.attempt_count,
          r.has_client_ip,
        ]).toEqual(['byok', false, 502, '0', '0', '0', '', 1, true]);
        return true;
      });
      await check('W01-C07', 'storage', 'agentcc_request_attempt', async () => {
        expect(await attemptsFor(fallback.requestId)).toEqual([
          { index: 1, kind: 'primary', outcome: 'error', provider: failing.provider },
        ]);
        return 1;
      });
      await check('W01-N08', 'storage', 'same-mode fallback and ledger', async () => {
        expect(await attemptsFor(allowed.requestId)).toEqual([
          { index: 1, kind: 'primary', outcome: 'error', provider: failing.provider },
          { index: 2, kind: 'request_fallback', outcome: 'success', provider: byok.provider },
        ]);
        for (const c of [ownCall, allowed]) {
          const r = row(c.requestId);
          expect([r.billing_mode, usdToMicros(r.cost_markup)]).toEqual(['byok', '0']);
          expect((await entryRows(probe, A)).filter((e) => e.request_id === c.requestId)).toEqual([]);
        }
        for (const p of [byok, failing])
          expect(
            (await upstream.requests(p.scenario)).map((r) => ({
              auth: r.headers.authorization,
              model: (r.body as any)?.model,
            })),
          ).toEqual(Array.from({ length: 2 }, () => ({ auth: `Bearer ${p.key}`, model: p.model })));
        expect(await moneyState()).toEqual(beforeByok);
        return 2;
      });
      await check('W01-C08', 'gateway', 'GET /-/orgs/{org}/credits', async () => {
        await expect
          .poll(async () => {
            const c = await gw.credits(A);
            return [c.held_micros, c.outbox_pending, c.spent_micros, c.available_micros];
          }, POLL.ASYNC_JOB)
          .toEqual([0, 5, 1075, 998925]);
        expect((await entryRows(probe, A)).map((e) => [e.kind, e.amount_micros])).toEqual([['grant', '1000000']]);
        return 1075;
      });
      const apiRows = await logsOf();
      for (const [name, response, outcomes] of [
        ['refused', fallback, ['error']],
        ['allowed', allowed, ['error', 'success']],
      ] as const) {
        const context = await walk.open(owner, `owner-logs-${name}`);
        const page = await context.newPage();
        await ui('Open Request Logs', () => page.goto(PATHS.logs, { waitUntil: 'domcontentloaded' }));
        await tickColumns(page);
        await check(`W01-N09-${name}`, 'ui', PATHS.logs, async () => {
          await expect
            .poll(() => GatewayGridReader.in(page).displayedRowCount(), { timeout: UI_READY })
            .toBe(apiRows.length);
          await expect.poll(() => gridCells(page), { timeout: UI_READY }).toEqual(expectedLogCells(apiRows));
          return apiRows.length;
        });
        const selected = apiRows.filter((r) => r.request_id === response.requestId);
        expect(selected.length).toBe(1);
        await openLog(page, selected[0]);
        const items = await openAttempts(page, outcomes.length);
        await check(name === 'refused' ? 'W01-C09' : 'W01-N08-UI-COUNT', 'ui', PATHS.logs, async () => {
          await expect(items).toHaveCount(outcomes.length);
          return outcomes.length;
        });
        await check(name === 'refused' ? 'W01-C10' : 'W01-N08-UI-OUTCOMES', 'ui', PATHS.logs, async () => {
          expect(await items.evaluateAll((elements) => elements.map((el) => el.getAttribute('data-outcome')))).toEqual([
            ...outcomes,
          ]);
          return outcomes;
        });
        await receipt.shot(page, `logs-${name}`);
        await context.close();
      }
      const creditContext = await walk.open(owner, 'owner-credit');
      const creditPage = await creditContext.newPage();
      const status = (await owner.api.get<{ result: { enabled: boolean; balance_usd: number } }>(API.status)).result;
      expect([status.enabled, usdToMicros(status.balance_usd)]).toEqual([true, '998925']);
      await ui('Open Models balance', () => creditPage.goto(PATHS.models, { waitUntil: 'domcontentloaded' }));
      await check('W01-N10', 'ui', PATHS.models, async () => {
        await expect(creditPage.getByText('Credit balance', { exact: true }).locator('..')).toHaveText(
          `Credit balance${formatBalance(status.balance_usd)}`,
          { timeout: UI_READY },
        );
        expect(formatBalance(status.balance_usd)).toBe('$0.998925');
        return true;
      });
      const summary = (await owner.api.get<{ result: any }>(API.credits)).result;
      await ui('Open Billing balance', () => creditPage.goto(BILLING_PAGE, { waitUntil: 'domcontentloaded' }));
      await check('W01-N19', 'ui', BILLING_PAGE, async () => {
        await expect(walletUi.credits(creditPage)).toBeVisible({ timeout: UI_READY });
        expect([usdToMicros(summary.pending_settlement_usd), usdToMicros(summary.gateway.available_usd)]).toEqual([
          '1075',
          '998925',
        ]);
        await expect(
          walletUi.figure(creditPage, 'Available').getByText(formatBalance(summary.available_usd), { exact: true }),
        ).toBeVisible();
        await expect(
          walletUi.figure(creditPage, 'Spent, settling').getByText(formatExactCost('0.001075'), { exact: true }),
        ).toBeVisible();
        expect(await GatewayGridReader.in(walletUi.ledger(creditPage)).column('kind')).toEqual(['Grant']);
        expect(await GatewayGridReader.in(walletUi.ledger(creditPage)).column('amount_usd')).toEqual([
          formatCostCell('1'),
        ]);
        return true;
      });
      await creditContext.close();
      for (const [role, actor, rowId] of [
        ['member', scopes.member, 'W01-C12'],
        ['viewer', scopes.viewer, 'W01-C13'],
      ] as const) {
        const modelContext = await walk.open(actor, `${role}-models`);
        const page = await modelContext.newPage();
        await ui(`${role} Models`, () => page.goto(PATHS.models, { waitUntil: 'domcontentloaded' }));
        await expect(page.getByLabel(CARD_TITLE, { exact: true })).toBeDisabled({ timeout: UI_READY });
        await expect(page.getByText(OWNER_ADMIN_CAPTION, { exact: true })).toBeVisible();
        await modelContext.close();
        const context = await walk.open(actor, `${role}-billing-fresh`);
        const billing = await context.newPage();
        const requests: string[] = [];
        billing.on('request', (request) => {
          if (MANAGED_CREDITS_ROUTE.test(new URL(request.url()).pathname)) requests.push(request.method());
        });
        await ui(`${role} Billing direct`, () => billing.goto(BILLING_PAGE, { waitUntil: 'domcontentloaded' }));
        await check(rowId, 'ui', BILLING_PAGE, async () => {
          await expect(billing.getByRole('heading', { name: 'Billing', exact: true, level: 1 })).toBeVisible({
            timeout: UI_READY,
          });
          await expect(billing.getByText(billingNoAccessMessage(), { exact: true })).toBeVisible();
          await settle(billing);
          expect(new URL(billing.url()).pathname).toBe(BILLING_PAGE);
          expect(requests).toEqual([]);
          await expect(walletUi.credits(billing)).toHaveCount(0);
          return true;
        });
        await context.close();
      }
      const beforeDenied = await configVersions(probe, A);
      expect(
        (await scopes.send(scopes.member, 'POST', API.updateConfig, { managed_models: { enabled: false } })).status,
      ).toBe(403);
      expect(await configVersions(probe, A)).toEqual(beforeDenied);
      await check('W01-N12', 'api', API.logs, async () => {
        const statuses: number[] = [];
        for (const r of apiRows) statuses.push((await scopes.send(ownerB, 'GET', `${API.logs}${r.id}/`)).status);
        expect(statuses).toEqual(apiRows.map(() => 404));
        expect(await logsOf(ownerB)).toEqual([]);
        return statuses.length;
      });
      const bContext = await walk.open(ownerB, 'owner-b');
      const bPage = await bContext.newPage();
      await ui('B Request Logs', () => bPage.goto(PATHS.logs, { waitUntil: 'domcontentloaded' }));
      await expect(bPage.getByText('No requests yet', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(GatewayGridReader.in(bPage).rows).toHaveCount(0);
      await ui('B Billing', () => bPage.goto(BILLING_PAGE, { waitUntil: 'domcontentloaded' }));
      await check('W01-N11', 'ui', BILLING_PAGE, async () => {
        await expect.poll(() => new URL(bPage.url()).pathname, { timeout: UI_READY }).toBe(PROFILE_SETTINGS);
        await expect(walletUi.credits(bPage)).toHaveCount(0);
        const own = (await ownerB.api.get<{ result: { entries: { kind: string; amount_usd: number }[] } }>(API.entries))
          .result.entries;
        expect(own.map((e) => [e.kind, usdToMicros(e.amount_usd)])).toEqual([['grant', '500000']]);
        return true;
      });
      await bContext.close();
      const offContext = await walk.open(admin, 'admin-off');
      const offPage = await offContext.newPage();
      const posts: string[] = [];
      offPage.on('request', (r) => {
        if (r.method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname)) posts.push('POST');
      });
      await ui('Admin Models', () => offPage.goto(PATHS.models, { waitUntil: 'domcontentloaded' }));
      const toggle = offPage.getByLabel(CARD_TITLE, { exact: true });
      const dialog = offPage.getByRole('dialog', { name: 'Turn off Future AGI credits?', exact: true });
      await ui('Focus enabled switch', () => toggle.focus());
      await ui('Space opens Turn off', () => offPage.keyboard.press('Space'));
      await expect(dialog).toBeVisible();
      await ui('Escape cancels', () => offPage.keyboard.press('Escape'));
      await expect(dialog).toBeHidden();
      await ui('Refocus switch', () => toggle.focus());
      await ui('Reopen Turn off', () => offPage.keyboard.press('Space'));
      await expect(dialog).toBeVisible();
      const cancel = dialog.getByRole('button', { name: 'Cancel', exact: true });
      for (let i = 0; i < 6 && !(await cancel.evaluate((el) => el === document.activeElement)); i++)
        await ui('Tab to Cancel', () => offPage.keyboard.press('Tab'));
      await expect(cancel).toBeFocused();
      await ui('Enter cancels', () => offPage.keyboard.press('Enter'));
      await expect(dialog).toBeHidden();
      await check('W01-N13', 'storage', 'agentcc_org_config', async () => {
        expect(posts).toEqual([]);
        expect(await configVersions(probe, A)).toEqual(beforeDenied);
        return true;
      });
      for (const [width, colorScheme] of [
        [390, 'light'],
        [390, 'dark'],
        [1440, 'dark'],
      ] as const) {
        await ui('Set viewport', () => offPage.setViewportSize({ width, height: 900 }));
        await ui('Set color scheme', () => offPage.emulateMedia({ colorScheme }));
        await settle(offPage);
        if (width === 390) expect(await horizontalOverflow(offPage)).toBeLessThanOrEqual(0);
        await receipt.shot(offPage, `models-${width}-${colorScheme}`);
      }
      const balance = await gw.credits(A);
      const account = (await accountRow(probe, A))!;
      await ui('Open Turn off', () => toggle.click());
      await ui('Confirm Turn off', async () => {
        const response = offPage.waitForResponse(
          (r) => r.request().method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname),
        );
        await dialog.getByRole('button', { name: 'Turn off', exact: true }).click();
        const r = await response;
        expect({ status: r.status(), body: r.request().postDataJSON() }).toEqual({
          status: 200,
          body: { managed_models: { enabled: false } },
        });
      });
      await expect(offPage.getByText('Future AGI credits turned off', { exact: true })).toBeVisible();
      await expect(offPage.getByText(MODELS_OFF_NOTICE, { exact: true })).toBeVisible();
      await offContext.close();
      await check('W01-C04', 'gateway', 'disabled key and retained money', async () => {
        await expect
          .poll(async () => refusal(await call.chat(CHAT, prompt('off'))), POLL.GATEWAY_SYNC)
          .toEqual({ status: 403, code: 'future_agi_credits_off', limit_source: undefined });
        const after = await gw.credits(A);
        const a = (await accountRow(probe, A))!;
        expect([
          after.granted_total_micros,
          after.available_micros,
          a.granted_total_micros,
          a.settled_spend_micros,
        ]).toEqual([
          balance.granted_total_micros,
          balance.available_micros,
          account.granted_total_micros,
          account.settled_spend_micros,
        ]);
        return true;
      });
      await check('W01-N14', 'storage', 'bin/e2e logs', () => {
        const { text, bytes } = stackLogs(startedAt);
        expect([rawKey, keyPrefix, byok.key, failing.key].map((value) => text.includes(value))).toEqual([
          false,
          false,
          false,
          false,
        ]);
        expect(bytes).toBeGreaterThan(0);
        return bytes;
      });
      await check('W01-N15', 'storage', 'org B money snapshot', async () => {
        expect(await bState()).toEqual(bBefore);
        return true;
      });
    } finally {
      await caller?.dispose();
      await seeder.dispose();
      await gw.dispose();
    }
  },
);
