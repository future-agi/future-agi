import type { Page } from '@playwright/test';
import { subfeatureDeclaration } from '../../../../stack/mcp-subfeature-selection.mjs';

// Expected capabilities come from the reviewed fixture declaration, not status.
// stack/mcp-subfeature-selection.mjs:11-39 pins mcp to both false, mcp-signin
// to OAuth only, W12 to both true; without a selection both remain false.
// futureagi/agentcc/mcp_features.py:33-39 reports those deployment flags.
export function declaredMcpFeatures() {
  const { gates } = subfeatureDeclaration();
  return {
    client_oauth: gates.AGENTCC_MCP_CLIENT_OAUTH_ENABLED === 'true',
    client_installs: gates.AGENTCC_MCP_CLIENT_INSTALLS_ENABLED === 'true',
  };
}

// 824c4ea31a: frontend/src/sections/gateway/mcp/tabs.js:22-71 and
// shell/page-status.js:37-46. Base list when client capabilities are absent.
// Guardrails never depends on stored config; Resources/Prompts stay disabled.
// Only applies AFTER a successful MCP-served status read; deployment-off has
// no bar (MCPManagementSection.jsx:155-175), not an empty successful tab set.
export const MCP_ON_TAB_LABELS = [
  'Overview', 'Servers', 'Virtual servers', 'Tool calls', 'Credentials', 'Guardrails', 'Try a tool',
  'Resources', 'Prompts',
] as const;
// tabs.js:33-50 and MCPManagementSection.jsx:224-244 (no Content mount).
export const MCP_DISABLED_TAB_LABELS = ['Resources', 'Prompts'] as const;
export const MCP_DISABLED_NOTICE = 'The Resources and Prompts tabs are disabled.';
// tabs.js:28,41,63-71: only explicit status capabilities insert these tabs.
export function mcpTabLabels(features?: { client_oauth?: boolean; client_installs?: boolean }): string[] {
  const labels: string[] = [...MCP_ON_TAB_LABELS];
  if (features?.client_oauth === true) labels.splice(5, 0, 'MCP clients');
  if (features?.client_installs === true) labels.push('Connect');
  return labels;
}

export const mcpLocators = {
  // MCPManagementSection.jsx:206-232: CustomTabs > Tab, no named tablist.
  // A visible locator is not a promise of interactivity: see disabled labels.
  tab: (page: Page, label: (typeof MCP_ON_TAB_LABELS)[number]) => page.getByRole('tab', { name: label, exact: true }),
  // Approvals remains hidden (page-status.js:43); preserve that absence check.
  absentApprovalsTab: (page: Page) => page.getByRole('tab', { name: 'Approvals', exact: true }),
  // Valid only after a successful status read has client_installs !== true
  // (tabs.js:69). Never infer absence on a capability-enabled deployment.
  absentConnectTab: (page: Page) => page.getByRole('tab', { name: 'Connect', exact: true }),
};

// OWED F18-P03 / F11: these absence locators do not qualify a positive
// approval or client-install journey. Do not borrow an org key, execute a
// custom-protocol handler, or treat absence as W11/W12 pass. Positive journey
// actions still need their own source pins and accepted runner interception.
