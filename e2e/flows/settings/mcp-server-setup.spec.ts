import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

const UI_READY = 60_000;
// futureagi/mcp_server/views/config.py MCPConfigView.get: { status, result }
// with result.mcp_url = mcp_server.public_url.mcp_public_url(), "<base>/mcp".
const CONFIG_PATH = '/mcp/config/';
// frontend/src/sections/settings/mcp-server/MCPSetupPage.jsx View Docs href,
// `${import.meta.env.BASE_URL}docs/mcp-server.html`; BASE_URL is "/" in the image.
const GUIDE_PATH = '/docs/mcp-server.html';
// frontend/public/docs/mcp-server.html: its title and its sections, which are
// exactly the statements this flow proves.
const GUIDE_TITLE = 'MCP server setup';
const GUIDE_SECTIONS = [
  'The MCP server URL',
  'Credentials are required',
  'The metadata names the same URL',
];
// futureagi/mcp_server/mcp_app.py _mcp_streamable_with_auth: every 401 carries
// WWW-Authenticate: Bearer resource_metadata="<base><METADATA_SUFFIX>".
const METADATA_SUFFIX = '/.well-known/oauth-protected-resource/mcp';
// The JSON-RPC request an MCP client opens a Streamable HTTP session with.
const INITIALIZE = {
  jsonrpc: '2.0',
  id: 1,
  method: 'initialize',
  params: {
    protocolVersion: '2025-06-18',
    capabilities: {},
    clientInfo: { name: 'e2e-set-e2e-002', version: '1.0.0' },
  },
};

interface ConfigEnvelope {
  status: boolean;
  result: { id: string; mcp_url: string | null };
}

test("SET-E2E-006: a user finds this deployment's MCP server URL and its setup guide", {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SET-E2E-006', area: 'settings',
    userGoal: "A user reads this deployment's MCP server URL and its setup guide before connecting a client",
    steps: ['open Settings MCP Server', 'read the MCP server URL under Connect Your IDE',
            'follow View Docs', 'read the MCP server setup guide'],
    backendChecks: [
      'GET /mcp/config/ mcp_url equals the MCP server URL the page shows',
      'GET /docs/mcp-server.html is a 200 text/html page with the MCP server setup title, loading nothing from another origin',
      'an uncredentialed request to the shown URL is 401 with WWW-Authenticate resource_metadata at <base>/.well-known/oauth-protected-resource/mcp',
      'that protected-resource document names the shown URL as its resource',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // Navigation, the new tab and five browser waits at UI_READY each, plus
  // three short API calls: more than the 120s default on a loaded stack.
  test.setTimeout(240_000);

  const config = await actor.api.get<ConfigEnvelope>(CONFIG_PATH);
  expect(config.status).toBe(true);
  const mcpUrl = config.result.mcp_url;
  expect(mcpUrl, 'the e2e backend sets BASE_URL, so it reports an MCP URL').not.toBeNull();
  const shownUrl = mcpUrl as string;
  expect(shownUrl).toMatch(/^https?:\/\/[^/]+(\/.*)?\/mcp$/);
  const expectedMetadata = `${shownUrl.slice(0, -'/mcp'.length)}${METADATA_SUFFIX}`;
  const appOrigin = new URL(E2E.appUrl).origin;
  await testInfo.attach('mcp-config', {
    body: JSON.stringify({ connectionId: config.result.id, organizationId: actor.organizationId,
      workspaceId: actor.workspaceId, mcpUrl: shownUrl, expectedMetadata, guide: `${appOrigin}${GUIDE_PATH}` }),
    contentType: 'application/json',
  });

  await test.step('UI: Settings > MCP Server shows the URL GET /mcp/config/ reports', async () => {
    await page.goto('/dashboard/settings/mcp-server', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Connect Your IDE', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('textbox', { name: 'MCP server URL' }))
      .toHaveValue(shownUrl, { timeout: UI_READY });
  });

  await test.step('UI: View Docs opens the bundled MCP server setup guide', async () => {
    const docs = page.getByRole('link', { name: 'View Docs' });
    await expect(docs).toHaveAttribute('href', GUIDE_PATH, { timeout: UI_READY });
    const opened = page.context().waitForEvent('page', { timeout: UI_READY });
    await docs.click();
    const guide = await opened;
    await expect(guide).toHaveURL(`${appOrigin}${GUIDE_PATH}`, { timeout: UI_READY });
    await expect(guide.getByRole('heading', { level: 1 })).toHaveText([GUIDE_TITLE], { timeout: UI_READY });
    await expect(guide.getByRole('heading', { level: 2 })).toHaveText(GUIDE_SECTIONS, { timeout: UI_READY });
    // Every resource the guide loaded and every link it offers stays on the app's origin.
    const offOrigin = await guide.evaluate((origin) => [
      ...performance.getEntriesByType('resource').map((entry) => entry.name),
      ...Array.from(document.querySelectorAll('a[href]'), (a) => (a as HTMLAnchorElement).href),
    ].filter((url) => new URL(url).origin !== origin), appOrigin);
    expect(offOrigin).toEqual([]);
    await guide.close();
  });

  const anonymous = await request.newContext();
  try {
    await test.step('API: the guide is a page the app serves itself', async () => {
      const res = await anonymous.get(`${appOrigin}${GUIDE_PATH}`, { maxRedirects: 0 });
      expect(res.status()).toBe(200);
      expect(res.headers()['content-type']).toMatch(/^text\/html/);
      expect(await res.text()).toContain(`<title>${GUIDE_TITLE}</title>`);
    });

    await test.step('API: an uncredentialed request to the shown URL is 401 naming its metadata', async () => {
      const res = await anonymous.post(shownUrl, {
        data: INITIALIZE,
        headers: { Accept: 'application/json, text/event-stream' },
        maxRedirects: 0,
      });
      expect(res.status()).toBe(401);
      const challenge = res.headers()['www-authenticate'] ?? '';
      expect(challenge).toMatch(/^Bearer /);
      const advertised = /resource_metadata="([^"]+)"/.exec(challenge)?.[1];
      expect(advertised).toBe(expectedMetadata);

      const metadata = await anonymous.get(advertised as string, { maxRedirects: 0 });
      expect(metadata.status()).toBe(200);
      const protectedResource = await metadata.json() as { resource: string };
      expect(protectedResource.resource).toBe(shownUrl);
    });
  } finally {
    await anonymous.dispose();
  }
});
