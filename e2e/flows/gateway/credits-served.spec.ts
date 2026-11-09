import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { GATEWAY_ADMIN_TOKEN } from '../../lib/gateway-admin';
import { GatewayGridReader, GRID_VIEWPORT } from '../../lib/gateway-grid';

// agentcc/urls_billing.py and serializers/managed_credits.py: real ledger reads
// and the staff/admin-token grant used only to seed these isolated local orgs.
const CREDITS = '/agentcc/managed-credits/';
// useManagedModels.js / serializers/contracts.py: organization enablement write.
const UPDATE_CONFIG = '/agentcc/gateways/default/update-config/';
// routes/paths.js and BillingCreditsPanel.jsx: central wallet and legacy link.
const BILLING = '/dashboard/settings/billing';
const SETTINGS = '/dashboard/gateway/settings';
// useDeploymentMode.js and utils/axios.js: platform Billing survives managed OFF.
const DEPLOYMENT = '/api/deployment-info/';
const PLATFORM_BILLING = '/usage/v2/billing-overview/';
// README Writing a flow: each first-paint/response wait gets its own 60s budget.
const UI_READY = 60_000;
// Provisioning 120s + config/grant/API/PG setup 120s + 14 UI units x 60s
// + teardown/headroom 120s. No gateway/CDC wait: these are acknowledged writes.
const FLOW_TIMEOUT = 1_200_000;
test.describe.configure({ timeout: FLOW_TIMEOUT });

// Managed-credit serializer envelopes; compare money at the numeric boundary.
interface Entry { id: string; kind: string; amount_usd: number; occurred_at: string }
interface Summary { enabled: boolean; available_usd: number; granted_usd: number; settled_spend_usd: number; billing_available: boolean }
interface Statement { month: string; closing_balance: number; credits_by_kind: { grant: number } }
interface Grant { result: { entry: Entry; created: boolean; idempotency_key: string } }

test('GW-E2E-028: an owner reads Future AGI credits in central Billing while a member cannot read the ledger', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'GW-E2E-028', area: 'gateway',
    userGoal: 'An organization owner reads its granted balance, history and statement only when managed credits are enabled for that organization, while a member cannot read its ledger.',
    steps: [
      'enable owner A through the real organization-config API as fixture setup, then seed its uniquely identified 1.23 grant',
      'open Settings > Billing and read the exact available balance, grant row and monthly statement',
      'verify the enabled wallet stays readable without purchases and Add credits follows the purchase capability',
      'open Gateway settings and follow Open Billing & credits',
      'open a legacy checkout return and keep its query and hash at central Billing',
      'open Billing as a member and see the access refusal without a ledger request',
      'explicitly disable owner B through the real config API and grant it 2.34 without enabling managed credits',
      'read B\'s successful OFF summary, then verify its new wallet and settings link stay absent without hiding existing cloud platform Billing',
    ],
    backendChecks: [
      'C1: the real config save, active PG config and summary agree on A\'s enabled true',
      'C2: summary, exact entries and monthly statement agree on A\'s uniquely identified 1.23 grant and closing balance',
      'C3: the enabled owner sees exactly 1.23 Available, one 1.23 grant row and 1.23 monthly closing balance',
      'C4: the enabled wallet is readable even when billing_available is false; only Add credits follows that capability',
      'C5: the settings link and legacy return reach central Billing with the exact href, query and hash',
      'C6: a member sends no managed-credits request while viewing BillingNoAccess',
      'C7: B retains its exact 2.34 grant with managed disabled in PG and summary, but has no wallet, credit link or browser entries/statement reads',
    ],
  }),
}, async ({ browser, request, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(FLOW_TIMEOUT);
  // Never send even the harness admin credential to a remote API.
  expect(['localhost', '127.0.0.1', '[::1]']).toContain(new URL(E2E.apiUrl).hostname);
  const owner = scopes.ownerA;
  const marker = `e2e-gw28-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const key = await owner.api.post<{ result: { gateway_key_id: string } }>('/agentcc/api-keys/', { name: marker });
  await testInfo.attach('seeded-key', { contentType: 'application/json',
    body: JSON.stringify({ marker, organizationId: owner.organizationId, gatewayKeyId: key.result.gateway_key_id }) });
  // C1: fixture setup, not the removed console opt-in journey. A grant alone
  // must never enable the new wallet (release-lead 793/880).
  const enabled = await owner.api.post<{ result: { version: number } }>(UPDATE_CONFIG, { managed_models: { enabled: true } });
  expect(enabled.result.version).toBeGreaterThan(0);
  const active = await probe.pg<{ managed_models: { enabled: boolean } }>(
    'SELECT managed_models FROM agentcc_org_config WHERE organization_id = $1 AND is_active AND NOT deleted', [owner.organizationId]);
  expect(active.map(row => row.managed_models.enabled)).toEqual([true]);
  expect((await owner.api.get<{ result: Summary }>(CREDITS)).result.enabled).toBe(true);
  await testInfo.attach('enabled-config', { contentType: 'application/json',
    body: JSON.stringify({ organizationId: owner.organizationId, version: enabled.result.version, enabled: true }) });
  const grant = await request.post(`${E2E.apiUrl}${CREDITS}grant/`, {
    headers: { Authorization: `Bearer ${GATEWAY_ADMIN_TOKEN}` },
    // ManagedCreditGrantRequestSerializer: no direct database mutation.
    data: { org_id: owner.organizationId, amount_usd: '1.23', note: marker, idempotency_key: marker },
  });
  expect(grant.status()).toBe(201);
  const granted = (await grant.json() as Grant).result;
  expect(granted.created).toBe(true);
  expect(granted.idempotency_key).toBe(marker);
  expect(granted.entry.id).toEqual(expect.any(String));
  expect(granted.entry.id.length).toBeGreaterThan(0);
  const month = new Date(granted.entry.occurred_at).toISOString().slice(0, 7);
  await testInfo.attach('seeded-credit', { contentType: 'application/json', body: JSON.stringify({
    marker, organizationId: owner.organizationId, entryIds: [granted.entry.id], month,
    version: enabled.result.version, enabled: true,
  }) });
  const summary = (await owner.api.get<{ result: Summary }>(CREDITS)).result;
  const entries = (await owner.api.get<{ result: { entries: Entry[]; next_cursor: string | null } }>(`${CREDITS}entries/`)).result;
  const statement = (await owner.api.get<{ result: Statement }>(`${CREDITS}statement/?month=${month}`)).result;
  expect(entries.entries.map(({ id, kind, amount_usd }) => [id, kind, Number(amount_usd)])).toEqual([[granted.entry.id, 'grant', 1.23]]);
  expect(entries.next_cursor).toBeNull();
  expect(summary.enabled).toBe(true);
  expect(Number(summary.available_usd)).toBe(1.23);
  expect(Number(summary.granted_usd)).toBe(1.23);
  expect(Number(summary.settled_spend_usd)).toBe(0);
  expect(statement.month).toBe(month);
  expect(Number(statement.closing_balance)).toBe(1.23);
  expect(Number(statement.credits_by_kind.grant)).toBe(1.23);
  const mode = (await owner.api.get<{ result: { mode: string } }>(DEPLOYMENT)).result.mode;
  expect(['cloud', 'oss', 'ee']).toContain(mode);
  // Required self-hosted row: a genuinely enabled wallet without purchases.
  if (mode !== 'cloud') expect(summary.billing_available).toBe(false);

  const context = await scopes.openContext(browser, owner);
  const page = await context.newPage();
  await page.setViewportSize(GRID_VIEWPORT);
  try {
    const credits = page.getByRole('region', { name: 'AI Gateway credits', exact: true });
    await test.step('C3: enabled owner receives a successful summary and visible wallet', async () => {
      const response = page.waitForResponse(r => new URL(r.url()).pathname === CREDITS && r.request().method() === 'GET', { timeout: UI_READY });
      await page.goto(BILLING, { waitUntil: 'domcontentloaded' });
      const loaded = await response;
      expect(loaded.status()).toBe(200);
      const read = (await loaded.json() as { result: Summary }).result;
      expect(read.enabled).toBe(true);
      expect(Number(read.available_usd)).toBe(1.23);
      await expect(credits).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('C3: Available is exactly the grant amount, not a repeated caption', async () => {
      await expect(credits.getByText('Available', { exact: true }).locator('..')
        .getByText('$1.23', { exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('C3: the exact ledger row and Amount agree', async () => {
      const grid = GatewayGridReader.in(credits);
      await expect.poll(() => grid.displayedRowCount(), { timeout: UI_READY }).toBe(1);
      await expect.poll(() => grid.columnHeaded('Amount'), { timeout: UI_READY }).toEqual(['$1.23']);
    }, { timeout: UI_READY });
    await test.step('C3: select the grant UTC month and read its exact closing Amount cell', async () => {
      const monthly = credits.getByRole('region', { name: 'Monthly statement', exact: true });
      // CreditStatement.jsx: Month combobox; format.js monthLabel uses UTC/en-US.
      const monthLabel = new Date(`${month}-01T00:00:00Z`).toLocaleString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
      const selector = monthly.getByRole('combobox', { name: 'Month' });
      await expect(selector).toBeVisible({ timeout: UI_READY });
      if ((await selector.innerText()).trim() !== monthLabel) {
        await selector.click();
        await page.getByRole('option', { name: monthLabel, exact: true }).click();
      }
      await expect(selector).toHaveText(monthLabel, { timeout: UI_READY });
      // CreditStatement.jsx columns: Line, Requests, Amount, Markup.
      await expect(monthly.getByRole('row', { name: /Closing balance/ }).getByRole('cell').nth(2))
        .toHaveText('$1.23', { timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('C4: purchase capability never hides the enabled wallet', async () => {
      await expect(credits).toBeVisible({ timeout: UI_READY });
      const purchase = credits.getByRole('button', { name: 'Add credits', exact: true });
      if (!summary.billing_available) await expect(purchase).toHaveCount(0, { timeout: UI_READY });
      else await expect(purchase).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    const link = page.getByRole('link', { name: 'Open Billing & credits', exact: true });
    await test.step('C5: Gateway settings links the central credit wallet', async () => {
      await page.goto(SETTINGS, { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { level: 1, name: 'Gateway settings' })).toBeVisible({ timeout: UI_READY });
      await expect(link).toHaveAttribute('href', `${BILLING}#ai-gateway-credits`, { timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('C5: follow the wallet link', async () => {
      await link.click();
      await expect(page).toHaveURL((url) => url.pathname === BILLING && url.hash === '#ai-gateway-credits', { timeout: UI_READY });
      await expect(credits).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('C5: legacy checkout return preserves its query and hash', async () => {
      await page.goto('/dashboard/gateway/billing?checkout_session=cs_x#ai-gateway-credits', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL((url) => url.pathname === BILLING && url.searchParams.get('checkout_session') === 'cs_x' && url.hash === '#ai-gateway-credits', { timeout: UI_READY });
    }, { timeout: UI_READY });

    const memberContext = await scopes.openContext(browser, scopes.member);
    try {
      const member = await memberContext.newPage();
      const calls: string[] = [];
      member.on('request', (req) => { if (new URL(req.url()).pathname.startsWith(CREDITS)) calls.push(req.method()); });
      await member.goto(BILLING, { waitUntil: 'domcontentloaded' });
      await expect(member.getByText(/Ask an organization Owner or Admin for access to Billing\./)).toBeVisible({ timeout: UI_READY });
      await expect(member.getByRole('region', { name: 'AI Gateway credits' })).toHaveCount(0);
      expect(calls).toEqual([]);
    } finally { await memberContext.close(); }
  } finally { await context.close(); }

  // C7: independent, positively funded OFF organization. Money is not enablement.
  const offOwner = scopes.ownerB;
  const offMarker = `${marker}-managed-off`;
  const disabled = await offOwner.api.post<{ result: { version: number } }>(UPDATE_CONFIG, { managed_models: { enabled: false } });
  expect(disabled.result.version).toBeGreaterThan(0);
  const offGrant = await request.post(`${E2E.apiUrl}${CREDITS}grant/`, {
    headers: { Authorization: `Bearer ${GATEWAY_ADMIN_TOKEN}` },
    data: { org_id: offOwner.organizationId, amount_usd: '2.34', note: offMarker, idempotency_key: offMarker },
  });
  expect(offGrant.status()).toBe(201);
  const offGranted = (await offGrant.json() as Grant).result;
  expect(offGranted.created).toBe(true);
  expect(offGranted.idempotency_key).toBe(offMarker);
  expect(offGranted.entry.id).toEqual(expect.any(String));
  expect(offGranted.entry.id.length).toBeGreaterThan(0);
  await testInfo.attach('seeded-credit-off', { contentType: 'application/json', body: JSON.stringify({
    marker: offMarker, organizationId: offOwner.organizationId, entryIds: [offGranted.entry.id],
    month: new Date(offGranted.entry.occurred_at).toISOString().slice(0, 7), version: disabled.result.version, enabled: false,
  }) });
  const offActive = await probe.pg<{ managed_models: { enabled: boolean } }>(
    'SELECT managed_models FROM agentcc_org_config WHERE organization_id = $1 AND is_active AND NOT deleted', [offOwner.organizationId]);
  expect(offActive.map(row => row.managed_models.enabled)).toEqual([false]);
  const offSummary = (await offOwner.api.get<{ result: Summary }>(CREDITS)).result;
  expect(offSummary.enabled).toBe(false);
  expect(Number(offSummary.available_usd)).toBe(2.34);
  expect(Number(offSummary.granted_usd)).toBe(2.34);
  expect(Number(offSummary.settled_spend_usd)).toBe(0);
  const offEntries = (await offOwner.api.get<{ result: { entries: Entry[]; next_cursor: string | null } }>(`${CREDITS}entries/`)).result;
  expect(offEntries.entries.map(({ id, kind, amount_usd }) => [id, kind, Number(amount_usd)]))
    .toEqual([[offGranted.entry.id, 'grant', 2.34]]);
  expect(offEntries.next_cursor).toBeNull();

  const offContext = await scopes.openContext(browser, offOwner);
  try {
    const offPage = await offContext.newPage();
    const downstream: string[] = [];
    offPage.on('request', req => {
      const path = new URL(req.url()).pathname;
      if (req.method() === 'GET' && [`${CREDITS}entries/`, `${CREDITS}statement/`].includes(path)) downstream.push(path);
    });
    // Observe BEFORE navigation, and allow the summary read that determines OFF.
    const readOffSummary = async () => {
      const response = await offPage.waitForResponse(r => new URL(r.url()).pathname === CREDITS && r.request().method() === 'GET', { timeout: UI_READY });
      expect(response.status()).toBe(200);
      const read = (await response.json() as { result: Summary }).result;
      expect(read.enabled).toBe(false);
      expect(Number(read.available_usd)).toBe(2.34);
      expect(Number(read.granted_usd)).toBe(2.34);
    };
    await test.step('C7: funded OFF owner receives a successful real browser summary', async () => {
      const read = readOffSummary();
      const platform = mode === 'cloud' ? offPage.waitForResponse(r => new URL(r.url()).pathname === PLATFORM_BILLING && r.request().method() === 'GET', { timeout: UI_READY }) : null;
      await offPage.goto(BILLING, { waitUntil: 'domcontentloaded' });
      await read;
      if (platform) expect((await platform).status()).toBe(200);
    }, { timeout: UI_READY });
    await test.step('C7: existing cloud Billing stays; non-cloud reaches Profile', async () => {
      if (mode === 'cloud') {
        await expect(offPage).toHaveURL(url => url.pathname === BILLING, { timeout: UI_READY });
        await expect(offPage.getByRole('heading', { level: 1, name: 'Billing', exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(offPage.getByText('Invoice History', { exact: true })).toBeVisible({ timeout: UI_READY });
      } else {
        await expect(offPage).toHaveURL(url => url.pathname === '/dashboard/settings/profile-settings', { timeout: UI_READY });
        // ProfileSettings.jsx: visible content, not merely a redirect URL.
        await expect(offPage.getByText('Profile Details', { exact: true })).toBeVisible({ timeout: UI_READY });
      }
    }, { timeout: UI_READY });
    await test.step('C7: settled OFF destination has no managed wallet or downstream reads', async () => {
      await expect(offPage.getByRole('region', { name: 'AI Gateway credits', exact: true })).toHaveCount(0, { timeout: UI_READY });
      await expect(offPage.getByRole('button', { name: 'Add credits', exact: true })).toHaveCount(0, { timeout: UI_READY });
      expect(downstream).toEqual([]);
    }, { timeout: UI_READY });
    await test.step('C7: full Gateway settings navigation reads the OFF summary again', async () => {
      const read = readOffSummary();
      await offPage.goto(SETTINGS, { waitUntil: 'domcontentloaded' });
      await read;
      await expect(offPage.getByRole('heading', { level: 1, name: 'Gateway settings' })).toBeVisible({ timeout: UI_READY });
      // GatewaySettingsSection.jsx mounts GeneralTab only after gateway/config
      // reads finish successfully; GeneralTab.jsx/SettingsPanel.jsx name this region.
      await expect(offPage.getByRole('tab', { name: 'General', exact: true })).toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
      await expect(offPage.getByRole('region', { name: 'Gateway Info', exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('C7: funded OFF owner has no managed billing panel or link', async () => {
      // BillingCreditsPanel.jsx existing test ID; no product selector was added.
      await expect(offPage.getByTestId('gateway-billing')).toHaveCount(0, { timeout: UI_READY });
      await expect(offPage.getByRole('link', { name: 'Open Billing & credits', exact: true })).toHaveCount(0, { timeout: UI_READY });
      expect(downstream).toEqual([]);
    }, { timeout: UI_READY });
  } finally { await offContext.close(); }
});
