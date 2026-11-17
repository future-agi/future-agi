import type { Page } from '@playwright/test';
import { fixtureText, recordCell, recordRow, type LocatorScope } from './scope';

// Product citations below: frontend/src/sections/gateway/ at LOCATOR_SOURCE_REF.
const addDialog = (page: Page) => page.getByRole('dialog').filter({
  has: page.getByText('Add a check', { exact: true }),
});
const ruleDrawer = (page: Page, checkLabel: string) => page.getByRole('dialog', { name: fixtureText(checkLabel), exact: true });
export type RuleColumn = 'label' | 'kind' | 'stage' | 'action' | 'threshold' | 'triggers' | 'blocked' | 'accuracy' | 'status';

export const guardrailLocators = {
  // GuardrailManagementSection.jsx:84-87 names the actual tablist.
  views: (page: Page) => page.getByRole('tablist', { name: 'Guardrails views', exact: true }),
  // rules/RulesView.jsx:211,237,224 -> GatewayFilterBar's search aria-label.
  addCheck: (scope: LocatorScope) => scope.getByRole('button', { name: 'Add check', exact: true }),
  search: (scope: LocatorScope) => scope.getByRole('textbox', { name: 'Search rules', exact: true }),
  // rules/RulesView.jsx:263 getRowId=data.id; rule-columns.jsx:86-146.
  row: (scope: LocatorScope, checkId: string) => recordRow(scope, checkId),
  cell: (scope: LocatorScope, checkId: string, column: RuleColumn) => recordCell(scope, checkId, column),
  // rules/AddCheckDialog.jsx:67-94 and 110-149: buttons include description,
  // so do not assert their full accessible name equals only the short label.
  addDialog,
  searchChecks: (page: Page) => addDialog(page).getByRole('textbox', { name: 'Search checks', exact: true }),
  builtInChoices: (page: Page) => addDialog(page).getByRole('region', { name: 'Built into the gateway', exact: true })
    .getByRole('button'),
  choice: (page: Page, checkLabel: string) => addDialog(page)
    .getByRole('region', { name: 'Built into the gateway', exact: true }).getByRole('button')
    .filter({ has: page.getByText(fixtureText(checkLabel), { exact: true }) }),
  // rules/RuleDrawer.jsx:73,113-117 -> GatewayDetailDrawer role=dialog/title.
  // Rule identity is the exact config check ID in row(), not a row position.
  drawer: ruleDrawer,
  // rules/CheckForm.jsx:58-72 and 88-99. State changes the switch's label.
  enabled: (page: Page, checkLabel: string, currentEnabled: boolean) => ruleDrawer(page, checkLabel)
    .getByRole('checkbox', { name: currentEnabled ? 'On' : 'Off', exact: true }),
  action: (page: Page, checkLabel: string) => ruleDrawer(page, checkLabel).getByLabel('Action', { exact: true }),
  // RuleDrawer.jsx:249-257. Apply changes a draft only; outer OrgConfigEditor
  // Save & Activate is required afterwards. Never conflate these three actions.
  applyDraft: (page: Page, checkLabel: string) => ruleDrawer(page, checkLabel).getByRole('button', { name: 'Apply', exact: true }),
  saveExisting: (page: Page, checkLabel: string) => ruleDrawer(page, checkLabel).getByRole('button', { name: 'Save', exact: true }),
  addNew: (page: Page, checkLabel: string) => ruleDrawer(page, checkLabel).getByRole('button', { name: 'Add check', exact: true }),
};

// catalog/builtin-checks.js:118-124 supplies this check's actual ID and label.
// Presence does not qualify eligibility, enforcement, role access or F15.
export const DATA_LEAKAGE_CHECK = {
  id: 'data-leakage-prevention', label: 'Data Leakage Prevention',
} as const;
