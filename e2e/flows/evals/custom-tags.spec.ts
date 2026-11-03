import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// EvalTemplateCreateV2RequestSerializer and EvalTemplateDetailResponseResultSerializer
// in futureagi/model_hub/serializers/contracts.py; routes in frontend/src/utils/axios.js.
const CREATE = '/model-hub/eval-templates/create-v2/';
const UI_READY = 60_000;
interface Detail { result: { id: string; tags: string[] } }

test('EVAL-E2E-011: custom evaluation tags survive draft reopening and repeated version saves', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-011', area: 'evals',
    userGoal: 'Keep preset and custom evaluation labels when reopening a draft and repeatedly saving a version',
    steps: ['seed a draft with preset and custom tags through the API',
      'reopen the draft, add a trimmed custom tag, reject its duplicate and leave another pending',
      'publish through Save Evaluation and reopen the saved default version',
      'remove a tag, save pending text as a version, then remove that tag and save again',
      'reload the saved version and inspect the remaining labels'],
    backendChecks: ['draft detail returns the exact seeded tags',
      'publishing retains existing tags and includes confirmed and pending entries exactly once',
      'repeated version saves retain the exact remaining tags without resurrecting removed pending input'],
  }),
}, async ({ page, actor }, testInfo) => {
  // Five UI_READY stages (300s) plus synchronous API calls and navigation headroom.
  test.setTimeout(360_000);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const client = `client${suffix}`;
  const entered = `entered${suffix}`;
  const pending = `pending${suffix}`;
  const later = `later${suffix}`;
  const name = `e2etags${suffix}`;
  const seeded = await actor.api.post<Detail>(CREATE, {
    is_draft: true, eval_type: 'code', tags: ['output_validation', client],
    code: 'def evaluate(**kwargs):\n    return 1.0', code_language: 'python',
    output_type: 'pass_fail', pass_threshold: 0.5,
  });
  const id = seeded.result.id;
  const detailPath = `/model-hub/eval-templates/${id}/detail/`;
  await testInfo.attach('fixture.json', { contentType: 'application/json', body: JSON.stringify({ id, name, client, entered, pending, later }) });
  const draft = await actor.api.get<Detail>(detailPath);
  expect(draft.result.tags).toEqual(['output_validation', client]);

  await test.step('reopen draft and enter tags', async () => {
    await page.goto(`/dashboard/evaluations/create/${id}`);
    await expect(page.getByRole('button', { name: client, exact: true })).toBeVisible({ timeout: UI_READY });
    await page.getByPlaceholder('Eg: Hallucination detector').fill(name);
    const input = page.getByRole('textbox', { name: 'Add custom tag' });
    await input.fill(`  ${entered}  `);
    await input.press('Enter');
    await expect(input).toHaveValue('', { timeout: UI_READY });
    await input.fill(entered);
    await input.press('Enter');
    await expect(page.getByRole('button', { name: entered, exact: true })).toHaveCount(1, { timeout: UI_READY });
    await input.fill(`  ${pending}  `);
    await page.screenshot({ path: testInfo.outputPath('draft-tags.png') });
  }, { timeout: UI_READY });

  await test.step('publish and verify the real detail response', async () => {
    await page.getByRole('button', { name: 'Save Evaluation', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/evaluations/${id}(\\?|$)`), { timeout: UI_READY });
    const saved = await actor.api.get<Detail>(detailPath);
    expect(saved.result.tags).toEqual(['output_validation', client, entered, pending]);
    await testInfo.attach('published-tags.json', { contentType: 'application/json', body: JSON.stringify(saved.result) });
  }, { timeout: UI_READY });

  // EvalDetailPage humanizes extra tag labels. Assert exact labels, not CSS colors.
  await test.step('reopen saved default version and save pending text', async () => {
    await page.goto(`/dashboard/evaluations/${id}`);
    await expect(page.getByRole('button', { name: `Client${suffix}`, exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('button', { name: `Entered${suffix}`, exact: true })).toBeVisible({ timeout: UI_READY });
    await page.getByRole('button', { name: `Pending${suffix}`, exact: true }).press('Backspace');
    await page.getByRole('textbox', { name: 'Add custom tag' }).fill(`  ${later}  `);
    await page.getByRole('button', { name: 'Save Version', exact: true }).click();
    await expect(page.getByRole('textbox', { name: 'Add custom tag' })).toHaveValue('', { timeout: UI_READY });
    await expect(page.getByRole('button', { name: 'Save Version', exact: true })).toBeDisabled({ timeout: UI_READY });
    expect((await actor.api.get<Detail>(detailPath)).result.tags).toEqual(['output_validation', client, entered, later]);
    // The success snackbar overlaps Save; hovering it pauses its dismissal.
    await page.mouse.move(0, 0);
    await expect(page.getByText(/^Version V\d+ saved$/)).toBeHidden({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('remove the previously pending tag and save again', async () => {
    await page.getByRole('button', { name: `Later${suffix}`, exact: true }).press('Backspace');
    await page.getByRole('button', { name: 'Save Version', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Save Version', exact: true })).toBeDisabled({ timeout: UI_READY });
    expect((await actor.api.get<Detail>(detailPath)).result.tags).toEqual(['output_validation', client, entered]);
  }, { timeout: UI_READY });

  await test.step('reload and inspect remaining labels', async () => {
    await page.reload();
    await expect(page.getByRole('button', { name: `Client${suffix}`, exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('button', { name: `Entered${suffix}`, exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('button', { name: `Pending${suffix}`, exact: true })).toHaveCount(0, { timeout: UI_READY });
    await expect(page.getByRole('button', { name: `Later${suffix}`, exact: true })).toHaveCount(0, { timeout: UI_READY });
    await page.getByRole('textbox', { name: 'Add custom tag' }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath('reopened-tags.png') });
  }, { timeout: UI_READY });
});
