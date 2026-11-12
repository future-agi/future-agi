// SELECTOR_RECHECK_AFTER_FE(FE-GW-OPS): WHOLE_FILE, not only conflict regions.
// Recheck every selector, imported grid/picker helper, declaration and repeated use,
// including all auto-merged hunks; change dependent references together after FE lands.
import { request } from '@playwright/test';
import type { Locator, Page, Route } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { GatewayGridReader, GRID_VIEWPORT } from '../../lib/gateway-grid';

// Pinned off the running system, like request-log-tags.spec.ts: the logs
// webhook the gateway flushes to, the request-logs list endpoint, the
// org-scoped custom-property declaration endpoint the Columns picker reads
// (AgentccCustomPropertySchemaViewSet), the browser-local preference key the
// picker writes (frontend/src/sections/gateway/logs/columns/columnPrefsStorage.js),
// the cell preview length (columns/formatMetadataValue.js PREVIEW_LIMIT), the
// mark an empty cell shows (formatMetadataValue.js EMPTY_TEXT, which is
// utils/formatters.js EMPTY, an em dash) and the Request Logs search box that
// sits before Saved filters, Filters and Columns (RequestExplorerSection.jsx
// GatewayFilterBar searchPlaceholder). The table is the gateway grid
// (logs/RequestTable.jsx on components/frame/GatewayGrid.jsx), whose column
// ids are the column model's (`builtin:<key>`, `metadata:<name>`), read in
// display order through lib/gateway-grid.ts.
const LOGS_WEBHOOK_PATH = '/agentcc/webhook/logs/';
const WEBHOOK_SECRET =
  process.env.AGENTCC_WEBHOOK_SECRET || 'e2e-agentcc-webhook-secret';
const REQUEST_LOGS_PATH = '/agentcc/request-logs/';
const CUSTOM_PROPERTIES_PATH = '/agentcc/custom-properties/';
const STORAGE_PREFIX = 'agentcc.requestLogs.columns.v1';
const PREVIEW_LIMIT = 80;
const EMPTY = '\u2014';
const SEARCH_PLACEHOLDER = 'Search model, provider, request id, error';
// The default view (logs/columns/columnModel.js DEFAULT_CONFIG): every
// built-in in DEFAULT_ORDER, with Application, Service and Session ID
// listed in the picker but hidden.
const DEFAULT_ORDER = [
  'Timestamp',
  'Status',
  'Model',
  'Provider',
  'Latency',
  'Cost',
  'Tokens',
  'Application',
  'Service',
  'Session ID',
];
const DEFAULT_HIDDEN = ['Application', 'Service', 'Session ID'];
const DEFAULT_HIDDEN_IDS = ['builtin:application', 'builtin:service', 'builtin:sessionId'];
const shown = (order: string[], hidden: string[] = []) =>
  order.filter((h) => !DEFAULT_HIDDEN.includes(h) && !hidden.includes(h));
const DEFAULT_HEADERS = shown(DEFAULT_ORDER);
// The built-in order once `Model` has moved down past `Provider` and `Latency`.
const REORDERED = [
  'Timestamp',
  'Status',
  'Provider',
  'Latency',
  'Model',
  'Cost',
  'Tokens',
  'Application',
  'Service',
  'Session ID',
];
// `label` moved to index `to` of `order`.
const movedTo = (order: string[], label: string, to: number) => {
  const rest = order.filter((h) => h !== label);
  rest.splice(to, 0, label);
  return rest;
};
// The Agent & billing group (F1): opt-in, listed unchecked between the
// built-ins and the custom properties, in this default order.
// A request's cost is the built-in Cost column alone: no Upstream cost or
// Markup column splits a credits charge.
const AGENT_BILLING = [
  'Billing mode',
  'Credential type',
  'Attempts',
  'Agent',
  'Step',
  'Run',
];
// The columns that describe managed calls only (logs/columns/gatewayColumns.js
// MANAGED_ONLY_COLUMN_IDS): the picker offers them where the deployment offers
// managed models, GET /agentcc/managed-models/ `available`
// (logs/RequestExplorerSection.jsx managedOffered, columns/columnModel.js
// resolveGatewayColumns), and leaves them out where it offers none.
const MANAGED_STATUS_PATH = '/agentcc/managed-models/';
const MANAGED_ONLY = ['Credential type'];
const agentBilling = (managed: boolean) =>
  (managed ? AGENT_BILLING : AGENT_BILLING.filter((name) => !MANAGED_ONLY.includes(name)));
// Covers this fixture's picker and table tab stops: Close, search, each
// checkbox and enabled move arrow, plus labels whose text is actually clipped.
const MAX_TAB_PRESSES = 40;
const LOG_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;
// Each UI step waits on at most one page load: its UI_READY plus 15 s for the
// keyboard and local assertions around it, so an assertion that runs out
// reports its own diff before the step's deadline.
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + seeding 30 + LOG_VISIBLE 60 + nine UI steps
// of STEP_BUDGET 75 (675) + teardown/headroom 30.
const FLOW_TIMEOUT = 915_000;

interface RequestLogRow {
  request_id: string;
  metadata: Record<string, string>;
}
interface Paginated<T> {
  count: number;
  results: T[];
}
interface CustomProperty {
  id: string;
  name: string;
  organization: string;
}

// Keyboard only: press `key` until `target` holds focus, then assert it, so a
// control the keyboard cannot reach fails here by name.
async function pressUntilFocused(page: Page, target: Locator, key: 'Tab' | 'Shift+Tab') {
  for (let presses = 0; presses < MAX_TAB_PRESSES; presses += 1) {
    if (await target.evaluate((el) => el === document.activeElement)) break;
    await page.keyboard.press(key);
  }
  await expect(target).toBeFocused({ timeout: UI_READY });
}

// The same for a grid cell: the grid moves keyboard focus cell by cell, so
// focus may sit on the cell itself or on the value inside it.
async function pressUntilFocusWithin(page: Page, target: Locator, key: 'Tab' | 'Shift+Tab') {
  const within = () => target.evaluate((el) => el.contains(document.activeElement));
  for (let presses = 0; presses < MAX_TAB_PRESSES; presses += 1) {
    if (await within()) break;
    await page.keyboard.press(key);
  }
  await expect.poll(within, { timeout: UI_READY }).toBe(true);
}

// scopeActors signs its users up before the body runs, so its setup falls
// under the file's timeout, not the body's test.setTimeout: without this
// line it gets the 120 s project default.
test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-003: a platform engineer adds a declared custom property as a Request Logs column',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-003',
      area: 'gateway',
      userGoal:
        'A platform engineer shows a declared custom property beside the built-in Request Logs columns, keeps that choice across reloads in this browser, and never sees it under another organization',
      steps: [
        'read whether the deployment offers managed models',
        'declare a custom property with a minted `tenant_<suffix>` name in an organization of its own',
        'mint a gateway API key and deliver four gateway requests on the logs webhook, three carrying tenant metadata, one longer than the 80-character cell preview',
        'in a second organization, mint a gateway API key and deliver one request whose metadata carries the same, undeclared, tenant property',
        'open Request Logs, Tab to Columns and press Enter to open the picker',
        'by keyboard only, Space hides `Provider`, Enter moves `Model` down twice and `Tokens` to the end of the built-ins and back, Space checks the tenant column, Escape closes the picker',
        'Tab to the truncated tenant value',
        'reload the page',
        'reset to default, then check the tenant column again',
        'hide `Provider`, then reload while the browser fails every declaration list GET with a 500 through page.route, React Query’s automatic retry included',
        'let the declaration list through again and press Retry',
        'open Request Logs as a second identity, in its own organization and browser context, holding a copy of the first preference record',
        'delete the declaration, reopen the picker and remove the entry listed as no longer declared',
      ],
      backendChecks: [
        'the declaration is stored in PG agentcc_custom_property_schema under the flow’s organization and is its only declaration',
        'the table renders the tenant value per row and an em dash where the row carries none',
        'selecting and reordering columns issues no per-row request-log detail call and at most one declaration list call',
        'the selection and order survive a reload for the same user, org and browser',
        'reset restores the default headers (seven shown; Application, Service and Session ID listed but hidden), renders no tenant cell and removes only this preference record',
        'while the browser-injected 500 answers every declaration list attempt, the saved built-in columns render and stay usable, no saved custom column name reaches the page and the saved record is unchanged',
        'Retry is answered by the real API and restores the tenant column and its row values, leaving the saved record byte-identical',
        'the second org never sees the first org’s tenant column, declaration or saved record: its own request lists under the default columns, without the tenant column its metadata carries',
        'after the declaration is deleted the saved column is listed as no longer declared and is not rendered',
        'Remove drops the stale tenant id from the saved record and keeps the rest of it',
      ],
      browserChecks: [
        'the picker opens, toggles, reorders and closes by keyboard alone: focus stays on the pressed Move button, moves to the opposite Move button when the column reaches the end of its group, and returns to Columns on Escape',
        'the picker lists the Agent & billing group between the built-ins and the custom properties, with Credential type only where the deployment offers managed models, and no Upstream cost or Markup column',
        'a tenant value longer than 80 characters shows its preview in the cell, and Tab to it shows the full value in a tooltip',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    // An organization per test: GW-E2E-001 asserts exact org-wide request-log
    // sets on the worker-scoped actor, so these logs must not land there.
    const owner = scopes.ownerA;
    const other = scopes.ownerB;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const application = `e2e-app-${suffix}`;
    const propertyName = `tenant_${suffix}`;
    const longTenant = `${'long-tenant-value-'.repeat(5)}${suffix}`;
    const longPreview = `${longTenant.slice(0, PREVIEW_LIMIT - 1)}…`;
    // Newest first, the table's default -started_at order.
    const tenantColumnText = [longPreview, 'globex', EMPTY, 'acme'];
    const tenantId = `metadata:${propertyName}`;
    const storageKey = `${STORAGE_PREFIX}:${owner.userId}:${owner.organizationId}:requests`;
    // The Agent & billing group this deployment's picker lists: with managed
    // models offered, or with none.
    const managed = (await owner.api.get<{ result: { available: boolean } }>(MANAGED_STATUS_PATH))
      .result.available === true;
    const billingGroup = agentBilling(managed);

    const declared = await owner.api.post<{ result: CustomProperty }>(
      CUSTOM_PROPERTIES_PATH,
      {
        name: propertyName,
        description: `e2e ${suffix}`,
        property_type: 'string',
        required: false,
        allowed_values: [],
      },
    );
    expect(declared.result.organization).toBe(owner.organizationId);

    const created = await owner.api.post<{ result: { gateway_key_id: string } }>(
      '/agentcc/api-keys/',
      { name: `e2e-gw-${suffix}` },
    );
    const gatewayKeyId = created.result.gateway_key_id;
    // The logs grid is drawn over rows only (an organization with no request
    // sees the empty state, with no headers), so the second organization gets
    // one request of its own. Its metadata carries the same property name,
    // which that organization never declared.
    const otherCreated = await other.api.post<{ result: { gateway_key_id: string } }>(
      '/agentcc/api-keys/',
      { name: `e2e-gw-${suffix}-b` },
    );

    const req = await request.newContext();
    try {
      const deliver = async (index: number, metadata: Record<string, string>, keyId = gatewayKeyId) => {
        const res = await req.post(`${E2E.apiUrl}${LOGS_WEBHOOK_PATH}`, {
          headers: { 'X-Webhook-Secret': WEBHOOK_SECRET },
          data: {
            logs: [
              {
                request_id: `e2e-gw-${suffix}-${index}`,
                auth_key_id: keyId,
                model: 'gpt-4o-mini',
                provider: 'openai',
                status_code: 200,
                latency_ms: 120,
                total_tokens: 42,
                cost: '0.000420',
                timestamp: new Date(Date.now() + index * 1000).toISOString(),
                metadata: { application, ...metadata },
              },
            ],
          },
        });
        expect(res.status(), await res.text()).toBe(200);
      };

      await deliver(1, { [propertyName]: 'acme' });
      await deliver(2, {});
      await deliver(3, { [propertyName]: 'globex' });
      await deliver(4, { [propertyName]: longTenant });
      await deliver(5, { [propertyName]: 'initech' }, otherCreated.result.gateway_key_id);
    } finally {
      await req.dispose();
    }

    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify(
        {
          suffix,
          organizationId: owner.organizationId,
          userId: owner.userId,
          application,
          propertyName,
          declarationId: declared.result.id,
          gatewayKeyId,
          requestIds: [1, 2, 3, 4].map((index) => `e2e-gw-${suffix}-${index}`),
          storageKey,
          managedModelsOffered: managed,
        },
        null,
        2,
      ),
    });

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    await page.setViewportSize(GRID_VIEWPORT);
    const columnsButton = page.getByRole('button', { name: 'Columns', exact: true });
    const dialog = page.getByRole('dialog', { name: 'Choose columns' });
    const grid = GatewayGridReader.in(page);
    const expectHeaders = (want: string[]) =>
      expect.poll(() => grid.headers(), { timeout: UI_READY }).toEqual(want);
    const rows = grid.rows;
    const tenantCells = grid.cells(tenantId);
    const tenantText = () => grid.column(tenantId);
    // The column's header and cells both carry its id.
    const tenantColumn = page.locator(`[col-id="${tenantId}"]`);
    const savedHeaders = [...shown(REORDERED, ['Provider']), propertyName];
    const savedHidden = [...DEFAULT_HIDDEN_IDS, 'builtin:provider'];
    const readRecord = () => page.evaluate((key) => localStorage.getItem(key), storageKey);

    await test.step('API: the declaration and the tagged requests are stored under their orgs', async () => {
      await expect
        .poll(
          async () => ({
            owner: (await owner.api.get<Paginated<RequestLogRow>>(REQUEST_LOGS_PATH, { application }))
              .count,
            other: (await other.api.get<Paginated<RequestLogRow>>(REQUEST_LOGS_PATH)).count,
          }),
          LOG_VISIBLE,
        )
        .toEqual({ owner: 4, other: 1 });
      const stored = await probe.pg<{ name: string }>(
        'SELECT name FROM agentcc_custom_property_schema WHERE organization_id = $1 AND deleted = false',
        [owner.organizationId],
      );
      expect(stored.map((row) => row.name)).toEqual([propertyName]);
    });

    await test.step('UI: Request Logs opens with the default columns and no saved record', async () => {
      await page.goto(`/dashboard/gateway/logs?application=${application}`, {
        waitUntil: 'domcontentloaded',
      });
      await expect(rows).toHaveCount(4, { timeout: UI_READY });
      await expectHeaders(DEFAULT_HEADERS);
      expect(await readRecord()).toBeNull();
    }, { timeout: STEP_BUDGET });

    await test.step('UI: the keyboard alone adds the tenant column, hides Provider and reorders', async () => {
      const detailCalls: string[] = [];
      const declarationCalls: string[] = [];
      const listCalls: string[] = [];
      const onRequest = (r: { url: () => string }) => {
        const url = r.url();
        if (/\/agentcc\/request-logs\/[0-9a-f-]{36}\/?(\?|$)/.test(url)) detailCalls.push(url);
        if (url.includes(CUSTOM_PROPERTIES_PATH)) declarationCalls.push(url);
        if (/\/agentcc\/request-logs\/?\?/.test(url)) listCalls.push(url);
      };
      page.on('request', onRequest);
      const listCallsBefore = listCalls.length;

      // Keyboard only from here, starting in the search box.
      await expect(columnsButton).toBeEnabled({ timeout: UI_READY });
      await page.getByPlaceholder(SEARCH_PLACEHOLDER).focus();
      await pressUntilFocused(page, columnsButton, 'Tab');
      await page.keyboard.press('Enter');
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await expect
        .poll(() => dialog.evaluate((el) => el.contains(document.activeElement)), {
          timeout: UI_READY,
        })
        .toBe(true);
      await expect(dialog.getByRole('checkbox', { name: 'Timestamp (always visible)' })).toBeDisabled({
        timeout: UI_READY,
      });
      const tenantBox = dialog.getByRole('checkbox', { name: propertyName });
      await expect(tenantBox).not.toBeChecked({ timeout: UI_READY });

      const provider = dialog.getByRole('checkbox', { name: 'Provider' });
      await pressUntilFocused(page, provider, 'Tab');
      await page.keyboard.press('Space');
      await expect(provider).not.toBeChecked({ timeout: UI_READY });

      // Enter moves the column and focus stays on the pressed button; when the
      // column reaches the end of its group focus moves to its other Move button.
      const pickerOrder = dialog.locator('label:has(input[type="checkbox"])');
      const modelDown = dialog.getByRole('button', { name: 'Move Model down' });
      await pressUntilFocused(page, modelDown, 'Shift+Tab');
      await page.keyboard.press('Enter');
      await expect(pickerOrder.nth(3)).toHaveText('Model', { timeout: UI_READY });
      await expect(modelDown).toBeFocused({ timeout: UI_READY });
      await page.keyboard.press('Enter');
      await expect(pickerOrder).toHaveText([...REORDERED, ...billingGroup, propertyName], {
        timeout: UI_READY,
      });
      await expect(modelDown).toBeFocused({ timeout: UI_READY });
      // Credential type is offered only where the deployment offers managed
      // models; Upstream cost and Markup are offered nowhere.
      for (const name of MANAGED_ONLY) {
        await expect(dialog.getByRole('checkbox', { name, exact: true })).toHaveCount(managed ? 1 : 0);
      }
      for (const name of ['Upstream cost', 'Markup']) {
        await expect(dialog.getByRole('checkbox', { name, exact: true })).toHaveCount(0);
      }

      // Tokens is three places from the end of the built-ins (past the hidden
      // Application, Service and Session ID): focus stays on Move down until
      // the last move, then moves to Move up, which brings it back.
      const tokensDown = dialog.getByRole('button', { name: 'Move Tokens down' });
      const tokensUp = dialog.getByRole('button', { name: 'Move Tokens up' });
      const tokensAt = REORDERED.indexOf('Tokens');
      const lastBuiltin = REORDERED.length - 1;
      await pressUntilFocused(page, tokensDown, 'Tab');
      for (let to = tokensAt + 1; to <= lastBuiltin; to += 1) {
        await page.keyboard.press('Enter');
        await expect(pickerOrder).toHaveText(
          [...movedTo(REORDERED, 'Tokens', to), ...billingGroup, propertyName],
          { timeout: UI_READY },
        );
        await expect(to === lastBuiltin ? tokensUp : tokensDown).toBeFocused({ timeout: UI_READY });
      }
      await expect(tokensDown).toBeDisabled({ timeout: UI_READY });
      for (let to = lastBuiltin - 1; to >= tokensAt; to -= 1) {
        await page.keyboard.press('Enter');
        await expect(pickerOrder).toHaveText(
          [...movedTo(REORDERED, 'Tokens', to), ...billingGroup, propertyName],
          { timeout: UI_READY },
        );
        await expect(tokensUp).toBeFocused({ timeout: UI_READY });
      }
      await expect(pickerOrder).toHaveText([...REORDERED, ...billingGroup, propertyName], {
        timeout: UI_READY,
      });

      await pressUntilFocused(page, tenantBox, 'Tab');
      await page.keyboard.press('Space');
      await expect(tenantBox).toBeChecked({ timeout: UI_READY });
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden({ timeout: UI_READY });
      await expect(columnsButton).toBeFocused({ timeout: UI_READY });

      await expectHeaders(savedHeaders);
      await expect.poll(tenantText, { timeout: UI_READY }).toEqual(tenantColumnText);
      // Custom headers carry no sort control and the URL keeps its filters.
      expect(page.url()).toContain(`application=${application}`);
      expect(page.url()).not.toContain('sort=');

      page.off('request', onRequest);
      expect(detailCalls).toEqual([]);
      expect(declarationCalls.length).toBeLessThanOrEqual(1);
      expect(listCalls.length - listCallsBefore).toBe(0);

      const record = JSON.parse((await readRecord()) || 'null');
      expect(record).toMatchObject({ v: 1, hidden: savedHidden });
      expect(record.columns.map((c: { id: string }) => c.id)).toContain(tenantId);
      expect(JSON.stringify(record)).not.toContain('acme');
    }, { timeout: STEP_BUDGET });

    await test.step('UI: Tab reaches the truncated tenant value and its tooltip shows it in full', async () => {
      await expect(columnsButton).toBeFocused({ timeout: UI_READY });
      // SELECTOR_RECHECK_AFTER_FE(FE-GW-OPS): tooltip accessible name asserted as dev does; candidate asserted role only
      // Newest first: the long value is the top row's.
      const longCell = grid.cell(0, tenantId);
      await expect(longCell).toHaveText(longPreview, { timeout: UI_READY });
      await pressUntilFocusWithin(page, longCell, 'Tab');
      await expect(page.getByRole('tooltip', { name: longTenant, exact: true })).toHaveText(longTenant, {
        timeout: UI_READY,
      });
    }, { timeout: STEP_BUDGET });

    await test.step('UI: the selection survives a reload and reset clears only this record', async () => {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect.poll(tenantText, { timeout: UI_READY }).toEqual(tenantColumnText);
      await expectHeaders(savedHeaders);

      const foreignKey = `${STORAGE_PREFIX}:other-user:${owner.organizationId}:requests`;
      await page.evaluate(
        ([key, value]) => localStorage.setItem(key, value),
        [foreignKey, '{"v":1,"columns":[{"id":"builtin:startedAt"}],"hidden":[]}'],
      );

      await columnsButton.click();
      await dialog.getByRole('button', { name: 'Reset to default' }).click();
      await page.keyboard.press('Escape');
      await expectHeaders(DEFAULT_HEADERS);
      await expect(tenantColumn).toHaveCount(0, { timeout: UI_READY });
      expect(await readRecord()).toBeNull();
      expect(await page.evaluate((key) => localStorage.getItem(key), foreignKey)).not.toBeNull();

      // Put the tenant column back for the remaining steps.
      await columnsButton.click();
      await dialog.getByRole('checkbox', { name: propertyName }).check();
      await page.keyboard.press('Escape');
      await expect(tenantCells).toHaveCount(4, { timeout: UI_READY });
    }, { timeout: STEP_BUDGET });

    const savedBuiltins = shown(DEFAULT_ORDER, ['Provider']);
    const retry = dialog.getByRole('button', { name: 'Retry loading custom properties' });
    const customNameOnPage = async () => ({
      text: await page.getByText(propertyName, { exact: true }).count(),
      column: await tenantColumn.count(),
    });
    let savedRecord: string | null = null;

    // Fail only the declaration list GET, in the browser, on every attempt the
    // app makes (React Query's automatic retry included); every other request
    // reaches the real backend. The first attempt is held so loading is observable.
    const failedCalls: string[] = [];
    let releaseFirst = () => {};
    const firstHeld = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    const isDeclarationList = (url: URL) => url.pathname.endsWith(CUSTOM_PROPERTIES_PATH);
    const failDeclarationList = async (route: Route) => {
      const intercepted = route.request();
      if (intercepted.method() !== 'GET') return route.fallback();
      failedCalls.push(intercepted.url());
      if (failedCalls.length === 1) await firstHeld;
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        headers: {
          'access-control-allow-origin':
            (await intercepted.headerValue('origin')) ?? new URL(E2E.appUrl).origin,
          'access-control-allow-credentials': 'true',
        },
        body: JSON.stringify({ status: false, result: 'e2e: declaration list unavailable' }),
      });
    };

    await test.step('UI: with the declaration list failing in the browser, the saved built-ins render and the picker reports loading', async () => {
      // A saved built-in change beside the custom column, so the failure path
      // has a non-default built-in layout to keep.
      await columnsButton.click();
      await dialog.getByRole('checkbox', { name: 'Provider' }).uncheck();
      await page.keyboard.press('Escape');
      await expectHeaders([...savedBuiltins, propertyName]);
      savedRecord = await readRecord();
      expect(JSON.parse(savedRecord || 'null')).toMatchObject({ v: 1, hidden: savedHidden });
      expect(savedRecord).toContain(tenantId);

      await page.route(isDeclarationList, failDeclarationList);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(rows).toHaveCount(4, { timeout: UI_READY });
      await expect.poll(() => failedCalls.length, { timeout: UI_READY }).toBe(1);
      await expectHeaders(savedBuiltins);
      await columnsButton.click();
      await expect(
        dialog.getByText('Loading custom properties… 1 saved custom column unavailable.'),
      ).toBeVisible({ timeout: UI_READY });
      expect(await customNameOnPage()).toEqual({ text: 0, column: 0 });
      expect(await readRecord()).toBe(savedRecord);
    }, { timeout: STEP_BUDGET });

    await test.step('UI: after the app’s own retry fails, the built-ins stay usable and nothing custom renders', async () => {
      releaseFirst();
      await expect(retry).toBeVisible({ timeout: UI_READY });
      await expect(
        dialog.getByText("Couldn't load custom properties. 1 saved custom column unavailable."),
      ).toBeVisible({ timeout: UI_READY });
      await testInfo.attach('declaration-list-failures', {
        contentType: 'application/json',
        body: JSON.stringify({ failedCalls }, null, 2),
      });
      // The error is what the app reached after its own retry, not one 500.
      expect(failedCalls.length).toBeGreaterThanOrEqual(2);
      await expectHeaders(savedBuiltins);
      expect(await customNameOnPage()).toEqual({ text: 0, column: 0 });
      expect(await readRecord()).toBe(savedRecord);

      // Built-in columns stay usable while declarations fail.
      await dialog.getByRole('checkbox', { name: 'Model' }).uncheck();
      await expectHeaders(savedBuiltins.filter((h) => h !== 'Model'));
      await dialog.getByRole('checkbox', { name: 'Model' }).check();
      await expectHeaders(savedBuiltins);
      expect(await customNameOnPage()).toEqual({ text: 0, column: 0 });
      expect(await readRecord()).toBe(savedRecord);
    }, { timeout: STEP_BUDGET });

    await test.step('UI: Retry is answered by the real API and restores the tenant column', async () => {
      // Let the list through again, then use the product's own Retry.
      await page.unroute(isDeclarationList, failDeclarationList);
      const failedBeforeRetry = failedCalls.length;
      const recovered = page.waitForResponse(
        (r) => isDeclarationList(new URL(r.url())) && r.request().method() === 'GET',
        { timeout: UI_READY },
      );
      await retry.click();
      expect((await recovered).status()).toBe(200);
      await expect(dialog.getByRole('checkbox', { name: propertyName })).toBeChecked({
        timeout: UI_READY,
      });
      await page.keyboard.press('Escape');
      await expectHeaders([...savedBuiltins, propertyName]);
      await expect.poll(tenantText, { timeout: UI_READY }).toEqual(tenantColumnText);
      expect(failedCalls.length).toBe(failedBeforeRetry);
      expect(await readRecord()).toBe(savedRecord);
    }, { timeout: STEP_BUDGET });

    await test.step('UI: a separate identity in its own org cannot reuse a copied first-identity record', async () => {
      const otherKey = `${STORAGE_PREFIX}:${other.userId}:${other.organizationId}:requests`;
      const firstRecord = (await readRecord()) ?? '';
      // The copied record hides Provider, so applying it would change the headers.
      expect(JSON.parse(firstRecord || 'null')).toMatchObject({ hidden: savedHidden });
      expect(firstRecord).toContain(tenantId);
      await testInfo.attach('seeded-second-identity', {
        contentType: 'application/json',
        body: JSON.stringify(
          {
            organizationId: other.organizationId,
            userId: other.userId,
            storageKey: otherKey,
            copiedStorageKey: storageKey,
            copiedRecord: firstRecord,
          },
          null,
          2,
        ),
      });

      const otherContext = await scopes.openContext(browser, other);
      // Put the first identity's record into this browser profile before the
      // app boots, to prove the key, not the profile, scopes the preference.
      await otherContext.addInitScript(
        ([key, value]) => localStorage.setItem(key, value),
        [storageKey, firstRecord],
      );
      const otherPage = await otherContext.newPage();
      await otherPage.setViewportSize(GRID_VIEWPORT);
      const declarationRequests: string[] = [];
      otherPage.on('request', (r) => {
        if (r.url().includes(CUSTOM_PROPERTIES_PATH)) {
          declarationRequests.push(r.headers()['x-organization-id'] || '');
        }
      });
      await otherPage.goto('/dashboard/gateway/logs', { waitUntil: 'domcontentloaded' });
      const otherColumns = otherPage.getByRole('button', { name: 'Columns', exact: true });
      await expect(otherColumns).toBeVisible({ timeout: UI_READY });
      await otherColumns.click();
      const otherDialog = otherPage.getByRole('dialog', { name: 'Choose columns' });
      await expect(otherDialog.getByText('No custom properties declared')).toBeVisible({
        timeout: UI_READY,
      });
      await expect
        .poll(() => GatewayGridReader.in(otherPage).headers(), { timeout: UI_READY })
        .toEqual(DEFAULT_HEADERS);
      await expect(otherPage.getByText(propertyName)).toHaveCount(0, { timeout: UI_READY });
      await expect(otherPage.locator(`[col-id="${tenantId}"]`)).toHaveCount(0, {
        timeout: UI_READY,
      });
      expect(await otherPage.evaluate((key) => localStorage.getItem(key), otherKey)).toBeNull();
      expect(new Set(declarationRequests)).toEqual(new Set([other.organizationId]));
      await otherContext.close();
    }, { timeout: STEP_BUDGET });

    await test.step('UI: a deleted declaration is listed as no longer declared, not rendered, and Remove drops it', async () => {
      await owner.api.delete(`${CUSTOM_PROPERTIES_PATH}${declared.result.id}/`);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(rows).toHaveCount(4, { timeout: UI_READY });
      await columnsButton.click();
      // Stale entries are listed only once the declaration list has loaded, so
      // the headers below are read after it resolved.
      await expect(dialog.getByText('No longer declared')).toBeVisible({ timeout: UI_READY });
      await expectHeaders(savedBuiltins);
      await expect(tenantColumn).toHaveCount(0, { timeout: UI_READY });

      const before = JSON.parse((await readRecord()) || 'null');
      expect(before.columns.map((c: { id: string }) => c.id)).toContain(tenantId);
      await dialog.getByRole('button', { name: `Remove ${propertyName}` }).click();
      await expect(dialog.getByText('No longer declared')).toHaveCount(0, { timeout: UI_READY });
      expect(JSON.parse((await readRecord()) || 'null')).toEqual({
        ...before,
        columns: before.columns.filter((c: { id: string }) => c.id !== tenantId),
      });
    }, { timeout: STEP_BUDGET });
  },
);
