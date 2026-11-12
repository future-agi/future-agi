import { test, expect } from '../../lib/walk/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { checkoutSessionId, LedgerSeeder, syntheticSession, topupFixture } from '../../lib/ledger-seed';
import { walkGatewayProfile, NEW_ORG_DEFAULT_OFF_TAG } from '../../lib/profile';
import type { ScopeActor } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import {
  accountRow,
  API,
  checked,
  consentRows,
  entryRows,
  KeyCaller,
  logRows,
  MANAGED_CREDITS_ROUTE,
  moneyColumnsPreflight,
  moneySnapshot,
  settled,
  stackLogs,
  tableExists,
  usdToMicros,
  type AccountRow,
} from '../../lib/walk/money';
import { billingNoAccessMessage, consentVersion, minimumTopupText } from '../../lib/walk/money-source';
import { BILLING_PAGE, settle, ui, UI_READY, walletUi } from '../../lib/walk/money-ui';
import { readWallet } from '../../lib/walk/wallet-read';
import type { VerdictRow } from '../../lib/walk/receipt';

// Product 651ab5bb8ac3a6c54d82c59bdd5a1f1271049aec. Standalone stock, venue-agnostic.
// Grants and test-mode fulfilment are fixtures, never a purchase/webhook/ON proof.
// R-LIT: runtime labels are visibly synthetic fixtures; checkoutSessionId hashes
// only those labels into a route-valid, visibly marked E2Ewalk namespace.
const GRANT = '1.000000';
const TOPUP = '2.00';
const B_GRANT = '0.500000';
const PENDING =
  'Your payment is still being processed. We will refresh the balance once more shortly. If it is still pending, refresh Billing later.';
const CREDITED = 'Credits added to your AI Gateway balance.';
const CANCELLED = 'Top-up cancelled. No payment was taken.';
// futureagi/agentcc/views/managed_credits.py AUTO_RELOAD_UNAVAILABLE(_MESSAGE) and
// gateway-credits/autoReloadConsent.js AUTO_RELOAD_UNAVAILABLE_TEXT: this release
// refuses every enabling save with this code, and Billing shows this sentence.
const AUTO_RELOAD_UNAVAILABLE = 'auto_reload_unavailable';
const AUTO_RELOAD_UNAVAILABLE_TEXT = 'Automatic reloads are not available in this release.';
// 150s provisioning + 75s B settle + 2*75s push + 60s logs + 110*60s UI +
// pending 20*1.5s + 240s fixture/log commands + 120s API + 180s headroom.
const FLOW_TIMEOUT = (150 + 75 + 2 * 75 + 60 + 110 * 60 + 20 * 1.5 + 240 + 120 + 180) * 1_000;
test.use({ scopeRoster: { admin: true }, walkName: 'W02' });
test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'WALK-E2E-002: W02 wallet projections, credited-once fixtures and fail-closed consent',
  {
    tag: ['@flow', '@walk', '@walk-W02', NEW_ORG_DEFAULT_OFF_TAG],
    annotation: flowAnnotation({
      id: 'WALK-E2E-002',
      area: 'walks',
      userGoal:
        'An Owner or Admin reads and exports the real credit ledger, observes honest purchase and auto-reload unavailability, and saves the low-balance alert',
      steps: [
        'opt in by API, grant fixture credit and make three managed calls costing exactly 625 micros',
        'read BillingV2 figures, ledger and statement as Owner, and download both CSV exports',
        'refuse checkout on this build, then visit pending, credited, repeated, legacy and cancelled return URLs using one test-mode top-up fixture',
        'verify self-hosted card-return URLs do not confirm a payment method or mutate money',
        'save the low-balance alert in the UI, see auto-reload disabled with the sentence that it is not available, have enabling saves with missing, stale and current consent and a below-minimum amount refused, then disable by API',
        'repeat reads and exports, checkout refusal, return checks, the refused enabling save and the disable as Admin',
        'deny Member and Viewer in fresh Billing contexts and on all money endpoints; isolate Owner B even when naming A',
      ],
      backendChecks: [
        'statement and CSV projections equal exact integer-micro ledger rows, and five summary GETs leave account metadata unchanged',
        'checkout is 503 billing_unavailable; status stays pending until one labelled top-up exists and repeats do not duplicate it',
        'every enabling save, with missing, stale or current consent or a below-minimum amount, is 400 auto_reload_unavailable and changes no balance, account or receipt',
        'Owner and Admin disabling saves succeed without consent, report auto-reload as not available and write no receipt',
        'opening Billing settings sends no settings save and changes no account or receipt',
        'Member and Viewer get five 403 responses each; Owner B never reads A money and its baseline stays unchanged',
        'stack logs contain neither synthetic checkout nor card session identifiers',
      ],
      browserChecks: [
        'BillingV2 regions, figures, ledger rows, statement lines and CSV controls match their public projections',
        'the auto-reload switch is visible and disabled, described by the sentence that automatic reloads are not available, with no agreement to give',
        'checkout notices and URL cleanup are UI facts, never purchase proof',
        'Member and Viewer remain on Billing with no-access copy and zero managed-credit requests',
        'Billing has no horizontal overflow at 390 pixels in light and dark layouts',
      ],
    }),
  },
  async ({ scopeActors: scopes, scopeProbe: probe, walk }, testInfo) => {
    const owner = scopes.ownerA;
    const admin = scopes.admin!;
    const ownerB = scopes.ownerB;
    const A = owner.organizationId;
    const B = ownerB.organizationId;
    const { receipt } = walk;
    const run = `${testInfo.project.name}-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const month = new Date().toISOString().slice(0, 7);
    const startedAt = new Date().toISOString();
    const session = checkoutSessionId(`w02-co-${run}`);
    const cardSession = checkoutSessionId(`w02-card-${run}`);
    const check = <T,>(id: string, lane: VerdictRow['lane'], where: string, fn: () => Promise<T> | T) =>
      checked(receipt, { feature: id, lane, where }, fn);
    const get = <T = any,>(actor: ScopeActor, path: string) => scopes.send<T>(actor, 'GET', path);
    const gw = await GatewayAdmin.open();
    const seeder = await LedgerSeeder.open();
    let caller: KeyCaller | undefined;
    const state = async () => ({
      account: await accountRow(probe, A),
      entries: await entryRows(probe, A),
      consents: await consentRows(probe, A),
    });
    // The stored account, ledger and receipts, plus the balance the gateway holds.
    const money = async () => ({ ...(await state()), available: (await gw.credits(A)).available_micros });
    const changed = (before: AccountRow, after: AccountRow) =>
      Object.keys(after)
        .filter((key) => before[key as keyof AccountRow] !== after[key as keyof AccountRow])
        .sort();
    receipt.extra = {
      profile: walkGatewayProfile(),
      fixtures: ['grant', 'test-mode topupFixture; walk fixture, not a purchase'],
      owed: [
        'cloud checkout and signed webhook',
        'card confirmation',
        'consent presentation in the console',
        'managed-ON acceptance',
      ],
    };
    try {
      const inspection = walk.inspection();
      expect(inspection.error).toBeUndefined();
      expect(inspection.profile).toBe('stock');
      expect(walkGatewayProfile()).toBe('stock');
      await moneyColumnsPreflight(probe);
      expect((await gw.credits(A)).ledger).toBe('memory');
      expect((await seeder.grant(B, B_GRANT, `w02-b-${run}`)).status).toBe(201);
      const bState = async () => ({
        db: await moneySnapshot(probe, B),
        credits: await gw.credits(B),
        config: await gw.orgConfig(B),
      });
      const bBefore = await settled(bState);
      expect((await scopes.send(owner, 'POST', API.updateConfig, { managed_models: { enabled: true } })).status).toBe(
        200,
      );
      const key = await owner.api.post<{ result: { key: string } }>(API.keys, { name: `e2e-w02-${run}` });
      caller = await KeyCaller.open(key.result.key);
      await expect.poll(async () => (await gw.orgConfig(A))?.managed_models?.enabled, POLL.GATEWAY_SYNC).toBe(true);
      expect((await seeder.grant(A, GRANT, `w02-a-${run}`)).status).toBe(201);
      await expect.poll(async () => (await gw.credits(A)).available_micros, POLL.GATEWAY_SYNC).toBe(1000000);
      const calls = [
        await caller.chat('gpt-4o', `e2e-w02-${run}-1`),
        await caller.chat('gpt-4o', `e2e-w02-${run}-2`),
        await caller.messages('claude-fable-5-1-20260831', `e2e-w02-${run}-3`),
      ];
      expect(calls.map((c) => c.status)).toEqual([200, 200, 200]);
      expect(calls.map((c) => usdToMicros(c.cost))).toEqual(['92', '92', '441']);
      await expect.poll(async () => (await logRows(probe, A)).length, POLL.ASYNC_JOB).toBe(3);
      await check('W02-N19', 'storage', 'agentcc_request_log', async () => {
        const rows = await logRows(probe, A);
        expect(rows.map((row) => usdToMicros(row.cost)).sort()).toEqual(['441', '92', '92']);
        expect(rows.map((row) => [row.billing_mode, row.input_tokens, row.output_tokens])).toEqual(
          Array.from({ length: 3 }, () => ['managed', 7, 7]),
        );
        return 625;
      });
      await readWallet(scopes, owner, 'owner', walk, probe, gw, A, month);
      await check('W02-N15', 'storage', 'summary GET account metadata', async () => {
        const pick = (a: AccountRow | null) => a && [a.version, a.updated_at, a.gateway_push_state];
        const before = pick(await accountRow(probe, A));
        for (let i = 0; i < 5; i++) expect((await get(owner, API.credits)).status).toBe(200);
        expect(pick(await accountRow(probe, A))).toEqual(before);
        return true;
      });
      const purchase = async (actor: ScopeActor, role: string) =>
        check(`W02-N01-${role}`, 'api', API.checkout, async () => {
          const before = await state();
          const r = await scopes.send<{ code: string; message: string }>(actor, 'POST', API.checkout, {
            amount_usd: '25.00',
            success_url: `http://localhost:3100${BILLING_PAGE}?topup=success&session_id={CHECKOUT_SESSION_ID}`,
            cancel_url: `http://localhost:3100${BILLING_PAGE}?topup=cancel`,
          });
          expect({ status: r.status, code: r.body.code, message: r.body.message }).toEqual({
            status: 503,
            code: 'billing_unavailable',
            message: 'Credit purchase is not installed in this build.',
          });
          expect(await state()).toEqual(before);
          if (await tableExists(probe, 'usage_stripeeventlog'))
            expect(
              await probe.pg<{ n: string }>(
                'SELECT count(*)::text AS n FROM usage_stripeeventlog WHERE organization_id = $1',
                [A],
              ),
            ).toEqual([{ n: '0' }]);
          return 503;
        });
      await purchase(owner, 'owner');
      await check('W02-N03', 'api', 'checkout invalid session shape', async () => {
        const r = await get(owner, `${API.checkout}${syntheticSession(`w02-invalid-${run}`)}/`);
        expect([r.status, r.body.code]).toEqual([404, 'checkout_session_not_found']);
        return 404;
      });
      const landing = async (
        actor: ScopeActor,
        label: string,
        phase: 'pending' | 'complete' | 'cancel',
        legacy = false,
      ) => {
        const before = await state();
        const context = await walk.open(actor, `${label}-landing`);
        const page = await context.newPage();
        const reads: Promise<{ method: string; status: number; result: unknown }>[] = [];
        page.on('response', (r) => {
          if (new URL(r.url()).pathname === `${API.checkout}${session}/`)
            reads.push(
              r.json().then((body) => ({ method: r.request().method(), status: r.status(), result: body.result })),
            );
        });
        const query =
          phase === 'cancel'
            ? 'topup=cancel'
            : legacy
              ? `checkout_session=${session}`
              : `topup=success&session_id=${session}`;
        try {
          await ui(`${label} checkout return`, () =>
            page.goto(`${BILLING_PAGE}?${query}&walk_probe=synthetic#ai-gateway-credits`, {
              waitUntil: 'domcontentloaded',
            }),
          );
          await check(`W02-N04-${label}`, 'ui', 'checkout return notice and cleanup', async () => {
            await expect(
              page.getByText(phase === 'pending' ? PENDING : phase === 'complete' ? CREDITED : CANCELLED, {
                exact: true,
              }),
            ).toBeVisible({ timeout: UI_READY });
            await expect.poll(() => new URL(page.url()).search, { timeout: UI_READY }).toBe('?walk_probe=synthetic');
            expect(new URL(page.url()).hash).toBe('#ai-gateway-credits');
            const completed = await Promise.all(reads);
            if (phase === 'cancel') expect(completed).toEqual([]);
            else {
              expect(completed.length).toBeGreaterThanOrEqual(1);
              expect(completed).toEqual(
                completed.map(() => ({
                  method: 'GET',
                  status: 200,
                  result:
                    phase === 'complete'
                      ? { session_id: session, status: 'complete', credited: true }
                      : { session_id: null, status: 'pending', credited: false },
                })),
              );
            }
            expect(await state()).toEqual(before);
            return completed.length;
          });
        } finally {
          await context.close();
        }
        await check(`W02-N02-${label}`, 'api', 'checkout status and exact top-up multiplicity', async () => {
          const r = await get(actor, `${API.checkout}${session}/`);
          const credited = phase !== 'pending';
          expect({ status: r.status, result: r.body.result }).toEqual({
            status: 200,
            result: credited
              ? { session_id: session, status: 'complete', credited: true }
              : { session_id: null, status: 'pending', credited: false },
          });
          expect(
            (await entryRows(probe, A))
              .filter((e) => e.kind === 'topup')
              .map((e) => [e.stripe_ref === session, e.amount_micros]),
          ).toEqual(credited ? [[true, '2000000']] : []);
          return credited;
        });
      };
      await landing(owner, 'pending', 'pending');
      const first = topupFixture(A, session, TOPUP);
      expect([first.created, first.stripe_ref === session]).toEqual([true, true]);
      await landing(owner, 'credited', 'complete');
      expect(topupFixture(A, session, TOPUP).created).toBe(false);
      await landing(owner, 'legacy-repeat', 'complete', true);
      await landing(owner, 'cancel', 'cancel');
      const cardReturn = async (actor: ScopeActor, role: string) => {
        for (const [label, query] of [
          ['added', `card=added&session_id=${cardSession}`],
          ['cancelled', 'card=cancelled'],
        ]) {
          const before = await state();
          const context = await walk.open(actor, `${role}-card-${label}`);
          const page = await context.newPage();
          const calls: string[] = [];
          page.on('request', (r) => {
            const path = new URL(r.url()).pathname;
            if (path.startsWith('/usage/v2/payment-methods') || path.startsWith(API.checkout)) calls.push(r.method());
          });
          try {
            await ui('Self-hosted card return', () =>
              page.goto(`${BILLING_PAGE}?${query}`, { waitUntil: 'domcontentloaded' }),
            );
            await expect(walletUi.credits(page)).toBeVisible({ timeout: UI_READY });
            await settle(page);
            await check(`W02-N16-${role}-${label}`, 'ui', 'self-hosted card return', async () => {
              expect(new URL(page.url()).search).toBe(`?${query}`);
              expect(calls).toEqual([]);
              expect(await state()).toEqual(before);
              return true;
            });
          } finally {
            await context.close();
          }
        }
      };
      await cardReturn(owner, 'owner');
      const disabledBody = {
        low_balance_threshold_usd: '0.25',
        auto_reload: { enabled: false, threshold_usd: null, amount_usd: null },
      };
      const formSave = async (value: string, label: string) => {
        const context = await walk.open(owner, label);
        const page = await context.newPage();
        try {
          await ui('Open credit settings', () => page.goto(BILLING_PAGE, { waitUntil: 'domcontentloaded' }));
          await expect(walletUi.lowBalance(page)).toBeVisible({ timeout: UI_READY });
          await ui('Edit low balance', () => walletUi.lowBalance(page).fill(value));
          const r = await ui('Save settings', async () => {
            const response = page.waitForResponse(
              (r) => new URL(r.url()).pathname === API.settings && r.request().method() === 'PUT',
            );
            await walletUi.saveSettings(page).click();
            return response;
          });
          const body = await r.json();
          let alerts: string[] = [];
          if (!r.ok()) {
            await expect(walletUi.settingsErrors(page)).toHaveCount(1, { timeout: UI_READY });
            alerts = await walletUi.settingsErrors(page).allTextContents();
          }
          return { status: r.status(), body, request: r.request().postDataJSON(), alerts };
        } finally {
          await context.close();
        }
      };
      await check('W02-N20', 'storage', 'UI low-balance-only save', async () => {
        const before = (await accountRow(probe, A))!;
        expect(await consentRows(probe, A)).toEqual([]);
        const result = await formSave('0.25', 'owner-low-balance');
        // The form sends USD amounts as JSON numbers; the API calls below send the same value as a string.
        expect({ status: result.status, body: result.request }).toEqual({
          status: 200,
          body: { ...disabledBody, low_balance_threshold_usd: 0.25 },
        });
        const after = (await accountRow(probe, A))!;
        expect(changed(before, after)).toEqual(['low_balance_threshold_micros', 'updated_at']);
        expect(after.low_balance_threshold_micros).toBe('250000');
        expect(await consentRows(probe, A)).toEqual([]);
        return true;
      });
      const version = consentVersion();
      const minText = minimumTopupText();
      const settings = (await get(owner, API.settings)).body.result;
      expect(usdToMicros(settings.min_topup_usd)).toBe(usdToMicros(minText));
      // Every enabling save is refused before any write, whatever consent or amount it carries.
      const refused = { status: 400, code: AUTO_RELOAD_UNAVAILABLE, message: AUTO_RELOAD_UNAVAILABLE_TEXT };
      for (const [id, consent, amount] of [
        ['W02-N05', undefined, minText],
        ['W02-N06', 'auto-reload-v0', minText],
        ['W02-N07', version, '1'],
      ] as const)
        await check(id, 'api', API.settings, async () => {
          const before = await money();
          const response = await scopes.send<{ code: string; message: string }>(owner, 'PUT', API.settings, {
            low_balance_threshold_usd: '0.25',
            auto_reload: {
              enabled: true,
              threshold_usd: '0.5',
              amount_usd: amount,
              ...(consent === undefined ? {} : { consent_version: consent }),
            },
          });
          expect({ status: response.status, code: response.body.code, message: response.body.message }).toEqual(
            refused,
          );
          expect(await money()).toEqual(before);
          return 400;
        });
      type SettingsResult = { result: { auto_reload: { enabled: boolean; disabled_reason: string } } };
      const enable = async (actor: ScopeActor, role: string, amount: string, threshold: string) =>
        check(`W02-N08-${role}`, 'storage', 'enabling with current consent: no account or receipt change', async () => {
          const before = await money();
          const userInfo = await get(actor, '/accounts/user-info/');
          expect(userInfo.status).toBe(200);
          expect(userInfo.body.id).toBe(actor.userId);
          const response = await scopes.send<{ code: string; message: string }>(actor, 'PUT', API.settings, {
            low_balance_threshold_usd: '0.25',
            auto_reload: { enabled: true, threshold_usd: threshold, amount_usd: amount, consent_version: version },
          });
          expect({ status: response.status, code: response.body.code, message: response.body.message }).toEqual(
            refused,
          );
          const after = await money();
          expect(changed(before.account!, after.account!)).toEqual([]);
          expect(after).toEqual(before);
          expect(after.consents).toEqual([]);
          const read = await scopes.send<SettingsResult>(actor, 'GET', API.settings);
          expect([
            read.status,
            read.body.result.auto_reload.enabled,
            read.body.result.auto_reload.disabled_reason,
          ]).toEqual([200, false, AUTO_RELOAD_UNAVAILABLE]);
          return 400;
        });
      const disable = async (actor: ScopeActor, role: string) =>
        check(`W02-N09-${role}`, 'storage', 'disabled authorization, retained receipts', async () => {
          const before = await money();
          const response = await scopes.send<SettingsResult>(actor, 'PUT', API.settings, disabledBody);
          expect([
            response.status,
            response.body.result.auto_reload.enabled,
            response.body.result.auto_reload.disabled_reason,
          ]).toEqual([200, false, AUTO_RELOAD_UNAVAILABLE]);
          const after = await money();
          // The save rewrites the same values; only its timestamp moves.
          expect(changed(before.account!, after.account!)).toEqual(['updated_at']);
          expect([
            after.account!.autoreload_enabled,
            after.account!.autoreload_consent_version,
            after.account!.autoreload_consent_at,
          ]).toEqual(['false', '', null]);
          expect({ ...after, account: null }).toEqual({ ...before, account: null });
          expect(after.consents).toEqual([]);
          return after.consents.length;
        });
      await enable(owner, 'owner', minText, '0.5');
      await check('W02-N10', 'storage', 'UI cannot turn auto-reload on', async () => {
        const before = await money();
        const context = await walk.open(owner, 'owner-auto-reload-ui');
        const page = await context.newPage();
        const saves: string[] = [];
        page.on('request', (r) => {
          if (new URL(r.url()).pathname === API.settings && r.method() !== 'GET') saves.push(r.method());
        });
        let seen: Record<string, unknown> = {};
        try {
          await ui('Open credit settings', () => page.goto(BILLING_PAGE, { waitUntil: 'domcontentloaded' }));
          const region = walletUi.settings(page);
          await expect(region).toBeVisible({ timeout: UI_READY });
          const control = walletUi.autoReload(page);
          await expect(control).toBeVisible();
          await expect(control).toBeDisabled();
          await expect(control).not.toBeChecked();
          await expect(control).toHaveAccessibleDescription(AUTO_RELOAD_UNAVAILABLE_TEXT);
          await expect(region.getByText(AUTO_RELOAD_UNAVAILABLE_TEXT, { exact: true })).toBeVisible();
          // The switch is the only checkbox: no terms or agreement are offered.
          await expect(region.getByRole('checkbox')).toHaveCount(1);
          await expect(region.getByRole('alert')).toHaveCount(0);
          await settle(page);
          seen = { disabled: await control.isDisabled(), checked: await control.isChecked(), saves: saves.length };
        } finally {
          await context.close();
        }
        receipt.verdict({ feature: 'W02-N10-UX', lane: 'ui', where: BILLING_PAGE, verdict: 'RECORDED', detail: seen });
        expect(saves).toEqual([]);
        expect(await money()).toEqual(before);
        return true;
      });
      await disable(owner, 'owner');
      await readWallet(scopes, admin, 'admin', walk, probe, gw, A, month);
      await purchase(admin, 'admin');
      await landing(admin, 'admin', 'complete');
      await cardReturn(admin, 'admin');
      await enable(admin, 'admin', '26', '0.75');
      await disable(admin, 'admin');
      for (const [role, actor, id] of [
        ['member', scopes.member, 'W02-C01'],
        ['viewer', scopes.viewer, 'W02-C02'],
      ] as const) {
        const context = await walk.open(actor, `${role}-billing-fresh`);
        const page = await context.newPage();
        const requests: string[] = [];
        page.on('request', (r) => {
          if (MANAGED_CREDITS_ROUTE.test(new URL(r.url()).pathname)) requests.push(r.method());
        });
        try {
          await ui(`${role} Billing direct`, () => page.goto(BILLING_PAGE, { waitUntil: 'domcontentloaded' }));
          await check(id, 'ui', BILLING_PAGE, async () => {
            await expect(page.getByRole('heading', { name: 'Billing', exact: true, level: 1 })).toBeVisible({
              timeout: UI_READY,
            });
            await expect(page.getByText(billingNoAccessMessage(), { exact: true })).toBeVisible();
            await settle(page);
            expect(new URL(page.url()).pathname).toBe(BILLING_PAGE);
            expect(requests).toEqual([]);
            await expect(walletUi.credits(page)).toHaveCount(0);
            return true;
          });
        } finally {
          await context.close();
        }
        await check(`W02-N13-${role}`, 'api', API.credits, async () => {
          const before = await state();
          const statuses = [
            (await get(actor, API.credits)).status,
            (await get(actor, API.entries)).status,
            (await get(actor, `${API.statement}?month=${month}`)).status,
            (await scopes.send(actor, 'PUT', API.settings, disabledBody)).status,
            (
              await scopes.send(actor, 'POST', API.checkout, {
                amount_usd: '25.00',
                success_url: 'http://localhost:3100/',
                cancel_url: 'http://localhost:3100/',
              })
            ).status,
          ];
          expect(statuses).toEqual([403, 403, 403, 403, 403]);
          expect(await state()).toEqual(before);
          return statuses;
        });
      }
      await check('W02-N17', 'api', 'Owner B naming org A', async () => {
        const asA = { ...ownerB, organizationId: A, workspaceId: owner.workspaceId };
        const statement = await get(asA, `${API.statement}?month=${month}`);
        const entries = await get(asA, API.entries);
        const bStatement = await get(ownerB, `${API.statement}?month=${month}`);
        const bEntries = await get(ownerB, API.entries);
        // A 200 must be the WHOLE B projection, not merely lack A's top-up marker.
        if (statement.status === 200) expect(statement.body.result).toEqual(bStatement.body.result);
        else expect([401, 403, 404].filter((status) => status === statement.status)).toEqual([statement.status]);
        if (entries.status === 200) expect(entries.body.result).toEqual(bEntries.body.result);
        else expect([401, 403, 404].filter((status) => status === entries.status)).toEqual([entries.status]);
        expect(
          bEntries.body.result.entries.map((e: { kind: string; amount_usd: number }) => [
            e.kind,
            usdToMicros(e.amount_usd),
          ]),
        ).toEqual([['grant', '500000']]);
        receipt.verdict({
          feature: 'W02-N17-STATUSES',
          lane: 'api',
          where: 'B naming A',
          verdict: 'RECORDED',
          detail: { statuses: [statement.status, entries.status] },
        });
        return true;
      });
      await check('W02-N14', 'storage', 'bin/e2e logs', () => {
        const { text, bytes } = stackLogs(startedAt);
        expect([text.includes(session), text.includes(cardSession)]).toEqual([false, false]);
        expect(bytes).toBeGreaterThan(0);
        return bytes;
      });
      await check('W02-N21', 'storage', 'org B money baseline', async () => {
        expect(await bState()).toEqual(bBefore);
        return true;
      });
      expect(
        new Date().toISOString().slice(0, 7),
        'UTC month must not change during this fresh-org statement walk',
      ).toBe(month);
    } finally {
      await caller?.dispose();
      await seeder.dispose();
      await gw.dispose();
    }
  },
);
