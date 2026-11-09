import type { Locator, Page } from '@playwright/test';

type Scope = Page | Locator;

/** Existing pre-cap UI only. Provenance is the actual foundation worktree at
 * 7543e2d995fe58b620745aa81ce1a76c8855ef0e, not the unmerged G8B input.
 * No agent/run cap selector, UTC/BYOK help text or activation timestamp is
 * invented. Their product/selector bindings remain W04-01/03/07 debts.
 */
export const budgetSelectors = {
  // frontend/src/sections/gateway/budgets/BudgetEnforcementCard.jsx:63-72.
  // MUI Switch retains the checkbox role; the visible FormControlLabel is exact.
  enforcement: (scope: Scope) => scope.getByRole('checkbox', { name: 'Enforce budgets', exact: true }),
  // settings/OrgConfigEditor.jsx:142-159. Scope nested controls to this dialog,
  // not another concurrently mounted panel. No positional .first() selection.
  editor: (scope: Scope) => scope.getByRole('dialog').filter({
    hasText: 'Edit Organization Config',
  }),
  // settings/orgConfigTabs.js:17, rendered as Tab in OrgConfigEditor.jsx:168-170.
  budgetsTab: (editor: Locator) => editor.getByRole('tab', { name: 'Budgets', exact: true }),
  // settings/BudgetsConfigTab.jsx:440-448.
  orgEnforcement: (editor: Locator) => editor.getByRole('checkbox', { name: 'Enable per-org budgets', exact: true }),
  // settings/OrgConfigEditor.jsx:277-285. Disabled for non-admin roles.
  saveAndActivate: (editor: Locator) => editor.getByRole('button', { name: 'Save & Activate', exact: true }),
};

// These locators do not implement the Admin/member/viewer fixture or prove role
// permissions. FOUNDATION F09 remains mandatory; workspaceAdmin is not org Admin.
// Budget-row identities/grid exact-set reads stay in immutable _shared/selectors.
