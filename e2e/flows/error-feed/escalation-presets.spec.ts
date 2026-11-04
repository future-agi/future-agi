import type { Locator } from '@playwright/test';
import { test, expect } from '../../lib/error-feed-fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { LABELS, UI_READY, openSettings, reopenSettings, readSettings, expectValues,
  saveSettings, expectStored, numbers } from '../../lib/error-feed-settings';

// frontend/src/pages/dashboard/error-feed/components/escalationSettings.js PRESETS.calm.
const CALM = { min_findings: 50, entry_multiplier: 4, entry_delta: 25,
  entry_confirmation_minutes: 30, recovery_min: 15, recovery_multiplier: 2,
  recovery_delta: 8, recovery_confirmation_minutes: 20 };

// EscalationSettingsDialog.jsx: each ToggleButton is named "<label>: <description>"
// inside the "Sensitivity" group and exposes aria-pressed.
function preset(dialog: Locator, label: string) {
  return dialog.getByRole('group', { name: 'Sensitivity', exact: true })
    .getByRole('button', { name: new RegExp(`^${label}:`) });
}

async function expectPressed(dialog: Locator, label: string) {
  const pressed = dialog.getByRole('group', { name: 'Sensitivity', exact: true })
    .getByRole('button', { pressed: true });
  await expect(pressed).toHaveCount(1, { timeout: UI_READY });
  await expect(pressed).toHaveAccessibleName(new RegExp(`^${label}:`));
}

test('ESC-E2E-009: Pick a sensitivity preset instead of typing thresholds', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ESC-E2E-009', area: 'error-feed',
    userGoal: 'An administrator picks a sensitivity preset instead of typing eight thresholds, and exactly that preset is saved for the workspace',
    steps: ['open settings on Balanced with the defaults', 'pick Calm and see its eight values',
      'save and reopen on Calm', 'fine-tune one value, see Custom and Edited, save',
      'reopen, pick Balanced, save'],
    backendChecks: ['PG policy holds Calm\'s eight values, revision +1 and the updating user',
      'scoped settings API returns Calm\'s values',
      'a fine-tuned save stores Calm with only min_findings changed, revision +2',
      'picking Balanced stores the server defaults in PG and the API, revision +3'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, testInfo) => {
  // Five bounded save/reopen rounds plus actor provisioning (120s).
  test.setTimeout(900_000);
  const actor = scopeActors.ownerA;
  await testInfo.attach('actor', { contentType: 'application/json', body: JSON.stringify({
    organizationId: actor.organizationId, workspaceId: actor.workspaceId, userId: actor.userId }) });
  const initial = await readSettings(scopeActors, actor);
  const defaults = numbers(initial.defaults);
  expect(numbers(initial.values)).toEqual(defaults);
  const { page } = await openSettings(browser, scopeActors, actor);
  let dialog = page.getByRole('dialog', { name: 'Escalation settings', exact: true });

  await test.step('UI: Balanced is the default and Calm fills all eight values', async () => {
    await expectPressed(dialog, 'Balanced');
    await expectValues(dialog, defaults);
    await preset(dialog, 'Calm').click();
    await expectPressed(dialog, 'Calm');
    await expectValues(dialog, CALM);
    await saveSettings(page, dialog);
  });
  await expectStored(scopeProbe, actor, CALM, initial.revision + 1);
  expect(numbers((await readSettings(scopeActors, actor)).values)).toEqual(CALM);

  const tuned = { ...CALM, min_findings: 60 };
  await test.step('UI: reopen on Calm, fine-tune one value into Custom', async () => {
    dialog = await reopenSettings(page);
    await expectPressed(dialog, 'Calm');
    await expectValues(dialog, CALM);
    await dialog.getByRole('spinbutton', { name: LABELS.min_findings, exact: true }).fill('60');
    await expectPressed(dialog, 'Custom');
    await expect(dialog.getByText('Edited', { exact: true })).toBeVisible({ timeout: UI_READY });
    await saveSettings(page, dialog);
  });
  await expectStored(scopeProbe, actor, tuned, initial.revision + 2);

  await test.step('UI: reopen on Custom and pick Balanced', async () => {
    dialog = await reopenSettings(page);
    await expectPressed(dialog, 'Custom');
    await expectValues(dialog, tuned);
    await preset(dialog, 'Balanced').click();
    await expectPressed(dialog, 'Balanced');
    await expectValues(dialog, defaults);
    await saveSettings(page, dialog);
  });
  await expectStored(scopeProbe, actor, defaults, initial.revision + 3);
  expect(numbers((await readSettings(scopeActors, actor)).values)).toEqual(defaults);
});
