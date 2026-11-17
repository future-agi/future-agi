import type { Locator, Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { INTEGRATIONS_ROUTE, shot } from '../../lib/integration-export';
import { mintScope } from '../../lib/sinks';
import { posthogHost } from '../../lib/export-sinks';

// Pinned from frontend/src/sections/settings/integrations: constants.js
// (PLATFORMS, UNLISTED_DESTINATIONS, PLATFORM_CAPABILITY, DATADOG_SITES),
// IntegrationsList.jsx, AddIntegrationWizard.jsx and its credentials steps,
// access.js (INTEGRATION_ADMIN_ONLY). The page offers exactly the platforms an
// end-to-end flow proves (flows/integrations/), Slack (ERR-E2E-001..003) and,
// where the deployment's capabilities allow Error Feed, Linear (INT-E2E-007);
// the stack's backend allows Error Feed (e2e/stack/backend/settings_e2e.py),
// so Linear is offered here. Slack's two entry points (the wizard's card and the
// page's) start the Slack authorization, answered by the stack's mock Slack
// (e2e/stack/mock-slack/server.mjs, workspace "E2E Slack Workspace"), which
// returns to the page with ?slack=connected. A Member reads the page with
// every write control disabled; a workspace Admin, whom the API lets manage
// integrations (futureagi/integrations/permissions.py), finds it in the
// Settings menu with every control enabled.
const UI_READY = 30_000;
// The Settings page every role opens (layouts/dashboard/config-navigation.jsx: Profile).
const PROFILE_ROUTE = '/dashboard/settings/profile-settings';
const CAPABILITIES_PATH = '/api/capabilities/';
const CONNECTIONS_PATH = '/integrations/connections/';
const SLACK_WORKSPACE = 'E2E Slack Workspace';

const GRID = [
  ['Datadog', 'AI Gateway request logs and metrics'],
  ['PostHog', 'AI Gateway request logs as PostHog events'],
  ['Mixpanel', 'AI Gateway request logs as Mixpanel events'],
  ['Cloud Storage', 'Request log archival in Amazon S3'],
  ['Slack', 'Send Error Feed alerts to a Slack channel'],
  ['Linear', 'Issue tracking for Error Feed clusters'],
];
// constants.js wizardDescription of each platform (StepPlatformSelect.jsx).
const WIZARD = [
  ['Datadog', 'Export AI Gateway request logs and metrics to Datadog'],
  ['PostHog', 'Export AI Gateway request logs to PostHog as events'],
  ['Mixpanel', 'Export AI Gateway request logs to Mixpanel as events'],
  ['Cloud Storage', 'Archive AI Gateway request logs in Amazon S3'],
  ['Slack', 'Connect a Slack workspace for Error Feed alerts'],
  ['Linear', 'Create Linear issues from Error Feed clusters'],
];
const NOT_OFFERED = /Langfuse|PagerDuty|Message Queue|Azure|Google Cloud Storage|Coming soon|Preview/;
const DATADOG_SITES = ['US1 (datadoghq.com)', 'US3 (us3.datadoghq.com)', 'US5 (us5.datadoghq.com)',
  'EU1 (datadoghq.eu)', 'AP1 (ap1.datadoghq.com)', 'US1-FED (ddog-gov.com)'];
const AWS_REGIONS = ['us-east-1', 'us-east-2', 'us-west-1', 'us-west-2', 'eu-west-1', 'eu-west-2', 'eu-central-1',
  'ap-southeast-1', 'ap-northeast-1'];

const cardTexts = (rows: string[][]) => rows.map(([name, text]) => new RegExp(`^${name}\\s*${text}$`));
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// Since the design-system pass a platform is a Section (components/section/Section.jsx), a
// role="button" box, not an MUI card. The page's cards are the buttons of the grid that follows
// the "Available Platforms" heading (IntegrationsList.jsx); a card the user may not use carries
// aria-disabled, which toBeDisabled reads.
const gridCards = (page: Page) => page.getByText('Available Platforms', { exact: true })
  .locator('xpath=following-sibling::div[1]').getByRole('button');

/** Toggles the password field labelled `label` inside `drawer` to plain text with its eye
 * button, then checks the wizard is still open: an eye click computed before the field's
 * error text moved it could land outside the drawer and close the wizard on some runs. */
async function reveal(drawer: Locator, label: string) {
  const input = drawer.getByLabel(label);
  await expect(input).toHaveAttribute('type', 'password');
  const eye = input.locator('xpath=ancestor::div[contains(@class,"MuiInputBase-root")]//button');
  await expect(eye).toBeVisible();
  await eye.click({ timeout: UI_READY });
  await expect(input).toHaveAttribute('type', 'text');
  await expect(drawer).toBeVisible();
}

test('INT-E2E-006: Settings > Integrations offers only what connects', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'INT-E2E-006',
    area: 'integrations',
    userGoal: 'An admin reads Settings > Integrations and finds only platforms that connect, each asking for what it uses; a Member reads it without write controls; a workspace Admin finds it in the Settings menu and manages it',
    steps: [
      'an Owner opens Settings > Integrations from the Settings menu, with nothing connected',
      'open Add Integration and read the platforms it offers',
      'open each export platform\'s credentials step, read its fields and choices, Validate & Continue empty, and go Back',
      'open Linear\'s step, read its field, Connect Linear empty, and go Back',
      'close the wizard, and open it again from the empty page\'s Add Integration',
      'connect Slack from the Add Integration wizard, then Disconnect it from its card',
      'connect Slack from its card on the page, then Disconnect it from its card',
      'open the workspace\'s own Settings > Integrations page',
      'with a PostHog connection in the current workspace, open another workspace\'s Integrations page and connect PostHog there',
      'a Member opens Settings > Integrations',
      'a workspace Admin opens Settings > Integrations from the Settings menu',
    ],
    backendChecks: [
      'the deployment\'s capabilities allow Error Feed, so Linear is offered',
      'another workspace\'s page reads and creates that workspace\'s connections: its list and create requests name it (X-Workspace-Id), its new connection is listed by the API in that workspace only, and the current workspace\'s connection is not shown there',
      'each Slack entry point stores one Slack connection for the workspace, and Disconnect removes it from the connections API',
    ],
    browserChecks: [
      'the page grid and the wizard list exactly Datadog, PostHog, Mixpanel, Cloud Storage, Slack and Linear with their descriptions, and no Langfuse, PagerDuty, Message Queue, Azure, GCS or "Coming soon"',
      'the empty state says what the page does: export, alerts and issues',
      'before a platform is picked the stepper names only Platform and Credentials, no Project step',
      'Datadog offers its six sites and an API key, and no Application Key; PostHog its two regions or a self-hosted address; Mixpanel a token and an optional API secret with its 5-day note; Cloud Storage only Amazon S3, its nine regions, keys and prefix; Linear an API key',
      'the empty page\'s Add Integration opens the same wizard',
      'each empty Validate & Continue names the missing fields and sends nothing: no integrations request leaves the browser and the organization has no connection',
      'after each Slack authorization the page says "Slack workspace connected successfully." and lists the workspace under Connections; after Disconnect it is gone',
      'the workspace\'s Integrations page has one Integrations heading and offers the same platforms with the same descriptions',
      'another workspace\'s page shows that workspace\'s connections only: none at first, then the one connected there',
      'a Member sees Add Integration and every platform card disabled with "Owner or Admin only", and no Integrations entry in the Settings menu',
      'a workspace Admin (an organization Member) finds Integrations in the Settings menu, and Add Integration and every platform card enabled',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(420_000);
  const caps = await scopes.send<{ features: Record<string, { allowed: boolean }> }>(
    scopes.ownerA, 'GET', CAPABILITIES_PATH);
  expect(caps.status).toBe(200);
  expect(caps.body.features.error_feed?.allowed ?? false).toBe(true);

  const owner = await (await scopes.openContext(browser, scopes.ownerA)).newPage();
  const validates: string[] = [];
  owner.on('request', (r) => { if (r.method() === 'POST' && r.url().includes('/integrations/')) validates.push(r.url()); });
  await owner.goto('/dashboard/settings/mcp-server', { waitUntil: 'domcontentloaded' });
  // The Settings menu's Integrations link (layouts/dashboard/config-navigation.jsx useNavSettingsData).
  await owner.locator(`a[href="${INTEGRATIONS_ROUTE}"]`).filter({ hasText: /^Integrations$/ }).click({ timeout: UI_READY });
  await expect(owner).toHaveURL(new RegExp(`${INTEGRATIONS_ROUTE}/?$`), { timeout: UI_READY });

  await test.step('the page offers exactly what connects', async () => {
    await expect(owner.getByText('Available Platforms', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(owner.getByText('Connect external platforms', { exact: true })).toBeVisible();
    await expect(owner.getByText('No integrations connected', { exact: true })).toBeVisible();
    await expect(owner.getByText(
      'Export request logs and metrics, or send alerts and issues to an external platform.', { exact: true }))
      .toBeVisible();
    await expect(gridCards(owner)).toHaveText(cardTexts(GRID));
    await expect(owner.getByText(NOT_OFFERED)).toHaveCount(0);
    await shot(owner, testInfo, '01-page');
  });

  // The Add Integration drawer (the page's other drawers hold no such title).
  const drawer = owner.locator('.MuiDrawer-paper').filter({ hasText: /^Add Integration/ });
  // The wizard's platform choices: the role="button" boxes after its "Select the platform" line
  // (StepPlatformSelect.jsx), which leaves out the close button.
  const choices = () => drawer.getByText('Select the platform you want to connect', { exact: true })
    .locator('xpath=following-sibling::div[@role="button"]');
  const pick = async (name: string) => {
    await choices().filter({ hasText: new RegExp(`^${escape(name)}`) }).click({ timeout: UI_READY });
    await expect(drawer.getByRole('button', { name: 'Validate & Continue' })).toBeVisible({ timeout: UI_READY });
  };
  const back = async () => {
    await drawer.getByRole('button', { name: 'Back', exact: true }).click({ timeout: UI_READY });
    await expect(drawer.getByText('Select the platform you want to connect', { exact: true })).toBeVisible();
  };
  // Captures taken while the wizard is open stay at viewport size: a full-page capture
  // resizes the viewport, and the wizard was gone right after one on some runs.
  const wizardShot = (name: string) => owner.screenshot({ path: testInfo.outputPath(`${name}.png`) });

  await test.step('Add Integration offers the same platforms', async () => {
    await owner.getByRole('button', { name: 'Add Integration' }).first().click({ timeout: UI_READY });
    await expect(drawer.getByText('Add Integration', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(drawer.getByText('Select the platform you want to connect', { exact: true })).toBeVisible();
    await expect(drawer.locator('.MuiStepLabel-label')).toHaveText(['Platform', 'Credentials']);
    await expect(choices()).toHaveText(cardTexts(WIZARD));
    await expect(drawer.getByText(NOT_OFFERED)).toHaveCount(0);
    await wizardShot('02-wizard');
  });

  await test.step('Datadog asks for its site and an API key', async () => {
    await pick('Datadog');
    await expect(drawer.locator('.MuiStepLabel-label')).toHaveText(['Platform', 'Credentials', 'Sync Settings']);
    await expect(drawer.getByLabel('Application Key (Optional)')).toHaveCount(0);
    await expect(drawer.getByText(/dashboard/i)).toHaveCount(0);
    await drawer.getByLabel('Datadog Site').click({ timeout: UI_READY });
    await expect(owner.getByRole('option')).toHaveText(DATADOG_SITES);
    await wizardShot('03-datadog-sites');
    // Close the Site list by choosing its selected option again: an Escape that
    // lands before the list takes focus closed the whole wizard on some runs.
    await owner.getByRole('option', { selected: true }).click({ timeout: UI_READY });
    await expect(owner.getByRole('option')).toHaveCount(0, { timeout: UI_READY });
    await expect(drawer).toBeVisible();
    await drawer.getByRole('button', { name: 'Validate & Continue' }).click({ timeout: UI_READY });
    await expect(drawer.getByText('API key is required', { exact: true })).toBeVisible();
    await reveal(drawer, 'API Key');
    await wizardShot('04-datadog');
    await back();
  });

  await test.step('PostHog asks for its region or a self-hosted address, and a project API key', async () => {
    await pick('PostHog');
    await expect(drawer.getByLabel('PostHog Region').locator('option')).toHaveText(['US Cloud', 'EU Cloud']);
    await drawer.getByRole('button', { name: 'Using self-hosted PostHog?' }).click({ timeout: UI_READY });
    await expect(drawer.getByLabel('Host URL')).toHaveValue('');
    await drawer.getByRole('button', { name: 'Validate & Continue' }).click({ timeout: UI_READY });
    await expect(drawer.getByText('Host URL is required', { exact: true })).toBeVisible();
    await expect(drawer.getByText('Project API key is required', { exact: true })).toBeVisible();
    await wizardShot('05-posthog-self-hosted');
    await drawer.getByRole('button', { name: 'Use PostHog Cloud' }).click({ timeout: UI_READY });
    await expect(drawer.getByLabel('PostHog Region')).toHaveValue('https://us.i.posthog.com');
    await reveal(drawer, 'Project API Key');
    await back();
  });

  await test.step('Mixpanel asks for a project token and an optional API secret', async () => {
    await pick('Mixpanel');
    await expect(drawer.getByText(
      'Optional. With the API secret, request logs go to Mixpanel\'s /import API, which also accepts logs older than 5 days; without it, to /track, which accepts the last 5 days.',
      { exact: true })).toBeVisible();
    await drawer.getByRole('button', { name: 'Validate & Continue' }).click({ timeout: UI_READY });
    await expect(drawer.getByText('Project token is required', { exact: true })).toBeVisible();
    await reveal(drawer, 'API Secret (Optional)');
    await wizardShot('06-mixpanel');
    await back();
  });

  await test.step('Cloud Storage offers Amazon S3 only, with its regions, keys and prefix', async () => {
    await pick('Cloud Storage');
    // The storage choices: the role="button" boxes under "Configure cloud storage destination"
    // (CloudStorageCredentials.jsx), listed by isDestinationListed.
    await expect(drawer.getByText('Configure cloud storage destination', { exact: true })
      .locator('xpath=following-sibling::div[1]').getByRole('button')).toHaveText(['Amazon S3']);
    await drawer.getByLabel('Region').click({ timeout: UI_READY });
    await expect(owner.getByRole('option')).toHaveText(AWS_REGIONS);
    // As for the Datadog Site list: choose the selected region again, never Escape.
    await owner.getByRole('option', { selected: true }).click({ timeout: UI_READY });
    await expect(owner.getByRole('option')).toHaveCount(0, { timeout: UI_READY });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByLabel('Key Prefix (Optional)')).toBeVisible();
    await drawer.getByRole('button', { name: 'Validate & Continue' }).click({ timeout: UI_READY });
    for (const missing of ['Bucket name is required', 'Access key is required', 'Secret key is required']) {
      await expect(drawer.getByText(missing, { exact: true })).toBeVisible();
    }
    await reveal(drawer, 'Secret Access Key');
    await wizardShot('07-s3');
    await back();
  });

  await test.step('Linear asks for its API key', async () => {
    await choices().filter({ hasText: /^Linear/ }).click({ timeout: UI_READY });
    await expect(drawer.getByText('Enter your Linear API key', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(drawer.locator('.MuiStepLabel-label')).toHaveText(['Platform', 'Credentials']);
    await drawer.getByRole('button', { name: 'Connect Linear' }).click({ timeout: UI_READY });
    await expect(drawer.getByText('API key is required', { exact: true })).toBeVisible();
    await reveal(drawer, 'API Key');
    await wizardShot('07b-linear');
    await back();
  });

  const closeWizard = async () => {
    await drawer.getByText('Add Integration', { exact: true }).locator('xpath=following-sibling::button').click({ timeout: UI_READY });
    await expect(drawer).toHaveCount(0);
  };
  await test.step('the wizard closes and nothing was sent', async () => {
    await closeWizard();
    expect(validates).toEqual([]);
    const rows = await probe.pg<{ n: string }>(
      'SELECT count(*)::text AS n FROM integrations_connection WHERE organization_id = $1',
      [scopes.ownerA.organizationId]);
    expect(rows).toEqual([{ n: '0' }]);
    await shot(owner, testInfo, '08-closed');
  });

  await test.step('the empty page\'s Add Integration opens the same wizard', async () => {
    // The empty state (IntegrationsList.jsx) is a panel with no button of its own; the page's one
    // Add Integration button is in its header, above it.
    await expect(owner.getByText('No integrations connected', { exact: true })).toBeVisible();
    await owner.getByRole('button', { name: 'Add Integration' }).click({ timeout: UI_READY });
    await expect(drawer.getByText('Select the platform you want to connect', { exact: true })).toBeVisible();
    await expect(choices()).toHaveText(cardTexts(WIZARD));
    await closeWizard();
  });

  const slackConnections = async () =>
    (await scopes.send<{ result: { connections: { id: string; platform: string }[] } }>(
      scopes.ownerA, 'GET', CONNECTIONS_PATH)).body.result.connections.filter((c) => c.platform === 'slack');
  /** Back from the Slack authorization: the notice, the card, one stored connection; then Disconnect. */
  const connectedThenDisconnect = async (name: string) => {
    await expect(owner).toHaveURL(/[?&]slack=connected/, { timeout: UI_READY });
    await expect(owner.getByText('Slack workspace connected successfully.', { exact: true }))
      .toBeVisible({ timeout: UI_READY });
    await expect(owner.getByText('Connections (1)', { exact: true })).toBeVisible({ timeout: UI_READY });
    expect((await slackConnections()).length).toBe(1);
    await shot(owner, testInfo, `${name}-connected`);
    await owner.getByText(SLACK_WORKSPACE, { exact: true }).click();
    const manage = owner.getByRole('dialog', { name: `Manage ${SLACK_WORKSPACE}` });
    await expect(manage.getByRole('button')).toHaveText(['Cancel', 'Reconnect Slack', 'Disconnect']);
    await manage.getByRole('button', { name: 'Disconnect', exact: true }).click();
    await expect(owner.getByText(SLACK_WORKSPACE, { exact: true })).toHaveCount(0, { timeout: UI_READY });
    await expect(owner.getByText('No integrations connected', { exact: true })).toBeVisible({ timeout: UI_READY });
    expect(await slackConnections()).toEqual([]);
  };

  // The mock Slack OAuth client is wired only in the Distributed E2E stack (as for
  // ERR-E2E-001..003). Standalone has no Slack OAuth client, so the backend refuses
  // the authorization up front (integrations/services/slack_oauth.py:38) and the
  // page says so; the walk checks that refusal and that nothing is stored.
  const slackOAuthWired = process.env.E2E_STACK !== 'standalone';
  const refusedWithoutOAuth = async (name: string) => {
    await expect(owner.getByText('Slack OAuth is not configured.', { exact: true }))
      .toBeVisible({ timeout: UI_READY });
    expect(await slackConnections()).toEqual([]);
    await shot(owner, testInfo, `${name}-not-configured`);
  };

  await test.step('Slack connects from the Add Integration wizard', async () => {
    await owner.getByRole('button', { name: 'Add Integration' }).first().click({ timeout: UI_READY });
    await choices().filter({ hasText: /^Slack/ }).click({ timeout: UI_READY });
    if (slackOAuthWired) await connectedThenDisconnect('10-slack-wizard');
    else await refusedWithoutOAuth('10-slack-wizard');
  });

  await test.step('Slack connects from its card on the page', async () => {
    await owner.goto(INTEGRATIONS_ROUTE, { waitUntil: 'domcontentloaded' });
    await gridCards(owner).filter({ hasText: /^Slack/ }).click({ timeout: UI_READY });
    if (slackOAuthWired) await connectedThenDisconnect('11-slack-card');
    else await refusedWithoutOAuth('11-slack-card');
  });

  await test.step('the workspace\'s Integrations page offers the same platforms', async () => {
    await owner.goto(`/dashboard/settings/workspace/${scopes.ownerA.workspaceId}/integrations`,
      { waitUntil: 'domcontentloaded' });
    await expect(owner.getByText('Available Platforms', { exact: true })).toBeVisible({ timeout: UI_READY });
    // One page heading: no second title and line above the Integrations page's own.
    await expect(owner.getByText('Connect external platforms', { exact: true })).toHaveCount(1);
    await expect(owner.getByText('Manage workspace integrations')).toHaveCount(0);
    await expect(gridCards(owner)).toHaveText(cardTexts(GRID));
    await expect(owner.getByText(NOT_OFFERED)).toHaveCount(0);
    await shot(owner, testInfo, '12-workspace-page');
  });

  await test.step('another workspace\'s Integrations page reads and adds that workspace\'s connections', async () => {
    const other = scopes.emptyWorkspace.id;
    const listed = async (workspaceId: string) => (await scopes.send<{ result: { connections: { id: string }[] } }>(
      scopes.withWorkspace(scopes.ownerA, workspaceId), 'GET', CONNECTIONS_PATH)).body.result.connections
      .map((c) => c.id);
    // A connection in the current (default) workspace, made through the API.
    const here = await scopes.send<{ result: { id: string } }>(scopes.ownerA, 'POST', CONNECTIONS_PATH, {
      platform: 'posthog', host_url: posthogHost(mintScope('posthog-ws-current')), display_name: 'PostHog here',
      credentials: { api_key: `phc_e2e_ws_${Date.now().toString(36)}` }, backfill_option: 'all',
    });
    expect(here.status).toBe(201);
    const sent: { method: string; path: string; workspace: string | undefined }[] = [];
    const record = (r: { method(): string; url(): string; headers(): Record<string, string> }) => {
      const path = new URL(r.url()).pathname;
      if (path.startsWith('/integrations/connections/')) {
        sent.push({ method: r.method(), path, workspace: r.headers()['x-workspace-id'] });
      }
    };
    owner.on('request', record);
    await owner.goto(`/dashboard/settings/workspace/${other}/integrations`, { waitUntil: 'domcontentloaded' });
    await expect(owner.getByText('Available Platforms', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(owner.getByText('No integrations connected', { exact: true })).toBeVisible();
    await expect(owner.getByText('PostHog here', { exact: true })).toHaveCount(0);
    await shot(owner, testInfo, '12b-other-workspace');

    const channel = mintScope('posthog-ws-other');
    await owner.getByRole('button', { name: 'Add Integration' }).first().click({ timeout: UI_READY });
    await choices().filter({ hasText: /^PostHog/ }).click({ timeout: UI_READY });
    await drawer.getByRole('button', { name: 'Using self-hosted PostHog?' }).click({ timeout: UI_READY });
    await drawer.getByLabel('Host URL').fill(posthogHost(channel));
    await drawer.getByLabel('Project API Key').fill(`phc_e2e_ws_other_${Date.now().toString(36)}`);
    await drawer.getByRole('button', { name: 'Validate & Continue' }).click({ timeout: UI_READY });
    const created = owner.waitForResponse((r) => r.request().method() === 'POST'
      && new URL(r.url()).pathname === CONNECTIONS_PATH, { timeout: UI_READY });
    await drawer.getByRole('button', { name: 'Connect Integration' }).click({ timeout: UI_READY });
    const res = await created;
    expect(res.status(), await res.text()).toBe(201);
    const made = ((await res.json()) as { result: { id: string } }).result.id;
    await testInfo.attach('workspace-connections', { contentType: 'application/json',
      body: JSON.stringify({ organizationId: scopes.ownerA.organizationId,
        currentWorkspace: scopes.ownerA.workspaceId, currentConnection: here.body.result.id,
        otherWorkspace: other, otherConnection: made, channel }) });
    await owner.getByRole('button', { name: 'Done', exact: true }).click({ timeout: UI_READY });
    await expect(owner.getByText('Connections (1)', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(owner.getByText('PostHog here', { exact: true })).toHaveCount(0);
    await shot(owner, testInfo, '12c-other-workspace-connected');
    owner.off('request', record);

    expect(sent.filter((r) => r.workspace !== other)).toEqual([]);
    expect(sent.map((r) => `${r.method} ${r.path}`)).toEqual(expect.arrayContaining([
      `GET ${CONNECTIONS_PATH}`, `POST ${CONNECTIONS_PATH}validate/`, `POST ${CONNECTIONS_PATH}`]));
    expect(await listed(other)).toEqual([made]);
    expect(await listed(scopes.ownerA.workspaceId)).toEqual([here.body.result.id]);
    for (const [id, workspaceId] of [[made, other], [here.body.result.id, scopes.ownerA.workspaceId]]) {
      expect((await scopes.send(scopes.withWorkspace(scopes.ownerA, workspaceId), 'DELETE',
        `${CONNECTIONS_PATH}${id}/`)).status).toBe(200);
    }
  });

  await test.step('a Member reads the page with every write control disabled', async () => {
    const member = await (await scopes.openContext(browser, scopes.member)).newPage();
    await member.goto(INTEGRATIONS_ROUTE, { waitUntil: 'domcontentloaded' });
    await expect(member.getByText('Available Platforms', { exact: true })).toBeVisible({ timeout: UI_READY });
    for (const button of await member.getByRole('button', { name: 'Add Integration' }).all()) {
      await expect(button).toBeDisabled();
    }
    const cards = gridCards(member);
    await expect(cards).toHaveText(cardTexts(GRID));
    for (const card of await cards.all()) await expect(card).toBeDisabled();
    // One tooltip wrapper (IntegrationAdminOnly) per platform card and one on the header's Add
    // Integration; the empty state no longer has a button of its own.
    await expect(member.locator('[aria-label="Owner or Admin only"]')).toHaveCount(GRID.length + 1);
    await member.getByText('Datadog', { exact: true }).hover({ force: true });
    await expect(member.getByRole('tooltip')).toHaveText('Owner or Admin only');
    await shot(member, testInfo, '09-member');
    await member.goto(PROFILE_ROUTE, { waitUntil: 'domcontentloaded' });
    await expect(member.locator(`a[href="${PROFILE_ROUTE}"]`).first()).toBeVisible({ timeout: UI_READY });
    await expect(member.locator(`a[href="${INTEGRATIONS_ROUTE}"]`)).toHaveCount(0);
  });

  await test.step('a workspace Admin opens Integrations from the Settings menu and can add one', async () => {
    const wsAdmin = await (await scopes.openContext(browser, await scopes.workspaceAdmin())).newPage();
    await wsAdmin.goto(PROFILE_ROUTE, { waitUntil: 'domcontentloaded' });
    await wsAdmin.locator(`a[href="${INTEGRATIONS_ROUTE}"]`).filter({ hasText: /^Integrations$/ })
      .click({ timeout: UI_READY });
    await expect(wsAdmin).toHaveURL(new RegExp(`${INTEGRATIONS_ROUTE}/?$`), { timeout: UI_READY });
    await expect(wsAdmin.getByText('Available Platforms', { exact: true })).toBeVisible({ timeout: UI_READY });
    for (const button of await wsAdmin.getByRole('button', { name: 'Add Integration' }).all()) {
      await expect(button).toBeEnabled();
    }
    const cards = gridCards(wsAdmin);
    await expect(cards).toHaveText(cardTexts(GRID));
    for (const card of await cards.all()) await expect(card).toBeEnabled();
    await expect(wsAdmin.locator('[aria-label="Owner or Admin only"]')).toHaveCount(0);
    await shot(wsAdmin, testInfo, '13-workspace-admin');
  });
});
