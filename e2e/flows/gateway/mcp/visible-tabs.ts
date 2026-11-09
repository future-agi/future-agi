import type { Page, Request } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../../lib/scope-actors';
import { declaredMcpFeatures, mcpTabLabels, MCP_DISABLED_TAB_LABELS, MCP_DISABLED_NOTICE } from '../../../journeys/gateway/_shared/locators/mcp';

// 824c4ea31a: frontend/src/sections/gateway/mcp/MCPManagementSection.jsx:35-40,66-89.
const MCP_PATH = '/dashboard/gateway/mcp';
// frontend/src/utils/axios.js:2476-2483; observe attempted requests of ANY method.
const DISABLED_READ = /\/agentcc\/gateways\/[^/]+\/mcp-(resources|prompts)\/$/;
const UPDATE_GUARDRAILS = '/agentcc/gateways/default/update-mcp-guardrails/';
// mcp/hooks/useMCPConfigPatch.js sends {mcp: patch}; an owned empty-state fixture.
const UPDATE_CONFIG = '/agentcc/gateways/default/update-config/';
// providers/hooks/useGatewayConfig.js and mcp/hooks/useMCPConfig.js:23-32,81-88.
const CONFIG = '/agentcc/gateways/default/config/';
const STATUS = '/agentcc/gateways/default/mcp-status/';
// mcp/guardrails/notices.js:1-3; storage, NOT runtime enforcement.
const STORED_NOTICE = 'MCP guardrail settings are stored for your organization. They are not active on this gateway.';
// mcp/guardrails/StoredSettingsDialog.jsx:19-26,84,151.
const STORED_DIALOG = 'Stored MCP guardrail settings';
const SAVE_STORED = 'Save stored settings';
// e2e/README.md:556-562: one short navigation/edit chain per step.
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
type Status = { reason?: string; features?: { client_oauth?: boolean; client_installs?: boolean } };
type Stored = { enabled?: boolean; blocked_tools?: string[] };
type Config = { result: { mcp?: { guardrails?: Stored } } };

export function observeDisabledMcpReads(page: Page) {
  const requests: string[] = [];
  const observe = (req: Request) => {
    if (DISABLED_READ.test(new URL(req.url()).pathname)) requests.push(req.method());
  };
  page.on('request', observe);
  return { requests, stop: () => page.off('request', observe) };
}

async function assertTabs(page: Page) {
  const tabs = page.getByRole('tablist').filter({ has: page.getByRole('tab', { name: 'Overview', exact: true }) });
  await expect(tabs.getByRole('tab')).toHaveText(mcpTabLabels(declaredMcpFeatures()), { timeout: UI_READY });
  for (const label of MCP_DISABLED_TAB_LABELS) {
    const tab = tabs.getByRole('tab', { name: label, exact: true });
    await expect(tab).toBeVisible({ timeout: UI_READY });
    await expect(tab).toBeDisabled({ timeout: UI_READY });
    await expect(tab).toHaveAccessibleDescription(MCP_DISABLED_NOTICE, { timeout: UI_READY });
  }
  await expect(tabs.getByRole('tab', { name: 'Guardrails', exact: true })).toBeEnabled();
}

export async function assertDisabledMcpNavigation(page: Page, owner: ScopeActor, requests: string[]) {
  const status = (await owner.api.get<{ result: Status }>(STATUS)).result;
  expect(status.reason, 'MCP is served; deployment-off is not a successful tab check').toBeUndefined();
  expect({ client_oauth: status.features?.client_oauth,
    client_installs: status.features?.client_installs }).toEqual(declaredMcpFeatures());
  // The observer starts only after a fresh Overview is selected, with the Servers
  // drawer unmounted. It covers both disabled deep-link forms, reloads and the
  // following stored-settings edits, not the server setup's legitimate list reads.
  // No route fulfilment, abort or mock can hide a fetch within that window.
  for (const [slug, label] of [['resources', 'Resources'], ['prompts', 'Prompts']]) {
    for (const path of [`${MCP_PATH}?tab=${slug}&from=gw23`, `${MCP_PATH}/${slug}?from=gw23`]) {
      await test.step(`UI: disabled ${label} stays visible at ${path}`, async () => {
        await page.goto(path, { waitUntil: 'domcontentloaded' });
        await assertTabs(page);
        await expect(page.getByRole('tab', { name: label, exact: true })).toHaveAttribute('aria-selected', 'true');
        await expect(page).toHaveURL((url) => url.searchParams.get('from') === 'gw23' &&
          (url.pathname === `${MCP_PATH}/${slug}` || url.searchParams.get('tab') === slug));
        expect(requests).toEqual([]);
      }, { timeout: STEP_BUDGET });
    }
    await test.step(`UI: ${label} reload sends no disabled-list request`, async () => {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await assertTabs(page);
      await expect(page.getByRole('tab', { name: label, exact: true })).toHaveAttribute('aria-selected', 'true');
      // A real UI transition provides a completion boundary after the disabled page.
      await page.getByRole('tab', { name: 'Guardrails', exact: true }).click();
      await expect(page.getByText(STORED_NOTICE, { exact: true })).toBeVisible({ timeout: UI_READY });
      expect(requests).toEqual([]);
    }, { timeout: STEP_BUDGET });
  }
}

export async function assertStoredMcpGuardrailEdits(page: Page, owner: ScopeActor, marker: string) {
  // GuardrailsTab.jsx:53,102-122: missing settings are reachable and can be added.
  await test.step('UI: Guardrails is reachable without stored settings', async () => {
    const before = (await owner.api.get<Config>(CONFIG)).result.mcp?.guardrails;
    expect(before).toBeUndefined();
    await page.goto(`${MCP_PATH}?tab=guardrails&from=gw23`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('tab', { name: 'Guardrails', exact: true })).toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
    await expect(page.getByText(STORED_NOTICE, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText('No MCP guardrails are stored.', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Add stored settings', exact: true })).toBeEnabled();
  }, { timeout: STEP_BUDGET });
  await test.step('UI + API: Guardrails also stays reachable with an empty stored object', async () => {
    // Use update-config's merge patch, not update-mcp-guardrails: the latter
    // rejects {} (futureagi/agentcc/views/gateway.py:1946-1948).
    await owner.api.post(UPDATE_CONFIG, { mcp: { guardrails: {} } });
    expect((await owner.api.get<Config>(CONFIG)).result.mcp?.guardrails).toEqual({});
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('tab', { name: 'Guardrails', exact: true })).toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
    await expect(page.getByRole('tab', { name: 'Guardrails', exact: true })).toBeEnabled();
    await expect(page.getByText('No MCP guardrails are stored.', { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('button', { name: 'Add stored settings', exact: true })).toBeEnabled();
  }, { timeout: STEP_BUDGET });
  await test.step('UI + API: add disabled stored settings, not an enforcement claim', async () => {
    await page.getByRole('button', { name: 'Add stored settings', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: STORED_DIALOG, exact: true });
    // Touch only the stored flag and a uniquely minted non-tool marker.
    await dialog.getByLabel('Enabled in stored settings', { exact: true }).check();
    await dialog.getByLabel('Enabled in stored settings', { exact: true }).uncheck();
    await dialog.getByRole('textbox', { name: 'Blocked tools', exact: true }).fill(marker);
    const saved = page.waitForResponse((r) => r.request().method() === 'POST' &&
      new URL(r.url()).pathname === UPDATE_GUARDRAILS, { timeout: UI_READY });
    await dialog.getByRole('button', { name: SAVE_STORED, exact: true }).click();
    const response = await saved;
    expect(response.status()).toBe(200);
    // useMCPConfig.js:81-88 posts {config}, not the org config patch envelope.
    expect(response.request().postDataJSON()).toEqual({ config: { enabled: false, blocked_tools: [marker] } });
    await expect(dialog).toBeHidden({ timeout: UI_READY });
    expect((await owner.api.get<Config>(CONFIG)).result.mcp?.guardrails)
      .toEqual({ enabled: false, blocked_tools: [marker] });
  }, { timeout: STEP_BUDGET });
  await test.step('UI + API: disabled stored settings survive a path link and remain editable', async () => {
    await page.goto(`${MCP_PATH}/guardrails?from=gw23`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('tab', { name: 'Guardrails', exact: true })).toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
    await expect(page).toHaveURL((url) => url.pathname === `${MCP_PATH}/guardrails` && url.searchParams.get('from') === 'gw23');
    await expect(page.getByText(STORED_NOTICE, { exact: true })).toBeVisible({ timeout: UI_READY });
    await page.getByRole('button', { name: 'Edit stored settings', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: STORED_DIALOG, exact: true });
    await expect(dialog.getByLabel('Enabled in stored settings', { exact: true })).not.toBeChecked();
    await expect(dialog.getByRole('textbox', { name: 'Blocked tools', exact: true })).toHaveValue(marker);
    await dialog.getByRole('textbox', { name: 'Blocked tools', exact: true }).fill(`${marker}-edited`);
    const saved = page.waitForResponse((r) => r.request().method() === 'POST' &&
      new URL(r.url()).pathname === UPDATE_GUARDRAILS, { timeout: UI_READY });
    await dialog.getByRole('button', { name: SAVE_STORED, exact: true }).click();
    const response = await saved;
    expect(response.status()).toBe(200);
    expect(response.request().postDataJSON()).toEqual({ config: { enabled: false, blocked_tools: [`${marker}-edited`] } });
    await expect(dialog).toBeHidden({ timeout: UI_READY });
    expect((await owner.api.get<Config>(CONFIG)).result.mcp?.guardrails)
      .toEqual({ enabled: false, blocked_tools: [`${marker}-edited`] });
  }, { timeout: STEP_BUDGET });
}
