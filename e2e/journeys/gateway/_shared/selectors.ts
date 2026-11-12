import type { Locator, Page } from '@playwright/test';
import { requireId } from './identifiers';
export { GatewayGridReader, GRID_VIEWPORT } from '../../../lib/gateway-grid';

type Scope = Page | Locator;

// All hooks below already exist in production frontend source at the foundation
// base. No mock/testing/fixture component is a locator authority. Prefer roles
// and exact minted text inside these roots; do not assert presence alone as proof
// of backend state or feature activation.
export const gatewaySelectors = {
  // sections/gateway/components/frame/GatewayGrid.jsx; shared reader accounts
  // for virtualisation and display order. DOM order is NOT row order.
  grid: (scope: Scope) => scope.getByTestId('gateway-grid'),
  // sections/gateway/shell/GatewayRail.jsx; GatewayRailScroll.jsx.
  rail: (scope: Scope) => scope.getByTestId('gateway-rail'),
  railScroll: (scope: Scope) => scope.getByTestId('gateway-rail-scroll'),
  // sections/gateway/shell/gateway-icons.jsx: GatewayIcon.
  icons: (scope: Scope) => scope.locator('[data-gateway-icon]'),
  // sections/gateway/keys/KeyManagementSection.jsx -> GatewayPageFrame.testId.
  keysPage: (scope: Scope) => scope.getByTestId('gateway-keys-page'),
  // sections/gateway/providers/ProviderManagementSection.jsx -> frame.testId.
  providersPage: (scope: Scope) => scope.getByTestId('gateway-providers-page'),
  // sections/gateway/settings/BillingCreditsPanel.jsx -> SettingsPanel.testId.
  billingPanel: (scope: Scope) => scope.getByTestId('gateway-billing'),
  // sections/gateway/connectors/IntegrationsSection.jsx / components/IntegrationsGrid.jsx.
  integrationsPage: (scope: Scope) => scope.getByTestId('integrations-page'),
  integrationsGrid: (scope: Scope) => scope.getByTestId('integrations-grid'),
  // sections/gateway/budgets/BudgetRBACSection.jsx uses data.id, NOT array index.
  budgetRow: (scope: Scope, id: string) => scope.getByTestId(`budget-row-${requireId(id)}`),
  // sections/gateway/guardrails/GuardrailManagementSection.jsx.
  guardrailsOff: (scope: Scope) => scope.getByTestId('guardrails-off-banner'),
  // sections/gateway/guardrails/rules/StoredRuleDrawer.jsx and settings/GuardrailConfigTab.jsx.
  storedOnlyChecks: (scope: Scope) => scope.locator('[data-stored-only-check]'),
  // sections/gateway/settings/ui/EnforcementNote.jsx (data-enforcement-kind carries state).
  enforcementNote: (scope: Scope, id: string) => scope.getByTestId(`enforcement-${requireId(id)}`),
  // sections/gateway/logs/attempts/AttemptsTimeline.jsx: AttemptRow renders li.
  // Scope to the selected request drawer. Check the complete ordered outcome set.
  attemptRows: (scope: Scope) => scope.locator('li[data-outcome]'),
  // sections/gateway/fallbacks/FallbacksSection.jsx.
  fallbackSteps: (scope: Scope) => scope.getByTestId('fallback-step'),
  fallbackChains: (scope: Scope) => scope.getByTestId('fallbacks-section-chains'),
  // sections/gateway/providers/RoutingConfigView.jsx.
  failoverCodes: (scope: Scope) => scope.getByTestId('routing-failover-codes'),
  retryCodes: (scope: Scope) => scope.getByTestId('routing-retry-codes'),
};

// Key access controls in sections/keys/view/CreateApiKey.jsx are NOT the gateway
// key table. A spec must use the correct product path rather than conflating them.
export const personalKeySelectors = {
  access: (scope: Scope) => scope.getByTestId('key-access'),
  createdAccess: (scope: Scope) => scope.getByTestId('created-key-access'),
};

// Intentionally no 'run-id' selector: logs/RunLink.jsx attaches that hook only
// in the hidden-runs plain-text arm. It would miss the live Link arm. Use exact
// minted run text/role after the spec pins its selected product branch.
