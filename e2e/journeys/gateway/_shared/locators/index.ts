/** F18 gateway-only source locator library, not a Playwright fixture or walk.
 *
 * Compose imports from this NEW directory; existing _shared/selectors.ts and
 * packet specs are intentionally unchanged. All functions return lazy locators:
 * no navigation, HTTP, credential reads, fixtures, processes or runtime hooks.
 * Only use with the accepted runner's authenticated loopback Page/Locator.
 *
 * Consumers must attach non-secret fixture IDs with attachSeededIds(testInfo,
 * packet, ids), retain tenant/role/profile/width bindings, assert uniqueness and
 * exact complete sets (including extras), and use GatewayGridReader for display
 * ordering plus pagination/virtualization checks. Locator presence is never
 * proof of an authoritative balance, config revision, approval or payment.
 * Poll the actual dependency with existing named POLL budgets; these factories
 * add no sleeps, retries, fallback IDs, hidden=true or widened deadlines.
 *
 * F18-P01..P05 and consent/payment correctness remain owed in external
 * fitems/F18/AUTHOR.json. Missing product functionality cannot be fixed by a
 * testid. Native media, W04 cap activation/revision and W11/W12 positives are
 * NOT implemented here. Console and FA-only paths are owned elsewhere.
 * No full row is converted to WALKED_PASS by this authoring-only delivery.
 */
export { LOCATOR_SOURCE_REF, type LocatorScope } from './scope';
export { walletLocators, cardLocators, type LedgerColumn } from './wallet';
export { budgetLocators, orgConfigLocators, type BudgetColumn } from './budgets';
export { requestLogLocators, toolCallLocators, type RequestColumn, type BillingColumn, type ToolCallColumn, type ToolCallFilter } from './logs';
export { mcpLocators, MCP_ON_TAB_LABELS } from './mcp';
export { modelDetailLocators } from './models';
export { guardrailLocators, DATA_LEAKAGE_CHECK, type RuleColumn } from './guardrails';
