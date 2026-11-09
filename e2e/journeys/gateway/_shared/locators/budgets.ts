import type { Page } from '@playwright/test';
import { gatewaySelectors } from '../selectors';
import { recordCell, recordRow, type LocatorScope } from './scope';

// Product pin: LOCATOR_SOURCE_REF in scope.ts. Citations relative to
// frontend/src/sections/gateway/. These are EXISTING hierarchical budgets,
// not the uncomposed W04 agent/key/run admission-cap UI.
const editor = (page: Page) => page.getByRole('dialog').filter({
  has: page.getByText('Edit Organization Config', { exact: true }),
});

export const orgConfigLocators = {
  // settings/OrgConfigEditor.jsx:142-170. Filter by exact title child rather
  // than depending on an implicit/generated DialogTitle id.
  editor,
  budgetsTab: (page: Page) => editor(page).getByRole('tab', { name: 'Budgets', exact: true }),
  guardrailsTab: (page: Page) => editor(page).getByRole('tab', { name: 'Guardrails', exact: true }),
  // settings/OrgConfigEditor.jsx:273-285. Only this is Save & Activate;
  // guardrails/rules/RuleDrawer has separate Apply/Add check/Save actions.
  saveAndActivate: (page: Page) => editor(page).getByRole('button', { name: 'Save & Activate', exact: true }),
  cancel: (page: Page) => editor(page).getByRole('button', { name: 'Cancel', exact: true }),
};

export type BudgetColumn = 'label' | 'period' | 'used' | 'action' | 'alertThreshold' | 'actions';
export const budgetLocators = {
  // budgets/BudgetEnforcementCard.jsx:62-72 is a FormControlLabel + Switch.
  enforcement: (scope: LocatorScope) => scope.getByRole('checkbox', { name: 'Enforce budgets', exact: true }),
  // settings/BudgetsConfigTab.jsx:440-448. This edits budgets.enabled only.
  orgEnforcement: (page: Page) => editor(page).getByRole('checkbox', { name: 'Enable per-org budgets', exact: true }),
  // BudgetRBACSection.jsx:83 is an existing hook on the label CELL, not the row.
  labelCell: gatewaySelectors.budgetRow,
  // BudgetRBACSection.jsx:234-273,489 getRowId=p.data.id, derived by budgetLevels.
  // Supply that fixture-backed ID; do not assume an agent/run cap scope is it.
  row: (scope: LocatorScope, budgetRowId: string) => recordRow(scope, budgetRowId),
  cell: (scope: LocatorScope, budgetRowId: string, column: BudgetColumn) => recordCell(scope, budgetRowId, column),
};

// OWED F18-P02 / F06: BudgetsConfigTab.jsx:432-570 edits org/team/user/key/tag
// budgets. It has no admission_caps_enabled, policy_revision, activated_at or
// agent/run cap inputs. BudgetEnforcementCard toggles budgets.enabled, NOT cap
// admission. No activation/revision locator or UTC+BYOK cap statement is guessed.
// The existing periodic budget dialog's UTC reset help is not W04 activation.
// Member/Viewer disabled state is an assertion owed to F09, not assumed here.
// No max_charge selector, fixture, header, endpoint or activation is provided.
