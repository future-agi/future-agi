import { test, expect } from '../../lib/key-administrator';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/src/utils/axios.js keys endpoints; accounts/serializers/org_api_key.py.
const CREATE = '/accounts/key/generate_secret_key/';
const LIST = '/accounts/key/get_secret_keys/';
const DISABLE = '/accounts/key/disable_key/';
// tfc/urls.py and tfc/views/health.py: both header and Basic authentication.
const HEALTH = '/api/public/health';
// routes/paths.js and sections/keys/view/{ApiKeyBar,CreateApiKey,dev-keys-view}.jsx.
const PAGE = '/dashboard/keys';
// e2e/README.md: first-paint budget, not a CDC wait.
const UI_READY = 60_000;
// Exceed the request client's 30s default timeout so queueing cannot turn the
// seed into a past date before validation. The clock poll remains bounded.
const KEY_LIFETIME = 60_000;
// CreateApiKey uses a local calendar end-of-day; make the independent expected instant explicit.
test.use({ timezoneId: 'UTC' });

type CreatedKey = {
  key_id: string; key_name: string; api_key: string; secret_key: string;
  expires_at: string | null;
};
type Created = { status: boolean; result: CreatedKey };
type ListedKey = {
  id: string; key_name: string; expires_at: string | null;
  enabled: boolean; is_expired: boolean;
};

test('SET-E2E-003: optional-expiry keys stop access and remain distinguishable from disabled keys', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SET-E2E-003', area: 'settings',
    userGoal: 'An administrator creates optional-expiry keys and distinguishes automatically expired access from a manually disabled key while non-expiring and future keys keep working.',
    steps: [
      'seed a short-lived key and a manually disabled key through the public key API',
      'open Keys and create a non-expiring key without touching the optional date',
      'create a future calendar-expiry key and see both saved expiries without reloading',
      'authenticate non-expiring and future keys through headers and Basic auth',
      'after natural expiry, replay both auth paths and distinguish expired from disabled rejection',
      'reload Keys and read the exact Expired and Disabled rows',
      'warm collector authentication with non-expiring and far-future keys',
      'save past and near-future expiries in Django admin and refuse previously cached access at each boundary',
      'keep the unrelated non-expiring key usable and show the admin-edited keys as Expired',
    ],
    backendChecks: [
      'UI-created no-expiry and future keys persist with their exact optional expiry and actor organization.',
      'Non-expiring and future keys authenticate using headers and Basic auth; expired keys consistently return api_key_expired, distinct from manually disabled keys.',
      'The Keys list reports the exact minted expiry/status set and matches Never, future date, Expired and Disabled in the browser.',
      'Admin-saved expiry persists exactly and invalidates previously warmed collector credentials; a newly cached near-future expiry also fails after its boundary, while the no-expiry control works.',
    ],
  }),
}, async ({ page, actor, probe, request }, testInfo) => {
  test.setTimeout(900_000);
  // Existing 480s + six admin/UI 60s operations + 60s boundary headroom.
  const prefix = `e2e-key-expiry-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const names = Object.fromEntries(['never', 'future', 'expired', 'disabled']
    .map(kind => [kind, `${prefix}-${kind}`]));
  // The public serializer rejects past dates. Seed a valid short TTL, which ages
  // naturally during the real UI journey; no fake clock, SQL writes or fixed sleep.
  const shortExpiry = new Date(Date.now() + KEY_LIFETIME).toISOString();
  const expired = (await actor.api.post<Created>(CREATE, {
    key_name: names.expired, expires_at: shortExpiry,
  })).result;
  const disabled = (await actor.api.post<Created>(CREATE, {
    key_name: names.disabled,
  })).result;
  await actor.api.post(DISABLE, { key_id: disabled.key_id });
  await testInfo.attach('seeded-key-identities', {
    body: JSON.stringify({ organizationId: actor.organizationId,
      keys: [expired, disabled].map(k => ({ id: k.key_id, name: k.key_name, expires_at: k.expires_at })) }),
    contentType: 'application/json',
  });

  const tomorrow = new Date();
  tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
  const year = tomorrow.getUTCFullYear();
  const month = String(tomorrow.getUTCMonth() + 1).padStart(2, '0');
  const day = String(tomorrow.getUTCDate()).padStart(2, '0');
  const futureExpiry = `${year}-${month}-${day}T23:59:59.999Z`;
  const futureDateLabel = `${month}-${day}-${year}`;
  const keys: CreatedKey[] = [];

  await test.step('UI: optional expiry remains optional and is saved without a reload', async () => {
    await page.goto(PAGE, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('button', { name: 'Add API Key', exact: true }))
      .toBeVisible({ timeout: UI_READY });
    await page.getByPlaceholder('Search', { exact: true }).fill(prefix);
  }, { timeout: UI_READY });

  for (const kind of ['never', 'future']) {
    await test.step(`UI: create ${kind} key and read its exact expiry`, async () => {
      await page.getByRole('button', { name: 'Add API Key', exact: true }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('textbox', { name: 'Key name', exact: true }).fill(names[kind]);
      if (kind === 'future') {
        await dialog.getByRole('textbox', { name: 'Expires on (optional)', exact: true }).fill(`${month}/${day}/${year}`);
        await dialog.getByRole('textbox', { name: 'Key name', exact: true }).click();
      }
      const createdResponse = page.waitForResponse(r => r.url().endsWith(CREATE)
        && r.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Next', exact: true }).click();
      const response = await createdResponse;
      expect(response.status()).toBe(200);
      const body = await response.json() as Created;
      expect(body.status).toBe(true);
      const key = body.result;
      keys.push(key);
      await testInfo.attach(`created-${kind}-identity`, {
        body: JSON.stringify({ id: key.key_id, name: key.key_name, expires_at: key.expires_at }),
        contentType: 'application/json',
      });
      await dialog.getByRole('button', { name: 'Done', exact: true }).click();
      // DataTable.jsx uses MUI DataGrid; dev-keys-view pins getRowId and expires_at field.
      const row = page.locator(`[role="row"][data-id="${key.key_id}"]`);
      await expect(row.locator('[data-field="expires_at"]'))
        .toHaveText(kind === 'never' ? 'Never' : futureDateLabel, { timeout: UI_READY });
    }, { timeout: UI_READY });
  }

  await test.step('storage: optional expiry and organization are preserved exactly', async () => {
    const rows = await probe.pg<{ id: string; name: string; expires_at: Date | null; organization_id: string }>(
      `SELECT id, name, expires_at, organization_id FROM accounts_orgapikey
       WHERE organization_id = $1 AND id = ANY($2::uuid[]) AND deleted = false ORDER BY name`,
      [actor.organizationId, keys.map(k => k.key_id)],
    );
    expect(rows.map(r => ({ name: r.name, expires_at: r.expires_at?.toISOString() ?? null,
      organization: r.organization_id }))).toEqual([
      { name: names.future, expires_at: futureExpiry, organization: actor.organizationId },
      { name: names.never, expires_at: null, organization: actor.organizationId },
    ]);
  });

  await test.step('API: golden access and stable expired versus disabled failures', async () => {
    // Observe only the clock boundary; authentication assertions below are never retried.
    await expect.poll(() => Date.now() >= Date.parse(shortExpiry), { timeout: UI_READY }).toBe(true);
    for (const key of [...keys, expired, disabled]) {
      for (const mode of ['headers', 'basic']) {
        const headers: Record<string, string> = mode === 'headers'
          ? { 'X-Api-Key': key.api_key, 'X-Secret-Key': key.secret_key }
          : { Authorization: `Basic ${Buffer.from(`${key.api_key}:${key.secret_key}`).toString('base64')}` };
        for (let replay = 0; replay < (key === expired ? 2 : 1); replay++) {
          const response = await request.get(`${E2E.apiUrl}${HEALTH}`, { headers });
          const body = await response.json();
          // Do not attach raw responses or credentials to the report.
          expect(JSON.stringify(body).includes(key.api_key)).toBe(false);
          expect(JSON.stringify(body).includes(key.secret_key)).toBe(false);
          if (key === expired) {
            expect(response.status()).toBe(401);
            expect({ code: body.code, detail: body.detail }).toEqual({
              code: 'api_key_expired', detail: 'API key has expired',
            });
          } else if (key === disabled) {
            expect(response.status()).toBe(401);
            expect({ code: body.code, detail: body.detail }).toEqual({
              code: 'authentication_failed', detail: 'API key is disabled',
            });
          } else {
            expect(response.status()).toBe(200);
            expect(body).toEqual({ status: 'OK', version: '1.0.0' });
          }
        }
      }
    }
  });

  await test.step('API and UI: exact list distinguishes expired and manually disabled', async () => {
    const listed = await actor.api.get<{ result: { table: ListedKey[] } }>(LIST, {
      search: prefix, current_page_index: 0, page_size: 25,
    });
    expect(listed.result.table.map(k => ({ name: k.key_name, expires_at: k.expires_at
      ? new Date(k.expires_at).toISOString() : null, enabled: k.enabled, is_expired: k.is_expired }))
      .sort((a, b) => a.name.localeCompare(b.name))).toEqual([
        { name: names.disabled, expires_at: null, enabled: false, is_expired: false },
        { name: names.expired, expires_at: shortExpiry, enabled: false, is_expired: true },
        { name: names.future, expires_at: futureExpiry, enabled: true, is_expired: false },
        { name: names.never, expires_at: null, enabled: true, is_expired: false },
      ]);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByPlaceholder('Search', { exact: true }).fill(prefix);
    const expiredRow = page.locator(`[role="row"][data-id="${expired.key_id}"]`);
    const disabledRow = page.locator(`[role="row"][data-id="${disabled.key_id}"]`);
    await expect(expiredRow.getByText('Expired', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(expiredRow.getByText('Disabled', { exact: true })).toHaveCount(0, { timeout: UI_READY });
    await expect(disabledRow.getByText('Disabled', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(disabledRow.getByText('Expired', { exact: true })).toHaveCount(0, { timeout: UI_READY });
  }, { timeout: 2 * UI_READY });

  const changedKeys: CreatedKey[] = [];
  for (const kind of ['past', 'near']) {
    const key = (await actor.api.post<Created>(CREATE, {
      key_name: `${prefix}-admin-${kind}`,
      expires_at: kind === 'past' ? null : futureExpiry,
    })).result;
    changedKeys.push(key);
    const seeded = await sendTrace(request, { collectorUrl: E2E.collectorUrl,
      apiKey: key.api_key, secretKey: key.secret_key, projectName: `${prefix}-${kind}` });
    await testInfo.attach(`warmed-${kind}-identity`, {
      body: JSON.stringify({ keyId: key.key_id, traceId: seeded.traceId }),
      contentType: 'application/json',
    });
    await expect.poll(async () => {
      const rows = await probe.ch<{ n: string }>(
        'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });
      return Number(rows[0].n);
    }, POLL.SPAN_VISIBLE).toBe(2);
  }

  await test.step('admin: sign in with the limited key-administrator actor', async () => {
    // Django admin's AuthenticationForm and accounts.admin.OrgApiKeyAdmin.
    await page.goto(`${E2E.apiUrl}/admin/login/`);
    await page.locator('#id_username').fill(actor.email);
    await page.locator('#id_password').fill(actor.password);
    await page.getByRole('button', { name: 'Log in', exact: true }).click();
    await expect(page).toHaveURL(`${E2E.apiUrl}/admin/`, { timeout: UI_READY });
  }, { timeout: UI_READY });

  for (const [index, kind] of ['past', 'near'].entries()) {
    const key = changedKeys[index];
    const expires = new Date(Math.floor(Date.now() / 1000) * 1000
      + (kind === 'past' ? -60_000 : 60_000));
    const iso = expires.toISOString();
    await test.step(`admin: save ${kind} expiry through the real change form`, async () => {
      await page.goto(`${E2E.apiUrl}/admin/accounts/orgapikey/${key.key_id}/change/`);
      // AdminSplitDateTime widget; admin displays UTC in this managed stack.
      await page.locator('#id_expires_at_0').fill(iso.slice(0, 10));
      await page.locator('#id_expires_at_1').fill(iso.slice(11, 19));
      await page.locator('input[name="_continue"]').click();
      await expect(page.locator('.messagelist .success')).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    const rows = await probe.pg<{ expires_at: Date }>(
      'SELECT expires_at FROM accounts_orgapikey WHERE id = $1 AND organization_id = $2',
      [key.key_id, actor.organizationId]);
    expect(rows.map(row => row.expires_at.toISOString())).toEqual([iso]);
    const headers = { 'X-Api-Key': key.api_key, 'X-Secret-Key': key.secret_key };
    // Empty OTLP export is a valid request, accepted before expiry. Authentication
    // assertions are single calls, not polled or retried.
    if (kind === 'near') {
      const before = await request.post(`${E2E.collectorUrl}/v1/traces`, {
        headers, data: { resourceSpans: [] },
      });
      expect(before.status()).toBe(200);
      await expect.poll(() => Date.now() >= expires.getTime(), { timeout: UI_READY }).toBe(true);
    }
    const refused = await request.post(`${E2E.collectorUrl}/v1/traces`, {
      headers, data: { resourceSpans: [] },
    });
    expect(refused.status()).toBe(401);
    expect((await refused.json()).code).toBe('api_key_expired');
    const apiRefused = await request.get(`${E2E.apiUrl}${HEALTH}`, { headers });
    expect(apiRefused.status()).toBe(401);
    expect((await apiRefused.json()).code).toBe('api_key_expired');
    const control = keys.find(k => k.key_name === names.never)!;
    const controlHeaders = { 'X-Api-Key': control.api_key, 'X-Secret-Key': control.secret_key };
    const collectorControl = await request.post(`${E2E.collectorUrl}/v1/traces`, {
      headers: controlHeaders, data: { resourceSpans: [] },
    });
    expect(collectorControl.status()).toBe(200);
    expect((await request.get(`${E2E.apiUrl}${HEALTH}`, { headers: controlHeaders })).status()).toBe(200);
  }
  await test.step('UI: the admin-edited keys show Expired', async () => {
    await page.goto(PAGE, { waitUntil: 'domcontentloaded' });
    await page.getByPlaceholder('Search', { exact: true }).fill(`${prefix}-admin-`);
    for (const key of changedKeys) {
      await expect(page.locator(`[role="row"][data-id="${key.key_id}"]`)
        .getByText('Expired', { exact: true })).toBeVisible({ timeout: UI_READY });
    }
  }, { timeout: UI_READY });
});
