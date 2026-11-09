import { test, expect } from '../../../lib/fixtures';
import { flowAnnotation } from '../../../lib/flow-meta';
import { E2E } from '../../../lib/env';
import { profileTag } from '../../../lib/profile';
import { inspectRealCatalog, validateRealCatalog } from '../../../lib/real-catalog';

// The Integrations page over the REAL connector catalogue, browse only
// (MCP-APPS-PLAN stage 2b, the open P2 of GATE-STAGE2: GW-E2E-022 browses H-MCP's
// one-app fixture). Runs on the mcp-catalog gateway profile only (tag
// @gw-profile-mcp-catalog; E2E_STACK=standalone E2E_GATEWAY_PROFILE=mcp-catalog,
// lib/profile.ts): the gateway serves MCP with no upstream of its own, and Django
// serves the catalogue this build bundles (futureagi/agentcc/connector_catalog/
// catalog.json), which lib/real-catalog.ts attests before any read.
//
// Nothing is added, so no provider is ever called. The browser is held to the
// local stack: every request to another host is aborted before it leaves and
// recorded, and the flow fails if any was for a FutureAGI host, or for a host the
// catalogue could make the page fetch from: a provider's server or an app's icon.
// (The console's own outside requests, such as its web fonts, are aborted too,
// and are not the catalogue's.)
//
// Pinned off the code it drives:
// - CATALOG_PATH and PAGE_PATH, as GW-E2E-022 (agentcc/urls_connector_catalog.py;
//   paths.dashboard.gateway.integrations).
// - listable(): what this build lists, the rule of connector_catalog.plan_for and
//   frontend/src/sections/gateway/connectors/catalog-model.js connectableApps: no
//   credential, one shared key sent in a header, or a sign-in whose card says this
//   deployment serves it (connect.served, connect-flow.js).
// - matches(): the page's search rule (catalog-model.js matchesSearch).
// First written 2026-10-25 in lane CN25.
const CATALOG_PATH = '/agentcc/connectors/catalog/';
const PAGE_PATH = '/dashboard/gateway/integrations';
const MCP_STATUS = '/agentcc/gateways/default/mcp-status/';
const PAGE_SIZE = 200;
const UI_READY = 60_000;
const CATEGORIES = ['Productivity', 'Developer', 'Communication', 'Data', 'Research'];
// Nothing on the page may promise what does not work on this build (AM-C23).
const FORBIDDEN_TEXT = /coming soon|after (per-user )?sign-in|sign in with|connect with oauth/i;
// Fields that would carry a secret; the catalog API never sends one.
const SECRET_FIELDS = new Set(['value', 'secret', 'client_secret', 'token', 'api_key', 'encrypted_secret']);
const SIGN_IN_KINDS = new Set(['oauth', 'oauth2', 'mcp_oauth']);

interface Credential { mode: string; kind?: string | null; in?: string; label?: string }
interface Provider { id: string; credential: Credential; connect?: { served?: unknown } }
interface Server { catalog_ref: string | null; connected: boolean }
interface CatalogApp {
  id: string; name: string; description: string; vendor?: { name?: string }; tags?: string[];
  providers: Provider[]; servers: Server[];
}
interface CatalogPage {
  results: CatalogApp[]; next: string | null; total_pages: number;
  catalog: { sha256: string; version: string; source_commit: string };
}

function listable(provider: Provider): boolean {
  const { mode, kind, in: placement } = provider.credential;
  if (mode === 'none') return true;
  if (kind && SIGN_IN_KINDS.has(kind)) return provider.connect?.served === true;
  return mode === 'shared' && (kind === 'bearer' || kind === 'api_key') && placement === 'header';
}

function matches(app: CatalogApp, query: string): boolean {
  const haystack = [app.id, app.name, app.description, app.vendor?.name, ...(app.tags ?? [])].join(' ').toLowerCase();
  return haystack.includes(query.trim().toLowerCase());
}

function secretFieldsIn(value: unknown, path = '$'): string[] {
  if (Array.isArray(value)) return value.flatMap((item, i) => secretFieldsIn(item, `${path}[${i}]`));
  if (value && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, item]) => [
      ...(SECRET_FIELDS.has(key) ? [`${path}.${key}`] : []),
      ...secretFieldsIn(item, `${path}.${key}`),
    ]);
  }
  return [];
}

const isFutureAgiHost = (host: string) => host === 'futureagi.com' || host.endsWith('.futureagi.com');
const hostOf = (url: string) => { try { return new URL(url).hostname; } catch { return ''; } };

test('GW-E2E-033: an admin browses the real connector catalogue: every app this build can add is listed and found, and no other', {
  tag: ['@flow', profileTag('mcp-catalog')],
  annotation: flowAnnotation({
    id: 'GW-E2E-033',
    area: 'gateway',
    userGoal: 'An organization admin browses the connector catalogue this build ships and finds every app it can add, and none it cannot',
    steps: [
      'open Gateway Integrations on the mcp-catalog gateway profile (the real catalogue, MCP served)',
      'read the page header, the five categories, the search box and the Custom MCP server action',
      'search for each listed app by name and find it',
      'search for each app the catalogue ships but this build cannot add, and find no row for it',
      'open the first app and read its access control, then close it',
    ],
    backendChecks: [
      'the console MCP status names no reason: the gateway serves MCP',
      'the app container mounts nothing over the catalogue it bundles, and the catalog API names exactly that catalogue (sha256, version, source commit)',
      'every provider the catalog API lists can be added on this build: no key, one shared key sent in a header, or a sign-in its card says is served',
      'no catalog API response carries a secret field',
    ],
    browserChecks: [
      'the grid holds exactly as many apps as the API lists, finds each of them, and the Connected count is the API\'s',
      'a search for an app the API leaves out shows only listed apps that match it, or the one empty state',
      'no text offers a sign-in or coming-soon app',
      'the browser sends no request outside the local stack: none to a FutureAGI host, a provider\'s server or an app\'s icon',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  test.setTimeout(180_000);

  await test.step('API: the console\'s MCP status says the gateway serves MCP', async () => {
    const status = await actor.api.get<{ result: { enabled: boolean; reason?: string } }>(MCP_STATUS);
    await testInfo.attach('mcp-status', { body: JSON.stringify(status.result), contentType: 'application/json' });
    expect(status.result.reason, 'the mcp-catalog profile serves MCP, so a fresh org\'s status names no reason')
      .toBeUndefined();
  });

  const bundle = inspectRealCatalog();
  await testInfo.attach('real-catalog', { contentType: 'application/json',
    body: JSON.stringify({ version: bundle.version, sha256: bundle.sha256, apps: bundle.apps.length }) });

  const apps: CatalogApp[] = [];
  await test.step('API: the catalog names the bundled catalogue and lists only apps this build can add', async () => {
    for (let pageNo = 1; pageNo <= 50; pageNo += 1) {
      const body = await actor.api.get<CatalogPage>(CATALOG_PATH, { limit: PAGE_SIZE, page: pageNo });
      validateRealCatalog(body.catalog, bundle);
      expect(secretFieldsIn(body), 'catalog API response carries a secret field').toEqual([]);
      apps.push(...body.results);
      if (!body.next || pageNo >= body.total_pages) break;
    }
    for (const app of apps) {
      for (const provider of app.providers) {
        expect(listable(provider), `${app.id}/${provider.id} is listed but cannot be added on this build`).toBe(true);
      }
    }
  });

  const listed = apps.filter((app) => app.providers.length > 0 || app.servers.length > 0);
  const listedIds = new Set(listed.map((app) => app.id));
  const hidden = (bundle.apps as { id: string; name: string }[]).filter((app) => !listedIds.has(app.id));
  expect(listed.length, 'the real catalogue lists at least one app this build can add').toBeGreaterThan(0);
  await testInfo.attach('apps', { contentType: 'application/json',
    body: JSON.stringify({ listed: listed.map((app) => app.id), hidden: hidden.map((app) => app.id) }) });
  // One in-page wait per search, plus the first load.
  test.setTimeout(Math.max(300_000, 180_000 + 5_000 * (listed.length + hidden.length)));

  // The browser is held to the local stack (the frontend, the API and the
  // gateway): anything else is aborted before it is sent, and recorded.
  const local = new Set([E2E.appUrl, E2E.apiUrl, E2E.gatewayUrl].map((url) => new URL(url).origin));
  const outside: string[] = [];
  await page.context().route('**/*', (route) => {
    const url = route.request().url();
    if (local.has(new URL(url).origin)) return route.continue();
    outside.push(hostOf(url));
    return route.abort('blockedbyclient');
  });

  const connectedCount = listed.filter((app) =>
    app.servers.some((server) => Boolean(server.catalog_ref) && server.connected)).length;
  const grid = page.getByTestId('integrations-grid');
  const gridRoot = grid.locator('[aria-rowcount]').first();
  const rowOf = (name: string) => grid.getByRole('row').filter({ hasText: name });
  const search = page.getByRole('textbox', { name: 'Search apps' });

  await test.step('UI: the page header, filter bar and the whole grid', async () => {
    await page.goto(PAGE_PATH, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { level: 1, name: 'Integrations' })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('link', { name: 'Custom MCP server' })).toBeVisible({ timeout: UI_READY });
    await expect(search).toBeVisible({ timeout: UI_READY });
    const categories = page.getByRole('group', { name: 'Category' });
    for (const name of CATEGORIES) {
      await expect(categories.getByRole('button', { name, exact: true })).toBeVisible({ timeout: UI_READY });
    }
    await expect(gridRoot).toHaveAttribute('aria-rowcount', String(listed.length + 1), { timeout: UI_READY });
    await expect(page.getByRole('button', { name: `Connected (${connectedCount})` })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByTestId('integrations-page')).not.toContainText(FORBIDDEN_TEXT, { timeout: UI_READY });
  });

  await test.step('UI: search finds every listed app', async () => {
    for (const app of listed) {
      await search.fill(app.name);
      await expect(rowOf(app.name).first()).toBeVisible({ timeout: UI_READY });
    }
  });

  await test.step('UI: a search for an app this build cannot add shows only listed apps that match it', async () => {
    for (const app of hidden) {
      await search.fill(app.name);
      const shown = listed.filter((candidate) => matches(candidate, app.name)).length;
      if (shown === 0) {
        await expect(page.getByText('No apps match')).toBeVisible({ timeout: UI_READY });
        await expect(grid).toHaveCount(0, { timeout: UI_READY });
      } else {
        await expect(gridRoot).toHaveAttribute('aria-rowcount', String(shown + 1), { timeout: UI_READY });
      }
      await expect(page.getByTestId('integrations-page')).not.toContainText(FORBIDDEN_TEXT);
    }
    await search.fill('');
    await expect(gridRoot).toHaveAttribute('aria-rowcount', String(listed.length + 1), { timeout: UI_READY });
  });

  await test.step('UI: the first app\'s drawer shows its access control and promises nothing', async () => {
    const first = listed[0];
    await rowOf(first.name).first().click();
    const drawer = page.getByRole('dialog', { name: first.name });
    await expect(drawer).toBeVisible({ timeout: UI_READY });
    await expect(drawer.getByRole('heading', { name: 'Access control' })).toBeVisible({ timeout: UI_READY });
    await expect(drawer.getByText('Who can connect')).toBeVisible({ timeout: UI_READY });
    await expect(drawer).not.toContainText(FORBIDDEN_TEXT, { timeout: UI_READY });
    await drawer.getByRole('button', { name: 'Close' }).click();
    await expect(drawer).toBeHidden({ timeout: UI_READY });
  });

  await test.step('browser: nothing was sent outside the local stack, and nothing was meant for FutureAGI or the catalogue\'s hosts', async () => {
    const attempted = [...new Set(outside)].sort();
    await testInfo.attach('outside-hosts', { body: JSON.stringify(attempted), contentType: 'application/json' });
    // What the catalogue could make the page fetch: a provider's server (its
    // URL with every variable filled) and an app's icon.
    const catalogueHosts = new Set<string>();
    for (const provider of bundle.providers) {
      const host = hostOf((provider.transport?.url ?? '').replace(/\{[^}]*\}/g, 'x'));
      if (host) catalogueHosts.add(host.toLowerCase());
    }
    for (const app of bundle.apps) {
      for (const src of [app.icon?.light, app.icon?.dark]) {
        const host = hostOf(src ?? '');
        if (host) catalogueHosts.add(host.toLowerCase());
      }
    }
    const isCatalogueHost = (host: string) => catalogueHosts.has(host.toLowerCase());
    expect(attempted.filter(isFutureAgiHost), 'requests meant for a FutureAGI host').toEqual([]);
    expect(attempted.filter(isCatalogueHost), 'requests meant for a provider\'s server or an app\'s icon').toEqual([]);
  });
});
