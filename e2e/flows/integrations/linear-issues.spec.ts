import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { inspectManagedMock } from '../../lib/managed-mock';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { WebhookSink } from '../../lib/sinks';
import { POLL } from '../../lib/state-probe';
import { EXPORT_CHANNELS } from '../../lib/export-sinks';
import { CONNECTIONS_PATH, INTEGRATIONS_ROUTE, shot } from '../../lib/integration-export';
import { inviteOrgMember } from '../../lib/org-member';

test.skip(process.env.E2E_STACK === 'standalone', 'The Linear sink is wired on the Distributed stack only');

// Pinned from frontend/src/sections/settings/integrations (LinearCredentials.jsx:
// the key is checked, then the connection created; IntegrationCard.jsx: the
// "Manage Linear" dialog), frontend/src/pages/dashboard/error-feed/components/
// ErrorMetadataPanel.jsx (the Linear row: Connect, Create issue, View <id>;
// LinearTeamPicker) and integrations/services/linear_service.py: every call is
// one POST <host>/graphql with the personal API key as the Authorization header,
// the connect check asking for the teams and the viewer, the picker for the
// teams, Create issue sending issueCreate with the picked team and
// "[<cluster id>] <title>" (tracer/views/feed/linear_issue_view.py). The stack
// points Linear's host at the webhook sink's linear-export channel
// (INTEGRATIONS_LINEAR_URL, lib/export-sinks.ts), which answers as Linear's
// GraphQL API with one team, E2E Team (stack/webhook-sink/server.mjs), and is
// shared by every worker: a flow's requests are the ones carrying its minted key.
// The issue URL the sink answers is on the sink; the app opens it in a new tab,
// which the flow answers itself, so the browser reaches no vendor.
const ISSUE_SEED_PATH = '/tracer/e2e/feed-issue/';
const LINEAR_HOST = 'https://api.linear.app';
const TEAM = { id: 'e2e-team-0001', name: 'E2E Team', key: 'E2E' };
const ALREADY_CONNECTED =
  'Linear is already connected for this workspace. Disconnect it in Settings > Integrations to connect another key.';
const UI_READY = 30_000;
// Each step's budget; the test's ceiling is their sum.
const STEP = {
  seed: POLL.SPAN_VISIBLE.timeout + 30_000, notConnected: 60_000, viewer: 90_000, connect: 60_000,
  refused: 60_000, create: 90_000, viewerConnected: 60_000, disconnect: 60_000,
};
// Why a Viewer is not offered a Linear action (frontend/src/pages/dashboard/
// error-feed/linearAccess.js), and what a Settings URL closed to a role says
// (frontend/src/routes/components/no-access-state.jsx and no-access-roles.js).
const VIEWER_NO_CONNECT = 'Only an Owner or Admin can connect Linear.';
const VIEWER_NO_CREATE = 'Viewers cannot create issues.';
const NO_ACCESS = "You don't have access to this page";

interface IssueSeed { issue_id: string; cluster_id: string }
interface GraphqlCall { query: string; variables?: Record<string, unknown> }

test('INT-E2E-007: an Error Feed issue becomes a Linear issue', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'INT-E2E-007',
    area: 'integrations',
    userGoal: 'An admin connects Linear from Settings > Integrations and files an Error Feed issue in a Linear team',
    steps: [
      'send a trace and seed an Error Feed issue in its project',
      'open the issue: its Linear row says Not connected; press Connect',
      'a Viewer of the workspace opens the issue and presses Connect, then opens Settings > Integrations by its URL',
      'on Settings > Integrations pick Linear, Connect Linear empty, then enter an API key and Connect Linear, and press Done',
      'read the Linear card under Connections',
      'pick Linear again, enter a second key and Connect Linear: read why it is refused',
      'open the issue again, press Create issue and pick the Linear team',
      'reload the issue and read its Linear row',
      'a Viewer opens a second issue and presses Create issue',
      'open the Linear card, read the Manage dialog, and Disconnect',
    ],
    backendChecks: [
      'the managed stack is the attested mock stack, its API and worker sending Linear only to the declared sink',
      'the empty Connect Linear sent nothing, and the API key reached Linear\'s /graphql twice with the teams and viewer query (the connect check and the create)',
      'one Linear connection is stored for the workspace with https://api.linear.app, and its card shows it',
      'the second key is refused while Linear is connected: it is never stored and the first connection stays',
      'the team picker lists the team Linear answered for the key',
      'Create issue sent one issueCreate with the key, the picked team, the issue\'s cluster id and title, and the issue is linked in PG to the identifier and URL Linear answered',
      'Disconnect soft-deletes the connection, drops it from the connections API, and the issue keeps its link',
      'a Viewer\'s Connect and Create issue send nothing: no integrations or create-issue request, and the second issue stays unlinked in PG',
    ],
    browserChecks: [
      'a Viewer sees Connect disabled with "Only an Owner or Admin can connect Linear." and stays on the issue',
      'a Viewer who opens Settings > Integrations by its URL stays on it and reads why it is closed and who it is open to',
      'a Viewer sees Create issue disabled with "Viewers cannot create issues.", and no team picker opens',
    ],
  }),
}, async ({ page, browser, actor, probe }, testInfo) => {
  test.setTimeout(Object.values(STEP).reduce((a, b) => a + b, 0));
  inspectManagedMock({ exportSinks: true });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const apiKey = `lin_api_e2e_${suffix}`;
  const otherKey = `lin_api_e2e_other_${suffix}`;
  const sink = new WebhookSink();
  const mine = async (key: string) => (await sink.received(EXPORT_CHANNELS.linear))
    .filter((r) => r.headers.authorization === key);
  const queries = async (key: string) => (await mine(key)).map((r) => {
    const body = r.body as GraphqlCall;
    const op = /issueCreate/.test(body.query) ? 'issueCreate'
      : /viewer/.test(body.query) ? 'teams+viewer' : /teams/.test(body.query) ? 'teams' : body.query;
    return `${r.method} ${new URL(r.path, 'http://sink').pathname.replace(`/r/${EXPORT_CHANNELS.linear}/as/linear`, '')} ${r.status} ${op}`;
  });
  const linearConnections = async () => probe.pg<{ id: string; host_url: string; deleted: boolean }>(
    `SELECT id::text, host_url, deleted FROM integrations_connection
      WHERE organization_id = $1 AND platform = 'linear' ORDER BY created_at`, [actor.organizationId]);
  // The issue page's Linear row (ConnectorRow): its name, its state and its action.
  const linearRow = (action: string) => page.locator('.MuiStack-root')
    .filter({ has: page.getByText('Linear', { exact: true }) })
    .filter({ has: page.getByText(action, { exact: true }) }).last();

  let projectId = '';
  const seedIssue = async (title: string) => ({
    ...(await actor.api.post<IssueSeed>(ISSUE_SEED_PATH, {
      project_id: projectId, title, source: 'scanner', severity: 'high', issue_category: 'Timeout',
      status: 'for_review', occurrences: 1,
    })),
    title,
  });
  const viewer = await inviteOrgMember(actor, `e2e-int-ln-viewer-${suffix}`, 1);
  const viewerPage = await (await viewer.browser(browser)).newPage();
  const viewerPosts: string[] = [];
  viewerPage.on('request', (r) => {
    const path = new URL(r.url()).pathname;
    if (r.method() !== 'GET' && (path.startsWith('/integrations/') || /linear/i.test(path))) viewerPosts.push(path);
  });
  // The issue page's Linear row as the Viewer sees it, and its action.
  const viewerRow = (action: string) => viewerPage.locator('.MuiStack-root')
    .filter({ has: viewerPage.getByText('Linear', { exact: true }) })
    .filter({ has: viewerPage.getByRole('button', { name: action, exact: true }) }).last();
  const expectRefused = async (action: string, reason: string) => {
    const button = viewerRow(action).getByRole('button', { name: action, exact: true });
    await expect(button).toHaveAttribute('aria-disabled', 'true', { timeout: UI_READY });
    await button.click({ force: true });
    await button.hover();
    await expect(viewerPage.getByRole('tooltip')).toHaveText(reason, { timeout: UI_READY });
  };

  const issue = await test.step('seed an Error Feed issue', async () => {
    const req = await request.newContext();
    const projectName = `e2e-int-ln-${suffix}`;
    await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName,
      rootName: `e2e.root-${suffix}`,
    });
    await expect.poll(async () => (await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
      [projectName, actor.organizationId]))[0]?.id, POLL.SPAN_VISIBLE).not.toBeUndefined();
    const [project] = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2', [projectName, actor.organizationId]);
    projectId = project.id;
    const seeded = await seedIssue(`e2e-linear-${suffix}`);
    await testInfo.attach('seeded-issue', { contentType: 'application/json', body: JSON.stringify(seeded) });
    return seeded;
  }, { timeout: STEP.seed });
  const issuePage = `/dashboard/error-feed/${issue.cluster_id}`;

  await test.step('the issue offers to connect Linear and lands on Settings > Integrations', async () => {
    await page.goto(issuePage, { waitUntil: 'domcontentloaded' });
    await expect(linearRow('Connect').getByText('Not connected', { exact: true })).toBeVisible({ timeout: UI_READY });
    await shot(page, testInfo, 'issue-not-connected');
    await linearRow('Connect').getByText('Connect', { exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${INTEGRATIONS_ROUTE}/?$`), { timeout: UI_READY });
    await expect(page.getByText('Available Platforms', { exact: true })).toBeVisible({ timeout: UI_READY });
  }, { timeout: STEP.notConnected });

  await test.step('a Viewer is not offered Connect, and Settings > Integrations says why it is closed', async () => {
    await viewerPage.goto(issuePage, { waitUntil: 'domcontentloaded' });
    await expect(viewerRow('Connect').getByText('Not connected', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expectRefused('Connect', VIEWER_NO_CONNECT);
    await expect(viewerPage).toHaveURL(new RegExp(`${issuePage}$`));
    await shot(viewerPage, testInfo, 'viewer-no-connect');
    await viewerPage.goto(INTEGRATIONS_ROUTE, { waitUntil: 'domcontentloaded' });
    await expect(viewerPage.getByText(NO_ACCESS, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(viewerPage.getByText(/^This page needs the organization role Owner, Admin or Member, or the workspace role Admin or Member\./))
      .toBeVisible();
    await expect(viewerPage).toHaveURL(new RegExp(`${INTEGRATIONS_ROUTE}/?$`));
    await expect(viewerPage.getByText('Available Platforms', { exact: true })).toHaveCount(0);
    await shot(viewerPage, testInfo, 'viewer-settings-closed');
  }, { timeout: STEP.viewer });

  const drawer = page.locator('.MuiDrawer-paper').filter({ hasText: /^Add Integration/ });
  const openLinear = async () => {
    await page.locator('.MuiCardActionArea-root').filter({ hasText: /^Linear\s*Issue tracking/ }).click();
    await expect(drawer.getByText('Enter your Linear API key', { exact: true })).toBeVisible({ timeout: UI_READY });
  };
  const connectionId = await test.step('connect Linear from its card', async () => {
    let posts = 0;
    const count = (r: { method(): string; url(): string }) => {
      if (r.method() === 'POST' && new URL(r.url()).pathname.startsWith('/integrations/')) posts += 1;
    };
    page.on('request', count);
    await openLinear();
    await drawer.getByRole('button', { name: 'Connect Linear' }).click();
    await expect(drawer.getByText('API key is required', { exact: true })).toBeVisible();
    expect(posts).toBe(0);
    page.off('request', count);
    await drawer.getByLabel('API Key').fill(apiKey);
    await shot(page, testInfo, 'credentials');
    const created = page.waitForResponse((r) => r.request().method() === 'POST'
      && new URL(r.url()).pathname === CONNECTIONS_PATH, { timeout: UI_READY });
    await drawer.getByRole('button', { name: 'Connect Linear' }).click();
    const res = await created;
    expect(res.status(), await res.text()).toBe(201);
    expect(res.request().postDataJSON()).toMatchObject({ platform: 'linear', host_url: LINEAR_HOST,
      display_name: 'Linear', credentials: { api_key: apiKey } });
    // The drawer drops its "Add Integration" title once connected.
    await expect(page.getByText('Integration Connected!')).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText('Your integration is now active.', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /^(View Integration|Done)$/ })).toHaveText(['Done']);
    await shot(page, testInfo, 'connected');
    await page.getByRole('button', { name: 'Done', exact: true }).click();
    await expect(page.getByText('Integration Connected!')).toHaveCount(0, { timeout: UI_READY });
    return ((await res.json()) as { result: { id: string } }).result.id;
  }, { timeout: STEP.connect });

  await testInfo.attach('connection', { contentType: 'text/plain', body: connectionId });
  expect(await queries(apiKey)).toEqual(['POST /graphql 200 teams+viewer', 'POST /graphql 200 teams+viewer']);
  expect(await linearConnections()).toEqual([{ id: connectionId, host_url: LINEAR_HOST, deleted: false }]);
  const card = page.locator('.MuiCard-root').filter({
    has: page.getByTestId('integration-card-destination').filter({ hasText: LINEAR_HOST }),
  });
  await expect(card).toHaveCount(1, { timeout: UI_READY });
  await expect(card.getByText('Linear', { exact: true })).toBeVisible();
  await expect(card.getByText('Connected', { exact: true })).toBeVisible();
  await expect(card.getByText('Action integration', { exact: true })).toBeVisible();
  await shot(page, testInfo, 'connections-card');

  await test.step('a second key is refused while Linear is connected', async () => {
    await openLinear();
    await drawer.getByLabel('API Key').fill(otherKey);
    const created = page.waitForResponse((r) => r.request().method() === 'POST'
      && new URL(r.url()).pathname === CONNECTIONS_PATH, { timeout: UI_READY });
    await drawer.getByRole('button', { name: 'Connect Linear' }).click();
    expect((await created).status()).toBe(400);
    await expect(page.getByText(ALREADY_CONNECTED, { exact: true })).toHaveCount(1, { timeout: UI_READY });
    await expect(drawer.getByRole('alert').getByText(ALREADY_CONNECTED, { exact: true })).toBeVisible();
    await shot(page, testInfo, 'second-key-refused');
    expect(await linearConnections()).toEqual([{ id: connectionId, host_url: LINEAR_HOST, deleted: false }]);
    await drawer.getByText('Add Integration', { exact: true }).locator('xpath=following-sibling::button').click();
    await expect(drawer).toHaveCount(0);
  }, { timeout: STEP.refused });

  await test.step('Create issue files the issue in the picked Linear team', async () => {
    await page.goto(issuePage, { waitUntil: 'domcontentloaded' });
    await expect(linearRow('Create issue').getByText('Connected', { exact: true })).toBeVisible({ timeout: UI_READY });
    await linearRow('Create issue').getByText('Create issue', { exact: true }).click();
    const picker = page.getByRole('dialog').filter({ hasText: 'Create Linear Issue' });
    await expect(picker.getByText('Select a team to create the issue in.', { exact: true })).toBeVisible();
    await expect(picker.getByText(TEAM.name, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(picker.getByText(TEAM.key, { exact: true })).toBeVisible();
    await shot(page, testInfo, 'team-picker');
    // The app opens the new issue in a tab; the flow answers it, so no request leaves.
    const issueUrl = new RegExp(`/r/linear-issues/E2E-1$`);
    await page.context().route(issueUrl, (route) => route.fulfill({ contentType: 'text/html', body: '<title>E2E-1</title>' }));
    const opened = page.context().waitForEvent('page', { timeout: UI_READY });
    await picker.getByText(TEAM.name, { exact: true }).click();
    const tab = await opened;
    await expect(page.getByText('Created E2E-1', { exact: true })).toBeVisible({ timeout: UI_READY });
    expect(tab.url()).toMatch(issueUrl);
    await tab.close();

    // After the connect check and the create, the issue page and the picker ask for
    // the teams (as often as they load), and Create issue sends one issueCreate.
    const calls = await mine(apiKey);
    const ops = await queries(apiKey);
    expect(ops.slice(0, 2)).toEqual(['POST /graphql 200 teams+viewer', 'POST /graphql 200 teams+viewer']);
    expect(ops.slice(2).filter((o) => o !== 'POST /graphql 200 teams')).toEqual(['POST /graphql 200 issueCreate']);
    expect(ops.slice(2, ops.indexOf('POST /graphql 200 issueCreate'))).toContain('POST /graphql 200 teams');
    const created = calls[ops.indexOf('POST /graphql 200 issueCreate')].body as GraphqlCall;
    expect(created.variables).toMatchObject({ teamId: TEAM.id, title: `[${issue.cluster_id}] ${issue.title}` });
    expect(String(created.variables?.description)).toContain(issue.cluster_id);
    expect(await queries(otherKey)).toEqual(['POST /graphql 200 teams+viewer', 'POST /graphql 200 teams+viewer']);
    const [linked] = await probe.pg<{ external_issue_id: string; external_issue_url: string }>(
      'SELECT external_issue_id, external_issue_url FROM tracer_trace_error_group WHERE id = $1', [issue.issue_id]);
    expect(linked.external_issue_id).toBe('E2E-1');
    expect(linked.external_issue_url).toMatch(issueUrl);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(linearRow('View E2E-1').getByText('E2E-1', { exact: true })).toBeVisible({ timeout: UI_READY });
    await shot(page, testInfo, 'issue-linked');
  }, { timeout: STEP.create });

  await test.step('a Viewer is not offered Create issue', async () => {
    const second = await seedIssue(`e2e-linear-viewer-${suffix}`);
    await viewerPage.goto(`/dashboard/error-feed/${second.cluster_id}`, { waitUntil: 'domcontentloaded' });
    await expect(viewerRow('Create issue').getByText('Connected', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expectRefused('Create issue', VIEWER_NO_CREATE);
    await expect(viewerPage.getByText('Create Linear Issue', { exact: true })).toHaveCount(0);
    await shot(viewerPage, testInfo, 'viewer-no-create');
    expect(viewerPosts).toEqual([]);
    const [unlinked] = await probe.pg<{ external_issue_id: string | null }>(
      'SELECT external_issue_id FROM tracer_trace_error_group WHERE id = $1', [second.issue_id]);
    expect(unlinked.external_issue_id || null).toBeNull();
    await viewer.dispose();
  }, { timeout: STEP.viewerConnected });

  await test.step('Disconnect removes the connection', async () => {
    await page.goto(INTEGRATIONS_ROUTE, { waitUntil: 'domcontentloaded' });
    await card.click({ timeout: UI_READY });
    const manage = page.getByRole('dialog', { name: 'Manage Linear' });
    await expect(manage.getByText(
      /^Connected .+\. Disconnecting will remove the API key and disable issue creation from Error Feed\.$/))
      .toBeVisible();
    await expect(manage.getByRole('button')).toHaveText(['Cancel', 'Disconnect']);
    await shot(page, testInfo, 'manage-dialog');
    await manage.getByRole('button', { name: 'Disconnect', exact: true }).click();
    await expect(card).toHaveCount(0, { timeout: UI_READY });
    expect(await linearConnections()).toEqual([{ id: connectionId, host_url: LINEAR_HOST, deleted: true }]);
    const listed = await actor.api.get<{ result: { connections: { id: string }[] } }>(CONNECTIONS_PATH);
    expect(listed.result.connections.map((c) => c.id)).not.toContain(connectionId);
    const [kept] = await probe.pg<{ external_issue_id: string }>(
      'SELECT external_issue_id FROM tracer_trace_error_group WHERE id = $1', [issue.issue_id]);
    expect(kept.external_issue_id).toBe('E2E-1');
    await shot(page, testInfo, 'after-disconnect');
  }, { timeout: STEP.disconnect });
});
