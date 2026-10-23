import { test, expect } from '../../lib/error-feed-fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { SETTINGS_PATH, LABELS, UI_READY, openSettings, reopenSettings,
  readSettings, fillValues, expectValues, saveSettings, expectStored, numbers } from '../../lib/error-feed-settings';

test('ESC-E2E-004: Configure and restore workspace escalation thresholds', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ESC-E2E-004', area: 'error-feed',
    userGoal: 'An administrator saves thresholds and can cancel, retry, or restore defaults safely',
    steps: ['inspect defaults', 'reject invalid thresholds', 'save eight valid values',
      'cancel edits and a reset', 'retain and retry a failed save', 'save a reset'],
    backendChecks: ['scoped settings API equals each saved policy',
      'PG policy has all eight values, one revision per changed save, and the updating user',
      'canceled and failed saves do not mutate the policy'],
  }),
}, async ({ browser, scopeActors, scopeProbe }) => {
  // Twelve bounded browser actions plus actor provisioning (120s).
  test.setTimeout(900_000);
  const actor = scopeActors.ownerA;
  const initial = await readSettings(scopeActors, actor);
  const defaults = numbers(initial.defaults);
  expect(numbers(initial.values)).toEqual(defaults);
  expect(defaults).toEqual({ min_findings: 20, entry_multiplier: 3, entry_delta: 10,
    entry_confirmation_minutes: 15, recovery_min: 5, recovery_multiplier: 1.5,
    recovery_delta: 3, recovery_confirmation_minutes: 10 });
  const { page } = await openSettings(browser, scopeActors, actor);
  let dialog = page.getByRole('dialog', { name: 'Escalation settings', exact: true });
  const custom = { min_findings: 30, entry_multiplier: 4, entry_delta: 15,
    entry_confirmation_minutes: 20, recovery_min: 8, recovery_multiplier: 2,
    recovery_delta: 4, recovery_confirmation_minutes: 15 };
  await test.step('UI: defaults, validation and all eight fields', async () => {
    await expectValues(dialog, defaults);
    await fillValues(dialog, { ...custom, min_findings: 4 });
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(dialog.getByText('Must be lower than the entry minimum.')).toBeVisible({ timeout: UI_READY });
    expect((await readSettings(scopeActors, actor)).revision).toBe(initial.revision);
    await fillValues(dialog, custom);
    await saveSettings(page, dialog);
  });
  await expectStored(scopeProbe, actor, custom, initial.revision + 1);
  expect(numbers((await readSettings(scopeActors, actor)).values)).toEqual(custom);
  await test.step('UI/API: canceled drafts and resets do not persist', async () => {
    dialog = await reopenSettings(page);
    await expectValues(dialog, custom);
    await dialog.getByRole('spinbutton', { name: LABELS.min_findings }).fill('40');
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
    dialog = await reopenSettings(page);
    await expectValues(dialog, custom);
    await dialog.getByRole('button', { name: 'Reset to defaults' }).click();
    await expectValues(dialog, defaults);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
    await expectStored(scopeProbe, actor, custom, initial.revision + 1);
  });
  const retried = { ...custom, min_findings: 40 };
  await test.step('UI/API: transport failure retains the draft for a real retry', async () => {
    dialog = await reopenSettings(page);
    await fillValues(dialog, retried);
    // Deliberately abort only this save; all successful requests reach the API.
    const failure = async (route: import('@playwright/test').Route) => {
      if (route.request().method() === 'PATCH') await route.abort();
      else await route.continue();
    };
    await page.route(`**${SETTINGS_PATH}`, failure);
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(dialog.getByText('Could not save escalation settings. Your changes are retained.'))
      .toBeVisible({ timeout: UI_READY });
    await expectValues(dialog, retried);
    await expectStored(scopeProbe, actor, custom, initial.revision + 1);
    await page.unroute(`**${SETTINGS_PATH}`, failure);
    await saveSettings(page, dialog);
    await expectStored(scopeProbe, actor, retried, initial.revision + 2);
  });
  await test.step('UI/API/storage: reset persists only after Save', async () => {
    dialog = await reopenSettings(page);
    await dialog.getByRole('button', { name: 'Reset to defaults' }).click();
    await expectValues(dialog, defaults);
    await saveSettings(page, dialog);
    await expectStored(scopeProbe, actor, defaults, initial.revision + 3);
    expect(numbers((await readSettings(scopeActors, actor)).values)).toEqual(defaults);
    dialog = await reopenSettings(page);
    await expectValues(dialog, defaults);
  });
});
