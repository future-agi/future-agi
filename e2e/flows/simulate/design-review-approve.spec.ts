// Not yet proven: written without a managed run or a fallibility proof (the local e2e stack could not run).
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedDesignReview } from '../../lib/design-review-seed';

// Browser-side waits: one navigation's or one click's first paint, not a stage.
const UI_READY = 60_000;
// Path from futureagi/simulate/views/harness_environment.py (design_review, approve_design).
const ENVIRONMENTS = '/simulate/api/harness-environments';
// Button and line copy from frontend/.../designReview/designReview.constants.js (REVIEW_COPY).
const APPROVE = 'Approve and build';
const removeNamed = (name: string) => `Remove ${name}`;
const removedFromPlan = (name: string) => `${name} was removed from the plan.`;
const EDIT = 'Edit';
const SAVE = 'Save';
const RULE_FIELD = 'Rule';
// MAX_RULE_LENGTH and REVIEW_COPY.ruleTooLong in the same file; the API's own limit is
// futureagi/simulate/serializers/harness_design_review.py (text max_length=2000).
const MAX_RULE_LENGTH = 2000;
const RULE_TOO_LONG = `A rule can be at most ${MAX_RULE_LENGTH} characters`;
// Rule id and text from e2e/lib/design-review-seed.ts (CONTRACT.hard_constraints[0]), numbered by
// futureagi/simulate/services/harness_design_contract.py (_numbered, prefix "rule").
const RULE_ID = 'rule-1';
const RULE_TEXT = 'Run check_otp before any refund';
// Table from futureagi/simulate/models/hosted_harness.py (HostedHarnessJob.Meta.db_table).
const JOBS_TABLE = 'simulate_hosted_harness_job';

interface Review {
  status: string;
  revision: string;
  tools: { id: string; name: string; removed: boolean }[];
  rules: { id: string; text: string; edited: boolean }[];
  edits: { op: string; kind: string; id: string }[];
}

interface StoredReview {
  status: string;
  edits: { op: string; kind: string; id: string; text?: string }[];
  approved_contract_key: string;
  reviewed_contract_key: string;
}

test('SIM-E2E-038: a person reviews a read design, removes a tool, rewords a rule and approves exactly what is on screen', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-038', area: 'simulate',
    userGoal: 'A developer reviews the design read from their agent, takes out a tool the test world should not '
      + 'have, rewords a rule, and approves the design they see so the build starts from it',
    steps: ['seed an environment whose read design waits for approval (e2e/lib/design-review-seed.ts)',
            'open the environment', 'remove the notify tool from the design',
            'reword the first rule past 2000 characters, then to a shorter wording, and save it',
            'read the design again (GET harness-environments/<id>/design-review/)',
            'approve and build'],
    backendChecks: ['the review is approved with the notify removal and the saved rewording of rule-1',
                    'the approved design is stored on the environment, apart from the copy the review opened with',
                    'the design read back after approval marks notify removed, holds the new rule-1 wording, '
                      + 'and is approved'],
    browserChecks: ['notify leaves the design and the page says it was removed',
                    'a rule over 2000 characters says so, keeps every character, and cannot be saved',
                    'approve sends the revision the page shows'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(180_000);
  const name = `e2e-design-review-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const seed = seedDesignReview({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, name });
  await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify(seed) });
  const reviewPath = `${ENVIRONMENTS}/${seed.environmentId}/design-review/`;
  const reworded = `${RULE_TEXT}, checked by ${name}`;
  await testInfo.attach('minted', { contentType: 'application/json', body: JSON.stringify({ reworded }) });

  await test.step('browser: remove notify from the design', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}`);
    await page.getByRole('button', { name: removeNamed('notify') }).click({ timeout: UI_READY });
    await expect(page.getByText(removedFromPlan('notify'))).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('button', { name: removeNamed('notify') })).toHaveCount(0);
    await expect(page.getByRole('button', { name: removeNamed('issue_refund') }).first()).toBeVisible();
  });

  await test.step('browser: an over-long rule is refused on the page, a shorter one is saved', async () => {
    await page.getByRole('button', { name: EDIT, exact: true }).first().click({ timeout: UI_READY });
    const field = page.getByRole('textbox', { name: RULE_FIELD, exact: true });
    await expect(field).toHaveValue(RULE_TEXT, { timeout: UI_READY });
    const tooLong = 'x'.repeat(MAX_RULE_LENGTH + 1);
    await field.fill(tooLong);
    await expect(field).toHaveValue(tooLong);
    await expect(page.getByText(RULE_TOO_LONG, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('button', { name: SAVE, exact: true })).toBeDisabled();

    await field.fill(reworded);
    await expect(page.getByText(RULE_TOO_LONG, { exact: true })).toHaveCount(0);
    const saved = page.waitForResponse((r) => r.url().endsWith(`${reviewPath}edits/`)
      && r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByRole('button', { name: SAVE, exact: true }).click();
    const response = await saved;
    expect(response.request().postDataJSON()).toEqual({ op: 'edit', kind: 'rule', id: RULE_ID, text: reworded });
    expect(response.ok()).toBe(true);
    await expect(page.getByText(reworded, { exact: true })).toBeVisible({ timeout: UI_READY });
  });

  const shown = await actor.api.get<Review>(reviewPath);
  await testInfo.attach('review-before-approve', { contentType: 'application/json', body: JSON.stringify(shown) });

  await test.step('browser: approve sends the revision on screen', async () => {
    const approved = page.waitForResponse((r) => r.url().endsWith(`${reviewPath}approve/`)
      && r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByRole('button', { name: APPROVE }).click({ timeout: UI_READY });
    const response = await approved;
    expect(response.request().postDataJSON()).toEqual({ revision: shown.revision });
    expect(response.status()).toBe(200);
  });

  await test.step('storage: the review is approved with both changes and its own stored design', async () => {
    const rows = await probe.pg<{ review: StoredReview }>(
      `SELECT payload->'metadata'->'design_review' AS review FROM ${JOBS_TABLE} WHERE id = $1`,
      [seed.environmentId],
    );
    expect(rows).toHaveLength(1);
    const { review } = rows[0];
    expect(review.status).toBe('approved');
    expect(review.edits.map(({ op, kind, id, text }) => ({ op, kind, id, text })))
      .toEqual([{ op: 'remove', kind: 'tool', id: 'notify', text: undefined },
                { op: 'edit', kind: 'rule', id: RULE_ID, text: reworded }]);
    const prefix = `environment-contracts/${actor.organizationId}/${seed.environmentId}/`;
    expect(review.approved_contract_key.startsWith(prefix)).toBe(true);
    expect(review.reviewed_contract_key.startsWith(prefix)).toBe(true);
    expect(review.approved_contract_key).not.toBe(review.reviewed_contract_key);
  });

  await test.step('api: the design reads back approved with notify removed and rule-1 reworded', async () => {
    const after = await actor.api.get<Review>(reviewPath);
    expect(after.status).toBe('approved');
    expect(after.tools.filter((tool) => tool.removed).map((tool) => tool.name)).toEqual(['notify']);
    expect(after.rules.filter((rule) => rule.edited).map(({ id, text }) => ({ id, text })))
      .toEqual([{ id: RULE_ID, text: reworded }]);
    expect(after.revision).not.toBe(shown.revision);
  });
});
