import type { Locator, Page } from '@playwright/test';
import { requireId } from '../identifiers';
import { gatewaySelectors } from '../selectors';
import { definitionValue, fixtureText, recordCell, recordRow, type LocatorScope } from './scope';

// All citations relative to frontend/src/sections/gateway/ at LOCATOR_SOURCE_REF.
// Grid identity is the persisted record's id. A request_id/tool_call_id is a
// separate wire identity and MUST NOT be passed as that row id without a join.
export type RequestColumn = 'startedAt' | 'model' | 'provider' | 'application' | 'service' | 'statusCode' | 'latencyMs' | 'cost' | 'totalTokens' | 'sessionId';
// logs/columns/gatewayColumns.js:GATEWAY_COLUMNS, opt-in via Columns picker.
export type BillingColumn = 'billingMode' | 'credentialType' | 'costUpstream' | 'costMarkup' | 'attemptCount' | 'agentId' | 'agentStep' | 'agentRunId';
const BILLING_COLUMN_LABELS = {
  billingMode: 'Billing mode', credentialType: 'Credential type', costUpstream: 'Upstream cost',
  costMarkup: 'Markup', attemptCount: 'Attempts', agentId: 'Agent', agentStep: 'Step', agentRunId: 'Run',
} as const;
const requestDrawer = (page: Page, requestId: string) => page.getByRole('dialog', { name: requireId(requestId), exact: true });

export const requestLogLocators = {
  // logs/RequestExplorerSection.jsx:598-601 -> GatewayFilterBar aria-label.
  search: (scope: LocatorScope) => scope.getByRole('textbox', { name: 'Search model, provider, request id, error', exact: true }),
  // RequestTable.jsx:364 getRowId=String(data.id), not request_id.
  row: (scope: LocatorScope, logId: string) => recordRow(scope, logId),
  // logs/columns/columnModel.js:33-55 -> builtin:<key>;
  // RequestTable.jsx:226-229 copies col.id as colId.
  cell: (scope: LocatorScope, logId: string, column: RequestColumn) => recordCell(scope, logId, `builtin:${column}`),
  billingCell: (scope: LocatorScope, logId: string, column: BillingColumn) => recordCell(scope, logId, `gateway:${column}`),
  // RequestExplorerSection.jsx:623-631; ColumnsPickerPopover.jsx:70-79,253-266.
  // Use the actual UI to expose opt-in columns. A missing column stays missing.
  openColumns: (scope: LocatorScope) => scope.getByRole('button', { name: 'Columns', exact: true }),
  billingColumnChoice: (page: Page, column: BillingColumn) => page.getByRole('dialog', { name: 'Choose columns', exact: true })
    .getByRole('checkbox', { name: BILLING_COLUMN_LABELS[column], exact: true }),
  // RequestDetailDrawer.jsx:520-540 title=request_id (id fallback is NOT
  // accepted here); GatewayDetailDrawer.jsx:29-32 names role=dialog by h2.
  drawer: requestDrawer,
  summary: (page: Page, requestId: string) => requestDrawer(page, requestId).getByRole('list', { name: 'Summary', exact: true }),
  // RequestDetailDrawer.jsx:TAB_LABELS + aria-label=Request details:632-646.
  // Only safe overview/guardrail paths exported; no raw body/header dumps.
  overview: (page: Page, requestId: string) => requestDrawer(page, requestId)
    .getByRole('tablist', { name: 'Request details', exact: true })
    .getByRole('tab', { name: 'Overview', exact: true }),
  guardrails: (page: Page, requestId: string) => requestDrawer(page, requestId)
    .getByRole('tablist', { name: 'Request details', exact: true })
    .getByRole('tab', { name: 'Guardrails', exact: true }),
  // attempts/AttemptsTimeline.jsx:134 li[data-outcome]. Preserve complete
  // ordered attempt inventory; never filter to successful outcomes first.
  attempts: (page: Page, requestId: string) => gatewaySelectors.attemptRows(requestDrawer(page, requestId)),
  // RunLink.jsx:26-35 + shell/page-status.js:21-28 (runs NOT hidden).
  // Do not use data-testid=run-id: it exists only on the other, hidden branch.
  runLink: (scope: LocatorScope, mintedRunId: string) => scope.getByRole('link', { name: requireId(mintedRunId), exact: true }),
  close: (page: Page, requestId: string) => requestDrawer(page, requestId).getByRole('button', { name: 'Close', exact: true }),
};

export type ToolCallColumn = 'time' | 'tool' | 'server' | 'virtual' | 'outcome' | 'latency' | 'key' | 'billing' | 'cost' | 'run';
export type ToolCallFilter = 'Server' | 'Tool' | 'Outcome' | 'Time';

// mcp/calls/ToolCallDrawer.jsx:234 names the drawer by call.tool, which is NOT
// unique. Bind the same drawer to its exact non-secret tool_call_id in the
// definition list (154-165). Required even if there is only one visible drawer.
function toolCallDrawer(page: Page, toolName: string, toolCallId: string): Locator {
  return page.getByRole('dialog', { name: fixtureText(toolName), exact: true }).filter({
    has: definitionValue(page, 'Tool call').and(page.getByText(requireId(toolCallId), { exact: true })),
  });
}

export const toolCallLocators = {
  // mcp/calls/ToolCallsTab.jsx:119-200,241-247; id is the persisted PG row ID.
  row: (scope: LocatorScope, persistedCallId: string) => recordRow(scope, persistedCallId),
  cell: (scope: LocatorScope, persistedCallId: string, column: ToolCallColumn) => recordCell(scope, persistedCallId, column),
  // ToolCallsTab.jsx:294-320 -> mcp/components/FilterSelect.jsx TextField.label.
  // MUI select's role name can include its selected value; use its actual label.
  filter: (scope: LocatorScope, label: ToolCallFilter) => scope.getByLabel(label, { exact: true }),
  refresh: (scope: LocatorScope) => scope.getByRole('button', { name: 'Refresh', exact: true }),
  loadMore: (scope: LocatorScope) => scope.getByRole('button', { name: 'Load more', exact: true }),
  drawer: toolCallDrawer,
  // Call detail safe metadata only. mcp/components/KeyValueList.jsx renders dt/dd.
  requestId: (page: Page, toolName: string, toolCallId: string) => definitionValue(toolCallDrawer(page, toolName, toolCallId), 'Request'),
  agentRun: (page: Page, toolName: string, toolCallId: string) => definitionValue(toolCallDrawer(page, toolName, toolCallId), 'Agent run'),
  server: (page: Page, toolName: string, toolCallId: string) => definitionValue(toolCallDrawer(page, toolName, toolCallId), 'Server'),
  close: (page: Page, toolName: string, toolCallId: string) => toolCallDrawer(page, toolName, toolCallId)
    .getByRole('button', { name: 'Close', exact: true }),
};

// J15/W14.A03 remains partial: this ToolCallsTab has server/tool/outcome/time
// filters, not agent filtering, a trace pivot or export. Those missing product
// paths are F18-P05, not synthesized selectors. Pagination/virtualization require
// the existing GatewayGridReader plus complete API inventory; these locators
// never equate a rendered subset, a page count, or one drawer with the exact set.
