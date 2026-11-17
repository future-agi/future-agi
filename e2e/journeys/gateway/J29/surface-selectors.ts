import type { Page } from '@playwright/test';
import { gatewaySelectors } from '../_shared/selectors';

// Pinned to candidate da4c81cc623f909ce38f53e01b557ee8374d5adb. These existing
// surfaces are NOT an offered-surface inventory or feature-ON permission.
// Re-pin after FA-733 composition; this gateway key dialog is not the personal
// key flow. No data-testid was invented, and no product source is imported.
export const J29_SELECTORS = {
  // frontend/src/routes/paths.js paths.dashboard.gateway.keys;
  // keys/KeyManagementSection.jsx -> GatewayPageFrame.testId.
  keys: (page: Page) => gatewaySelectors.keysPage(page),
  keyDetails: (page: Page, mintedName: string) => gatewaySelectors.keysPage(page)
    .getByRole('button', { name: `View details for ${mintedName}`, exact: true }),
  // keys/CreateKeyDialog.jsx:74-125. Read/cancel only until 733 is bound.
  keyDialog: (page: Page) => page.getByRole('dialog', { name: 'Create API key', exact: true }),
  // guardrails/GuardrailManagementSection.jsx:TABS, CustomTabs aria-label.
  ruleTabs: (page: Page) => page.getByRole('tablist', { name: 'Guardrails views', exact: true }),
  // settings/OrgConfigEditor.jsx:149-159 and 273-285. This is the actual
  // Save & Activate surface, not RuleDrawer's distinct immediate-save path.
  configDialog: (page: Page) => page.getByRole('dialog', { name: 'Edit Organization Config', exact: true }),
  saveAndActivate: (page: Page) => page.getByRole('dialog', { name: 'Edit Organization Config', exact: true })
    .getByRole('button', { name: 'Save & Activate', exact: true }),
  // gateway/shell/GatewayRail.jsx and gateway-icons.jsx.
  rail: (page: Page) => page.getByRole('navigation', { name: 'AI Gateway', exact: true }),
};

export const GUARDRAIL_TAB_LABELS = ['Rules', 'Analytics', 'Feedback', 'Test', 'Logs'] as const;
// Public navigation paths only; do not use to infer API URLs or response bodies.
export const J29_ROUTES = {
  gatewayKeys: '/dashboard/gateway/keys',
  guardrailRules: '/dashboard/gateway/guardrails',
  gatewaySettings: '/dashboard/gateway/settings',
} as const;
