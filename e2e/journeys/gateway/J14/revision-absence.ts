import { expect, test, type Page, type TestInfo, type Request } from '@playwright/test';
import { declaredMcpFeatures, mcpTabLabels, MCP_DISABLED_TAB_LABELS, MCP_DISABLED_NOTICE } from '../_shared/locators/mcp';

// 824c4ea31a: frontend/src/routes/paths.js:paths.dashboard.gateway.mcp.
const MCP_PATH = '/dashboard/gateway/mcp';
// frontend/src/utils/axios.js:2478-2483; observe attempts, not just successes.
const DISABLED_READ = /\/agentcc\/gateways\/[^/]+\/mcp-(resources|prompts)\/$/;
// MCPManagementSection.jsx:58-65: only explicit status features gate extra tabs.
const STATUS_PATH = '/agentcc/gateways/default/mcp-status/';
type McpStatus = { reason?: string; features?: { client_oauth?: boolean; client_installs?: boolean } };
// One UI action's first-paint budget: e2e/README.md, Writing a flow.
const MCP_UI_READY = { timeout: 60_000 };

/** UI-only portion of W09.2, not an executable journey or complete row.
 * Needs an already authenticated, admitted local Stage-A page with MCP served;
 * does not provision legacy org keys or add a replacement identity fixture.
 * Missing backend OFF/config witness, native sign-in/search restoration and
 * reviewed visible-control/switch mutant are separate owed items. In particular
 * this cannot make W09.1 pass or convert an OFF-only profile into a positive.
 * Future caller attaches its minted tenant/user IDs via attachSeededIds and
 * gives its test >= two first-paint budgets plus navigation time.
 */
export async function assertRevisionUiAbsent(page: Page, info: TestInfo): Promise<void> {
  const current = new URL(page.url());
  if (!['http:', 'https:'].includes(current.protocol) ||
      !['localhost', '127.0.0.1', '[::1]'].includes(current.hostname) ||
      current.username || current.password) {
    throw new Error('J14 UI subassertion requires an admitted loopback app page');
  }
  info.annotations.push({ type: 'journey-subassertion', description: JSON.stringify({
    schema: 'gateway-journey-subassertion/v1', packet: 'J14', row: 'W09.2',
    scope: 'SERVED_UI_ABSENCE_ONLY', satisfiesPacketRow: false,
  }) });

  const forbiddenReads: string[] = [];
  const observe = (req: Request) => {
    if (DISABLED_READ.test(new URL(req.url()).pathname)) forbiddenReads.push(req.method());
  };
  page.on('request', observe);
  const open = async (path: string) => {
    const status = page.waitForResponse((r) => r.request().method() === 'GET' &&
      new URL(r.url()).pathname === STATUS_PATH, MCP_UI_READY);
    await page.goto(new URL(path, current.origin).href, { waitUntil: 'domcontentloaded' });
    const response = await status;
    expect(response.status()).toBe(200);
    const body = await response.json() as { result: McpStatus };
    expect(body.result.reason, 'a real MCP-served status, not an off/error page').toBeUndefined();
    expect({ client_oauth: body.result.features?.client_oauth,
      client_installs: body.result.features?.client_installs }).toEqual(declaredMcpFeatures());
  };
  const verifyTabs = async () => {
    // MCPManagementSection.jsx -> GatewayPageFrame.jsx component="h1".
    await expect(page.getByRole('heading', { name: 'MCP tools', exact: true }))
      .toBeVisible(MCP_UI_READY);
    // CustomTabs wraps MUI Tabs; MCPManagementSection's label mapping supplies
    // these roles. Scope via the unique Overview tab, not global tab text.
    const tablist = page.getByRole('tablist').filter({
      has: page.getByRole('tab', { name: 'Overview', exact: true }),
    });
    await expect(tablist).toHaveCount(1, MCP_UI_READY);
    await expect(tablist.getByRole('tab')).toHaveText(mcpTabLabels(declaredMcpFeatures()), MCP_UI_READY);
    for (const label of MCP_DISABLED_TAB_LABELS) {
      const tab = tablist.getByRole('tab', { name: label, exact: true });
      await expect(tab).toBeVisible(MCP_UI_READY);
      await expect(tab).toBeDisabled(MCP_UI_READY);
      await expect(tab).toHaveAccessibleDescription(MCP_DISABLED_NOTICE, MCP_UI_READY);
    }
    await expect(tablist.getByRole('tab', { name: 'Guardrails', exact: true })).toBeEnabled(MCP_UI_READY);
    await expect(tablist.getByRole('tab', { name: 'Overview', exact: true }))
      .toHaveAttribute('aria-selected', 'true', MCP_UI_READY);
    await expect(page.getByRole('tab', { name: /^(Trust|Tool revisions)$/ })).toHaveCount(0, MCP_UI_READY);
    expect(forbiddenReads).toEqual([]);
  };

  try {
    await test.step('W09.2/UI: exact served MCP tabs exclude revision trust', async () => {
      await open(MCP_PATH);
      await verifyTabs();
    });
    await test.step('W09.2/UI: hidden trust deep link returns to the supported overview', async () => {
      // MCPManagementSection.jsx:73-89: only a genuinely hidden tab redirects.
      await open(`${MCP_PATH}?tab=trust`);
      await expect(page).toHaveURL(new URL(MCP_PATH, current.origin).href, MCP_UI_READY);
      await verifyTabs();
    });
  } finally {
    page.off('request', observe);
  }
  // Deliberately no journey-row "passed" attachment: backend/controls still owed.
}
