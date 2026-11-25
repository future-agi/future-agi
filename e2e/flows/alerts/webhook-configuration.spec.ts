import type { Request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
// UserAlertMonitorSerializer and ProjectSerializer define these wire bodies.
const MONITORS = '/tracer/user-alerts/';
const PROJECTS = '/tracer/project/';
// AlertsSheetView.jsx exposes Edit Rule; AlertSettingsForm.jsx exposes these labels.
const ALERTS = '/dashboard/alerts';
const UI_READY = 60_000;

test('ALERT-E2E-003: alert owners configure webhooks without private destinations or unauthorized changes', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ALERT-E2E-003', area: 'alerts',
    userGoal: 'An authorized alert owner configures a public webhook through the alert editor while private destinations and unauthorized changes are refused.',
    steps: [
      'create a uniquely named muted monitor with no notification destinations',
      'open the alert editor, choose Webhook, enter a public URL and save',
      'read the exact saved destination through the detail API and Postgres',
      'try a private destination through the editor and verify rejection',
      'try cross-organization, cross-workspace and viewer updates',
    ],
    backendChecks: [
      'The browser-saved webhook is returned by the alert detail API and stored on the owner organization\'s muted monitor.',
      'A private destination is rejected and the saved webhook remains unchanged.',
      'Cross-organization, cross-workspace and viewer updates are refused without changing the saved webhook.',
    ],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  test.setTimeout(480_000); // 6 × UI_READY plus 120s provisioning/API headroom.
  const owner = scopeActors.ownerA;
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-webhook-${suffix}`;
  // Configuration-only address. This monitor stays muted throughout the flow;
  // the owned-VM socket matrix, not this flow, exercises delivery.
  const destination = `http://93.184.216.34/${name}`;
  const project = await scopeActors.send<{ result: { project_id: string } }>(owner, 'POST', PROJECTS, {
    name, trace_type: 'observe', model_type: 'GenerativeLLM',
  });
  expect(project.status).toBe(200);
  const created = await scopeActors.send<{ status: boolean; result: string }>(owner, 'POST', MONITORS, {
    project: project.body.result.project_id, organization: owner.organizationId, workspace: owner.workspaceId,
    name, metric_type: 'count_of_errors',
    threshold_operator: 'greater_than', threshold_type: 'static', critical_threshold_value: 999999,
    alert_frequency: 60, is_mute: true, slack_webhook_url: '', filters: {},
    // Omitted uses the model's empty-list default on dev as well as the candidate.
    // The actual editor save below explicitly clears it when selecting Webhook.
  });
  // UserAlertMonitorView.create returns a message, not a serialized row.
  expect(created.status, JSON.stringify(created.body)).toBe(200);
  expect(created.body).toEqual({ status: true, result: `${name} alert created successfully` });
  const seeded = await scopeProbe.pg<{ id: string }>(
    'SELECT id FROM tracer_useralertmonitor WHERE name=$1 AND organization_id=$2 AND workspace_id=$3 AND deleted=false',
    [name, owner.organizationId, owner.workspaceId]);
  expect(seeded).toHaveLength(1);
  const id = seeded[0].id;
  const detailPath = `${MONITORS}${id}/details/`;
  const updatePath = `${MONITORS}${id}/`;
  await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({ id, name, destination, project: project.body.result.project_id, organization: owner.organizationId, workspace: owner.workspaceId }) });
  const context = await scopeActors.openContext(browser, owner);
  const blocked: string[] = [];
  await context.route('**/*', route => {
    const host = new URL(route.request().url()).hostname;
    if (!['localhost', '127.0.0.1'].includes(host)) { blocked.push(route.request().url()); return route.abort(); }
    return route.continue();
  });
  const page = await context.newPage();
  const openEditor = async (opening: 'first-open' | 'reopen') => {
    // AlertsListView debounces search_text. Even the unfiltered list can show the seed.
    const listSearch = (request: Request) => {
      const url = new URL(request.url());
      return request.method() === 'GET' && url.pathname === `${MONITORS}list_monitors/`
        ? url.searchParams.get('search_text') : undefined;
    };
    const requests: Request[] = [];
    const started = performance.now();
    const remaining = () => {
      const ms = UI_READY - (performance.now() - started);
      if (ms <= 0) throw new Error(`${opening}: editor readiness deadline exceeded`);
      return { timeout: Math.ceil(ms) };
    };
    const events: { event: string; ms: number; request: number }[] = [];
    const mark = (event: string, request = requests.length) => {
      events.push({ event, ms: Math.round(performance.now() - started), request });
    };
    const onRequest = (request: Request) => {
      const search = listSearch(request);
      if (search === undefined) return;
      requests.push(request);
      if (!search) mark('unfiltered');
      else if (search === name) mark('full');
      else mark(name.startsWith(search) ? 'prefix' : 'other');
    };
    const responseAbort = new AbortController();
    page.on('request', onRequest);
    try {
      // One absolute budget covers navigation through the populated editor, including body reads.
      await test.step(`Open the seeded alert editor (${opening})`, async () => {
        await page.goto(ALERTS, { waitUntil: 'domcontentloaded', ...remaining() });
        const beforeFill = requests.length;
        const searchOptions = remaining();
        const search = page.getByPlaceholder('Search').first();
        const [response] = await Promise.all([
          page.waitForResponse(
            r => listSearch(r.request()) === name && requests.indexOf(r.request()) >= beforeFill,
            { ...searchOptions, signal: responseAbort.signal },
          ),
          search.fill(name, searchOptions),
        ]);
        expect(response.status()).toBe(200);
        expect(await response.finished()).toBeNull();
        // UserAlertMonitorView.list_monitors returns result.table; match the entire row set.
        expect((await response.json()).result.table).toMatchObject([{ id, name }]);
        const searched = response.request();
        mark('validated-full-response', requests.indexOf(searched) + 1);

        await expect(page.getByRole('progressbar', { name: 'Loading', exact: true })).toBeHidden(remaining());
        const row = page.locator(`[data-alert-row-id="${id}"][data-alert-row-name="${name}"]`);
        await expect(row).toBeVisible(remaining());
        // Compare request identity, not URL or response arrival order. A newer request
        // can still be in flight while the previous row remains visible and actionable.
        expect(requests.at(-1) === searched, 'validated search is the last list request before click').toBe(true);
        mark('row-click-start');
        await row.click(remaining());
        mark('row-click-end');
        expect(requests.at(-1) === searched, 'no list request started before the row click completed').toBe(true);
        const edit = page.getByRole('button', { name: 'Edit Rule', exact: true });
        await expect(edit).toBeEnabled(remaining());
        await edit.click(remaining());
        await expect(page.getByPlaceholder('Enter alert name', { exact: true })).toHaveValue(name, remaining());
        mark('editor-verified');
      }, remaining());
    } finally {
      responseAbort.abort();
      page.off('request', onRequest);
      await testInfo.attach(`editor-readiness-${opening}`, { contentType: 'application/json', body: JSON.stringify(events) });
    }
  };
  try {
    await openEditor('first-open');
    await page.getByRole('radio', { name: 'Webhook', exact: true }).check({ timeout: UI_READY });
    const input = page.getByPlaceholder('Enter webhook URL (e.g. https://example.com/webhook)');
    await input.fill(destination);
    const saved = page.waitForResponse(response => new URL(response.url()).pathname === updatePath && ['PUT', 'PATCH'].includes(response.request().method()), { timeout: UI_READY });
    await page.getByRole('button', { name: /Update.*Alert/i }).click();
    expect((await saved).status()).toBe(200);
    const detail = await scopeActors.send<{ result: { webhook_url: string; is_mute: boolean } }>(owner, 'GET', detailPath);
    expect(detail.status).toBe(200);
    expect(detail.body.result.webhook_url).toBe(destination);
    expect(detail.body.result.is_mute).toBe(true);
    const stored = await scopeProbe.pg<{ webhook_url: string; is_mute: boolean }>(
      'SELECT webhook_url, is_mute FROM tracer_useralertmonitor WHERE id=$1 AND organization_id=$2 AND workspace_id=$3 AND deleted=false',
      [id, owner.organizationId, owner.workspaceId]);
    expect(stored).toEqual([{ webhook_url: destination, is_mute: true }]);
    await page.screenshot({ path: testInfo.outputPath('configured.png'), fullPage: true });

    await openEditor('reopen');
    await input.fill('http://127.0.0.1:9/private');
    const refused = page.waitForResponse(response => new URL(response.url()).pathname === updatePath && ['PUT', 'PATCH'].includes(response.request().method()), { timeout: UI_READY });
    await page.getByRole('button', { name: /Update.*Alert/i }).click();
    const refusal = await refused;
    expect(refusal.status()).toBe(400);
    expect(await refusal.text()).toContain('Webhook URL must resolve only to public HTTP(S) destinations.');
    const afterPrivate = await scopeProbe.pg<{ webhook_url: string }>('SELECT webhook_url FROM tracer_useralertmonitor WHERE id=$1', [id]);
    expect(afterPrivate).toEqual([{ webhook_url: destination }]);

    for (const [label, actor, expected] of [
      // UserAlertMonitorView._update_monitor translates a missing scoped row
      // into the existing 400 refusal envelope, rather than exposing a row.
      ['cross-organization', scopeActors.ownerB, 400],
      ['cross-workspace', scopeActors.withWorkspace(owner, scopeActors.emptyWorkspace.id), 400],
      ['viewer', scopeActors.viewer, 403],
    ] as const) {
      const response = await scopeActors.send(actor, 'PATCH', updatePath, { webhook_url: `http://93.184.216.34/${name}-${label}` });
      await testInfo.attach(label, { contentType: 'application/json', body: JSON.stringify(response) });
      expect(response.status, label).toBe(expected);
      expect(response.body).toMatchObject({ status: false, detail: expected === 403
        ? 'Write access denied to this workspace' : 'Unable to update alert. Please try again.' });
      expect(JSON.stringify(response.body)).not.toContain(destination);
      const unchanged = await scopeProbe.pg<{ webhook_url: string }>('SELECT webhook_url FROM tracer_useralertmonitor WHERE id=$1', [id]);
      expect(unchanged, label).toEqual([{ webhook_url: destination }]);
    }
  } finally {
    await testInfo.attach('blocked-external-urls', { contentType: 'application/json', body: JSON.stringify(blocked) });
  }
});
