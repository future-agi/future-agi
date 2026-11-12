import { request, type Page } from '@playwright/test';
import { test, expect } from '../../lib/walk/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { walkGatewayProfile as gatewayProfile, PROFILE_SERVES_MCP, NEW_ORG_DEFAULT_OFF_TAG, newOrgDefaultDeclared } from '../../lib/profile';
import { RedisProbe } from '../../lib/redis-probe';
import { configSwitchVerdicts, orgConfigRows, orgSnapshot, priceFeedVerdict, zeroRowVerdicts } from '../../lib/walk/census';
import type { VerdictRow } from '../../lib/walk/receipt';
import { CENSUS_ROLES, CENSUS_VIEWPORTS, CENSUS_SCHEMES, observeCat2, expectedFirstConfigVersions,
  type CensusCatalogModel, type CensusPhase, type CensusRole, type CensusPage } from '../../lib/walk/census-contract';
import { censusCreditsRefusals } from '../../lib/walk/census-credits';
import { censusDigest, MANAGED_MODELS_OFF_NOTICE, MANAGED_MODELS_STATUS_PATH,
  type ManagedModelsOffObservation, type CensusUIRow, type CensusBackendRow } from '../../lib/walk/census-result';

// W00 / HR-JRN-03. Exactly one execution per run/profile/browser, consumed by
// J00.L1 through census-result.ts, never imported/copied as a second spec.
// Product provenance at 3184d9212b: managed_models_default.py:90-93 returns
// without writing on default-OFF; :98-103 writes one only for declared, fresh,
// gateway-offered orgs. J00's unconditional "exactly one" is NOT product truth.
// Locators: frontend/src/sections/gateway/models/ManagedModelsCard.jsx;
// keys/{KeyManagementSection,CreateKeyDialog}.jsx; routes: routes/paths.js.
const UI_READY = 60_000;
// ManagedModelsCard.jsx current/legacy switch names only: CatalogFacets.jsx
// also renders an Access checkbox named "Future AGI credits (<count>)".
const CREDITS_CONTROL_NAME = /^Future AGI credits(?:: one key for every model)?$/;
// BillingNoAccess.jsx: current copy and dsx3g/credits-copy-r1 @ 21ec1a674e.
// Keep W00 compatible without touching GW-E2E-028's separately owned member block.
const BILLING_NO_ACCESS = /^(?:Only an organization Owner or Admin, or a workspace admin, can open Billing\.|Ask an organization Owner or Admin for access to Billing\.)$/;
// BillingV2/useCentralBilling.js, BillingPage.jsx and useDeploymentMode.js.
const CREDITS_PATH = '/agentcc/managed-credits/';
const DEPLOYMENT_PATH = '/api/deployment-info/';
const PLATFORM_BILLING_PATH = '/usage/v2/billing-overview/';
const KEYS_PATH = '/dashboard/gateway/keys';
const PRICE_FEED_PATH = '/public/model-prices/v1.json';
const CATALOG_PATH = '/agentcc/managed-models/catalog/';
interface CensusPageSpec { id: CensusPage; path: string; feature: string; marker?: string; read?: 'checkbox' | 'present' }
const PAGES: readonly CensusPageSpec[] = [
  { id: 'models', path: '/dashboard/gateway/models', feature: 'managed_models' },
  { id: 'billing', path: '/dashboard/settings/billing', feature: 'billing' },
  { id: 'budgets', path: '/dashboard/gateway/budgets', feature: 'admission_caps', marker: 'Daily admission caps use UTC', read: 'present' },
  { id: 'mcp', path: '/dashboard/gateway/mcp', feature: 'mcp' },
  { id: 'security', path: '/dashboard/gateway/settings', feature: 'trace_content', marker: 'Export full tool span content', read: 'checkbox' },
  { id: 'integrations', path: '/dashboard/settings/integrations', feature: 'integrations' },
  { id: 'guardrails', path: '/dashboard/gateway/guardrails', feature: 'guardrails' },
];
// Chained ceiling: provisioning 150s + 2 phases x 4 roles x 7 page steps x
// (navigation/settle 60s + 4 viewport/scheme reads x UI_READY) + key 6 x
// UI_READY + 34 credits probes x 3 reads x 10s + 2 backend phases x 75s sync + finalization 120s.
const PAGE_BUDGET = 60_000 + CENSUS_VIEWPORTS.length * CENSUS_SCHEMES.length * UI_READY;
const CENSUS_TIMEOUT = 150_000 + 2 * CENSUS_ROLES.length * PAGES.length * PAGE_BUDGET + 6 * UI_READY + 34 * 3 * 10_000 + 2 * 75_000 + 120_000;
// No raw actor attachments, traces, screenshots or videos: the typed matrix is
// the value-free viewport/scheme evidence (key dialog and account UI hold IDs).
test.use({ scopeRoster: { admin: true }, scopeEvidence: 'digest', walkName: 'W00', trace: 'off', screenshot: 'off', video: 'off' });
test.describe.configure({ timeout: CENSUS_TIMEOUT });

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState('networkidle', { timeout: 20_000 }).catch(() => undefined);
}

test('WALK-E2E-000: W00 a fresh organization reads every launch gateway feature off, for every role, before and after its first key', {
  tag: ['@flow', '@walk', '@walk-W00', NEW_ORG_DEFAULT_OFF_TAG],
  annotation: flowAnnotation({
    id: 'WALK-E2E-000', area: 'walks',
    userGoal: 'Every role of a brand-new organization sees each launch gateway feature remain off until explicitly enabled; UI absence alone is never proof of OFF',
    steps: [
      'sign up Owner A and Owner B, and invite Admin, Member and Viewer into A',
      'as each of A\'s roles open Models, Billing, Budgets, MCP, Gateway Settings, Integrations and Guardrails',
      'read the neutral credits-OFF notice, absent switch and server OFF state, plus each feature marker at 1440 and 390 px, light and dark',
      'after each role-appropriate Billing page loads, verify the managed wallet stays absent without hiding cloud platform Billing',
      'open the gateway\'s public price feed anonymously',
      'as Owner A create the first gateway key in the console',
      'repeat every page and viewport/scheme census as Owner, Admin, Member and Viewer',
    ],
    backendChecks: [
      'before the key neither org has a PG agentcc_org_config version and the gateway holds no org config for A',
      'A has no row in usage_managedcreditaccount, usage_managedcreditentry, the MCP credential, connect, approval and tool-call tables, integrations_connection or the cap activation table, and no trace-content audit entry',
      'when the served credit ledger is Redis-backed, Redis holds no mc:{A} key and A is in neither mc:outbox_orgs nor mc:needs_push; an in-memory ledger reads ABSENT',
      'GET /-/config shows the public price feed as the profile declares it, and the feed route is not served on a profile that does not declare it',
      'on default-off profiles after the key A has exactly zero org config rows and active versions and a null gateway mirror; config switches stay off and mcp.search absent',
      'all 34 exact CAT-2 identities are absent or blocked-unpriced in both the API catalogue and gateway admin catalogue before and after the key',
      'after the first console key every CAT-2 credits request returns 403 permission_denied with zero mock upstream calls',
      'org B\'s rows, audit entries, gateway org config and Redis keys are identical at start and end',
      'a complete value-free result binds every role, phase, page and viewport/scheme to the run, profile, browser, hashed org set and attested stack',
    ],
    browserChecks: ['every Models cell proves managed OFF with an absent control, the exact visible neutral notice and a successful server readback of enabled false',
      'the loaded Billing destination has no AI Gateway credits region for any role; existing cloud platform Billing and member access refusal remain',
      'no browser request left the machine except the ASSETS-PENDING font and icon hosts'],
  }),
}, async ({ scopeActors: scopes, scopeProbe: probe, walk }, testInfo) => {
  test.setTimeout(CENSUS_TIMEOUT);
  const { receipt } = walk;
  const profile = gatewayProfile();
  const A = scopes.ownerA.organizationId;
  const B = scopes.ownerB.organizationId;
  const admin = await GatewayAdmin.open();
  const redis = new RedisProbe([A, B], undefined, '');
  const ui: CensusUIRow[] = [];
  const backend: CensusBackendRow[] = [];
  const verdicts = (rows: VerdictRow[]) => rows.forEach(row => receipt.verdict(row));
  // Kept as the existing value-free projection; walk-regressions executes it.
  receipt.extra = { profile, orgs: ['A', 'B'], roles: scopes.orgA().map(({ role, level }) => ({ role, level })) };
  try {
    await test.step('harness: the stack is the declared mock-only default-off profile', async () => {
      const inspection = walk.inspection();
      expect(inspection.error, 'managed-mock inspection').toBeUndefined();
      expect(inspection.profile).toBe(profile);
      expect(newOrgDefaultDeclared(), 'W00 is default-OFF; default-ON is a separate flow').toBe(false);
      expect(scopes.orgA().map(r => r.role)).toEqual([...CENSUS_ROLES]);
    });
    const bBefore = await test.step('org B: baseline', () => orgSnapshot(probe, admin, redis, B));
    expect(await orgConfigRows(probe, B)).toEqual([]);

    const backendCensus = async (phase: CensusPhase) => {
      const start = receipt.verdicts.length;
      const rows = await orgConfigRows(probe, A);
      const count = phase === 'before-key' ? 0 : expectedFirstConfigVersions(newOrgDefaultDeclared());
      expect(rows, `${phase}: exact org config rows`).toHaveLength(count);
      expect(rows.filter(r => r.is_active), `${phase}: exact active versions`).toHaveLength(count);
      const mirrored = await admin.orgConfig(A);
      expect(mirrored, `${phase}: no saved config means null mirror`).toBeNull();
      receipt.verdict({ feature: 'org config versions', lane: 'storage', where: `${phase} agentcc_org_config`,
        verdict: 'OFF', detail: { rows: rows.length, active: rows.filter(r => r.is_active).length, expected: count } });
      verdicts(configSwitchVerdicts(rows[0] ?? null, 'storage', `${phase} active org config`));
      verdicts(configSwitchVerdicts(mirrored, 'gateway', `${phase} GET /-/orgs/{A}/config`));
      verdicts(await zeroRowVerdicts(probe, A));
      const served = await admin.mcpStatus(A);
      expect([200, 404]).toContain(served.status);
      const on = served.status === 200 && served.body.enabled === true;
      expect(on).toBe(PROFILE_SERVES_MCP[profile]);
      receipt.verdict({ feature: 'MCP gateway served state (operator profile)', lane: 'gateway', where: `${phase} GET /-/mcp/status`,
        verdict: served.status === 404 ? 'ABSENT' : 'RECORDED', detail: { status: served.status, enabled: on, declared: PROFILE_SERVES_MCP[profile] } });
      const ledger = (await admin.credits(A)).ledger;
      receipt.extra.creditLedger = ledger;
      if (ledger === 'redis') {
        expect(redis.keys(A)).toEqual([]);
        expect(redis.inSet('mc:outbox_orgs', A)).toBe(false);
        expect(redis.inSet('mc:needs_push', A)).toBe(false);
      }
      receipt.verdict({ feature: 'managed credits Redis state', lane: 'redis', where: `${phase} mc:{A}*, mc:outbox_orgs, mc:needs_push`,
        verdict: ledger === 'redis' ? 'OFF' : 'ABSENT', detail: { ledger } });
      // Exact envelope: managed_models.py:77-86; catalog_admin.go:48-103.
      const apiCatalog = (await scopes.ownerA.api.get<{ result: { models: CensusCatalogModel[] } }>(CATALOG_PATH)).result;
      const gatewayCatalog = await admin.catalog();
      const cat2 = { api: observeCat2(apiCatalog.models, 'api'), gateway: observeCat2(gatewayCatalog.models, 'gateway') };
      for (const lane of ['api', 'gateway'] as const) {
        const blockedUnpriced = cat2[lane].filter(row => row.disposition === 'blocked-unpriced').length;
        receipt.verdict({ feature: 'CAT-2 exact 34 IDs default-off', lane, where: `${phase} catalogue`, verdict: 'OFF',
          detail: { checked: 34, absent: 34 - blockedUnpriced, blockedUnpriced } });
      }
      const declaredOn = profile === 'pricefeed';
      const price = priceFeedVerdict(await admin.config(), declaredOn);
      receipt.verdict({ ...price, where: `${phase} ${price.where}` });
      expect(price.verdict).not.toBe('ON');
      const anon = await request.newContext();
      try {
        const res = await anon.get(`${E2E.gatewayUrl}${PRICE_FEED_PATH}`);
        receipt.verdict({ feature: 'public_price_feed route', lane: 'gateway', where: `${phase} GET ${PRICE_FEED_PATH}`,
          verdict: res.status() === 200 ? (declaredOn ? 'RECORDED' : 'ON') : price.verdict === 'ABSENT' ? 'ABSENT' : 'OFF',
          detail: { status: res.status() } });
        if (!declaredOn) expect(res.status()).not.toBe(200);
      } finally { await anon.dispose(); }
      const context = await walk.anonymous(`${phase}-anonymous`);
      const page = await context.newPage();
      const res = await page.goto(`${E2E.gatewayUrl}${PRICE_FEED_PATH}`);
      if (!declaredOn) expect(res?.status()).not.toBe(200);
      await context.close();
      const snapshot = await orgSnapshot(probe, admin, redis, B);
      expect((snapshot.redis as { error?: string })?.error, 'org B Redis snapshot must be readable').toBeUndefined();
      expect(snapshot, `${phase}: org B byte-identical`).toEqual(bBefore);
      backend.push({ phase, configRows: rows.length, activeVersions: rows.filter(r => r.is_active).length,
        mirrorNull: mirrored === null, catalogues: { api: apiCatalog.models.length, gateway: gatewayCatalog.models.length, cat2 },
        orgBSnapshot: censusDigest(snapshot), verdicts: receipt.verdicts.slice(start) });
    };
    await test.step('backend before the key: exact config, zero feature rows and exact CAT-2 default-off', () => backendCensus('before-key'));

    const deployment = (await scopes.ownerA.api.get<{ result: { mode: string } }>(DEPLOYMENT_PATH)).result;
    expect(['cloud', 'oss', 'ee']).toContain(deployment.mode);
    const census = async (phase: CensusPhase) => {
      for (const { role, actor } of scopes.orgA()) {
        const label = `${phase}-${role}`;
        const context = await walk.open(actor, label);
        for (const p of PAGES) await test.step(`UI ${phase} ${role}: ${p.id}`, async () => {
          // Isolate each route from the preceding page's late navigation. Firefox
          // can abort the next goto (NS_BINDING_ABORTED) when a reused SPA tab
          // redirects while leaving Billing. Same auth context, no retry/catch.
          const page = await context.newPage();
          try {
            const billingAdmin = role === 'owner' || role === 'admin';
            // Register before navigation. A blank/error/loading page is not a
            // dark-wallet proof, and a successful summary is allowed while OFF.
            const summaryRead = p.id === 'billing' && billingAdmin ? page.waitForResponse(
              r => new URL(r.url()).pathname === CREDITS_PATH && r.request().method() === 'GET', { timeout: UI_READY }) : null;
            const platformRead = p.id === 'billing' && billingAdmin && deployment.mode === 'cloud' ? page.waitForResponse(
              r => new URL(r.url()).pathname === PLATFORM_BILLING_PATH && r.request().method() === 'GET', { timeout: UI_READY }) : null;
            await page.goto(p.path, { waitUntil: 'domcontentloaded', timeout: UI_READY });
            if (summaryRead) {
              const response = await summaryRead;
              expect(response.status()).toBe(200);
              expect((await response.json() as { result: { enabled: boolean } }).result.enabled).toBe(false);
            }
            if (platformRead) expect((await platformRead).status()).toBe(200);
            await settle(page);
            for (const viewport of CENSUS_VIEWPORTS) for (const scheme of CENSUS_SCHEMES) {
              await page.setViewportSize(viewport);
              await page.emulateMedia({ colorScheme: scheme });
              const start = receipt.verdicts.length;
              const where = `${label} ${p.path} ${viewport.width} ${scheme}`;
              if (p.id === 'models') {
                // Server projection is independent of DOM absence. Never derive
                // org enablement from deployment availability, money or a label.
                const status = (await scopes.ownerA.api.get<{ result: { available: boolean; enabled: boolean } }>(MANAGED_MODELS_STATUS_PATH)).result;
                if (status.enabled !== false) throw new Error('W00_MANAGED_MODELS_SERVER_NOT_OFF');
                if (status.available === false) {
                  await expect(page).toHaveURL(url => url.pathname === '/dashboard/gateway/models/auto', { timeout: UI_READY });
                  await expect(page.getByRole('heading', { level: 1, name: 'Auto routing' })).toBeVisible({ timeout: UI_READY });
                }
                // No absence-only receipt is permitted, including unavailable
                // deployments: without the notice this cell must fail closed.
                const notice = page.getByRole('alert').filter({ hasText: MANAGED_MODELS_OFF_NOTICE });
                await expect(notice).toBeVisible({ timeout: UI_READY });
                await expect(notice).toHaveText(MANAGED_MODELS_OFF_NOTICE, { timeout: UI_READY });
                // Include hidden/disabled and old-name elements: ANY switch fails.
                const checkboxes = page.getByRole('checkbox', { name: CREDITS_CONTROL_NAME, includeHidden: true });
                const switches = page.getByRole('switch', { name: CREDITS_CONTROL_NAME, includeHidden: true });
                await expect(checkboxes).toHaveCount(0, { timeout: UI_READY });
                await expect(switches).toHaveCount(0, { timeout: UI_READY });
                await expect(page.getByRole('button', { name: /^Turn on/, includeHidden: true })).toHaveCount(0, { timeout: UI_READY });
                const detail: ManagedModelsOffObservation = { kind: 'absent-with-notice', control: 'ABSENT',
                  notice: { present: true, text: MANAGED_MODELS_OFF_NOTICE },
                  server: { source: MANAGED_MODELS_STATUS_PATH, enabled: status.enabled } };
                receipt.verdict({ feature: 'managed_models', lane: 'ui', where, verdict: 'OFF', detail });
              } else if (p.id === 'billing') {
                if (!billingAdmin) {
                  // BillingNoAccess.jsx: retain member/viewer access behavior.
                  await expect(page.getByText(BILLING_NO_ACCESS)).toBeVisible({ timeout: UI_READY });
                } else if (deployment.mode === 'cloud') {
                  await expect(page.getByRole('heading', { level: 1, name: 'Billing', exact: true })).toBeVisible({ timeout: UI_READY });
                  await expect(page.getByText('Invoice history', { exact: true })).toBeVisible({ timeout: UI_READY });
                } else {
                  await expect(page).toHaveURL(url => url.pathname === '/dashboard/settings/profile-settings', { timeout: UI_READY });
                  await expect(page.getByText('Profile Details', { exact: true })).toBeVisible({ timeout: UI_READY });
                }
                await expect(page.getByRole('region', { name: 'AI Gateway credits', exact: true })).toHaveCount(0, { timeout: UI_READY });
                receipt.verdict({ feature: 'managed credit wallet', lane: 'ui', where, verdict: 'ABSENT', detail: { loaded: true } });
              } else if (p.id === 'mcp') {
                const visible = async (text: RegExp) => page.getByText(text).first().isVisible().catch(() => false);
                const on = await visible(/^MCP on$|^MCP gateway: Enabled$/);
                const off = await visible(/MCP is turned off|MCP is off for this organization|did not report MCP as enabled|No MCP server is configured/);
                const verdict = on ? 'ON' : off ? 'OFF' : 'ABSENT';
                receipt.verdict({ feature: 'mcp (ui)', lane: 'ui', where, verdict });
                expect(verdict).not.toBe('ON');
              } else if (p.marker) {
                const shown = await page.getByText(p.marker, { exact: false }).first().isVisible().catch(() => false);
                let verdict: VerdictRow['verdict'] = shown ? 'RECORDED' : 'ABSENT';
                if (shown && p.read === 'checkbox') verdict = await page.getByLabel(p.marker).isChecked() ? 'ON' : 'OFF';
                receipt.verdict({ feature: `${p.feature} (ui)`, lane: 'ui', where, verdict, detail: { shown } });
                expect(verdict).not.toBe('ON');
              } else {
                // These pages have no pinned OFF marker; record navigation, never
                // invent an OFF verdict. Storage checks carry the default proof.
                receipt.verdict({ feature: `${p.feature} page visited`, lane: 'ui', where, verdict: 'RECORDED' });
              }
              await receipt.icons(page, label, p.id, walk.boundary.iconHostsAborted(label));
              ui.push({ phase, role: role as CensusRole, page: p.id, viewport, scheme, verdicts: receipt.verdicts.slice(start) });
            }
          } finally { await page.close(); }
        }, { timeout: PAGE_BUDGET });
        await context.close();
      }
    };
    await census('before-key');
    expect(await orgConfigRows(probe, A), 'page reads must not create an org config').toEqual([]);
    const rawKey = await test.step('UI: Owner creates the first gateway key in the console', async () => {
      const context = await walk.open(scopes.ownerA, 'key-owner');
      const page = await context.newPage();
      await page.goto(KEYS_PATH, { waitUntil: 'domcontentloaded' });
      const create = page.getByRole('button', { name: 'Create Key' });
      await expect(create).toBeEnabled({ timeout: UI_READY });
      await create.click();
      const dialog = page.getByRole('dialog', { name: 'Create API Key' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('textbox', { name: /^Name/ }).fill(`e2e-w00-${testInfo.project.name}-${Date.now().toString(36)}`);
      const created = page.waitForResponse(r => r.request().method() === 'POST' && /\/agentcc\/api-keys\/$/.test(new URL(r.url()).pathname));
      await dialog.getByRole('button', { name: 'Create', exact: true }).click();
      const response = await created;
      expect(response.status()).toBeLessThan(300);
      // api_key.py create serializer: key returned once, never attached/logged.
      const body = await response.json() as { result: { key: string } };
      if (typeof body.result?.key !== 'string' || !body.result.key) throw new Error('CAT2_CREDITS_KEY_MISSING');
      await expect(page.getByRole('dialog', { name: 'API Key Created' })).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText('Copy this key now. It will not be shown again.')).toBeVisible();
      await page.getByRole('button', { name: 'Done' }).click();
      await context.close();
      return body.result.key;
    }, { timeout: 6 * UI_READY });
    const credits = await test.step('CAT-2: every credits request is refused before upstream', () => censusCreditsRefusals(rawKey));
    for (const row of credits) receipt.verdict({ feature: 'CAT-2 credits refusal', lane: 'gateway', where: row.id, verdict: 'OFF', detail: row });
    await census('after-key');
    await test.step('backend after every role: exact count, null mirror, zero rows and unchanged B', () => backendCensus('after-key'));
    expect(receipt.verdicts.filter(v => v.verdict === 'ON'), 'features reading ON for a fresh organization').toEqual([]);
    walk.publishCensus({ orgs: { A: censusDigest(A), B: censusDigest(B) }, ui, backend, credits, verdicts: receipt.verdicts });
  } finally {
    receipt.extra = { ...receipt.extra, redisReads: redis.log.length };
    await admin.dispose();
  }
});
