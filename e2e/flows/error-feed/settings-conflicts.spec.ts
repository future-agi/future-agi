import { test, expect } from '../../lib/error-feed-fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { LABELS, UI_READY, openSettings, readSettings, numbers, saveSettings,
  expectStored } from '../../lib/error-feed-settings';

test('ESC-E2E-008: Resolve concurrent settings edits without losing another save', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ESC-E2E-008', area: 'error-feed',
    userGoal: 'An administrator reloads a conflicting edit and explicitly reapplies their changes',
    steps: ['open two sessions at one revision', 'save in the first session',
      'see a conflict with retained draft in the second', 'reload and explicitly save a new edit'],
    backendChecks: ['real stale PATCH returns 409 and leaves stored values and revision unchanged',
      'scoped API and PG policy equal the last successful edit, with exactly two revision increments'],
  }),
}, async ({ browser, scopeActors, scopeProbe }) => {
  // Actor provisioning + six first-paint/save actions.
  test.setTimeout(540_000);
  const actor = scopeActors.ownerA;
  const initial = await readSettings(scopeActors, actor);
  const first = await openSettings(browser, scopeActors, actor);
  const second = await openSettings(browser, scopeActors, actor);
  await first.dialog.getByRole('spinbutton', { name: LABELS.min_findings }).fill('35');
  await second.dialog.getByRole('spinbutton', { name: LABELS.min_findings }).fill('40');
  await saveSettings(first.page, first.dialog);
  const saved = { ...numbers(initial.values), min_findings: 35 };
  await expectStored(scopeProbe, actor, saved, initial.revision + 1);
  await saveSettings(second.page, second.dialog, 409);
  await expect(second.dialog.getByText('Settings changed. Reload the latest settings before saving.'))
    .toBeVisible({ timeout: UI_READY });
  await expect(second.dialog.getByRole('spinbutton', { name: LABELS.min_findings })).toHaveValue('40');
  await expectStored(scopeProbe, actor, saved, initial.revision + 1);
  expect(numbers((await readSettings(scopeActors, actor)).values)).toEqual(saved);
  await second.dialog.getByRole('button', { name: 'Reload settings' }).click();
  await expect(second.dialog.getByRole('spinbutton', { name: LABELS.min_findings }))
    .toHaveValue('35', { timeout: UI_READY });
  await second.dialog.getByRole('spinbutton', { name: LABELS.min_findings }).fill('40');
  await saveSettings(second.page, second.dialog);
  const final = { ...saved, min_findings: 40 };
  await expectStored(scopeProbe, actor, final, initial.revision + 2);
  expect(numbers((await readSettings(scopeActors, actor)).values)).toEqual(final);
});
