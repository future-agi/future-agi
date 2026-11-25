// Not yet proven: written without a managed run or a fallibility proof (the local e2e stack could not run).
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// Browser-side waits: one navigation's or one click's first paint, not a stage.
const UI_READY = 60_000;
// Path from futureagi/simulate/urls.py (harness-environments/<id>/scenario-tags).
const ENVIRONMENTS = '/simulate/api/harness-environments';
// Copy from frontend/.../workspace/scenarios/scenarios.constants.js (SCENARIO_TAGS_COPY, SCENARIOS_COPY).
const TAG_TIP = 'Add or remove a tag on the selected scenarios';
const NEW_TAG = 'Type a new tag and press Enter';
// MAX_TAG_LENGTH and SCENARIO_TAGS_COPY.tooLong in the same file; the API's own limit and wording
// are futureagi/simulate/services/harness_scenario_tags.py (MAX_TAG_LENGTH).
const MAX_TAG_LENGTH = 64;
const TAG_TOO_LONG = `A tag can be at most ${MAX_TAG_LENGTH} characters`;
// How long a refused tag is watched for a write: the bulk menu sends its change on Enter, at once.
const NO_WRITE_WINDOW = 3_000;
const added = (tag: string, n: number) => `Added ${tag} to ${n} scenario${n === 1 ? '' : 's'}`;
const openRow = (name: string) => `Open ${name}`;
// Table from futureagi/simulate/models/harness_scenario_tags.py (HarnessScenarioTags.Meta.db_table).
const TAGS_TABLE = 'simulate_harness_scenario_tags';

test('SIM-E2E-039: a person tags one scenario of an environment, an over-long tag is refused on the page, and only that scenario carries the tag', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-039', area: 'simulate',
    userGoal: 'A developer labels a scenario of their environment with a tag of their own, '
      + 'so they can find and group it later, without changing the versioned suite',
    steps: ['seed a built, versioned Environment with two scenarios (e2e/lib/harness_environment_seed.py)',
            'open its Scenarios tab', 'select one scenario', 'open Tag and type a 65-character tag',
            'type a new tag of a valid length instead',
            'read the environment\'s tags (GET harness-environments/<id>/scenario-tags/)'],
    backendChecks: ['one tag row exists, on the selected scenario, holding only the new tag',
                    'the environment\'s tags are exactly the new tag'],
    browserChecks: ['a 65-character tag says a tag can be at most 64 characters and sends no tag change',
                    'the page confirms the tag was added to one scenario',
                    'the tagged row shows the tag and the other row does not'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(180_000);
  const seed = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    refunds: 1, lookups: 1 });
  await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify(seed) });
  const [tagged, untagged] = seed.scenarioKeys;
  const tag = `e2e-tag-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  await testInfo.attach('minted', { contentType: 'application/json', body: JSON.stringify({ tag, tagged }) });

  await test.step('browser: tag one scenario', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}?tab=scenarios`);
    const row = page.getByRole('row', { name: openRow(tagged) });
    await row.getByRole('checkbox').check({ timeout: UI_READY });
    await page.getByRole('button', { name: TAG_TIP, exact: true }).click({ timeout: UI_READY });
    const input = page.getByPlaceholder(NEW_TAG);
    await input.fill('t'.repeat(MAX_TAG_LENGTH + 1), { timeout: UI_READY });
    const tagWrite = page.waitForRequest((r) => r.url().includes(`${ENVIRONMENTS}/${seed.environmentId}/scenario-tags/`)
      && r.method() !== 'GET', { timeout: NO_WRITE_WINDOW });
    await input.press('Enter');
    await expect(page.getByText(TAG_TOO_LONG, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(tagWrite).rejects.toThrow();

    await input.fill(tag);
    await expect(page.getByText(TAG_TOO_LONG, { exact: true })).toHaveCount(0);
    await input.press('Enter');
    await expect(page.getByText(added(tag, 1))).toBeVisible({ timeout: UI_READY });
    await expect(row.getByText(tag, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByRole('row', { name: openRow(untagged) }).getByText(tag, { exact: true }))
      .toHaveCount(0);
  });

  await test.step('storage: only the selected scenario holds the tag', async () => {
    const rows = await probe.pg<{ scenario_key: string; tags: string[] }>(
      `SELECT scenario_key, tags FROM ${TAGS_TABLE} WHERE environment_id = $1 AND deleted = false`,
      [seed.environmentId],
    );
    expect(rows).toEqual([{ scenario_key: tagged, tags: [tag] }]);
  });

  await test.step('api: the environment\'s tags are exactly the new one', async () => {
    const body = await actor.api.get<{ tags: string[] }>(`${ENVIRONMENTS}/${seed.environmentId}/scenario-tags/`);
    expect(body.tags).toEqual([tag]);
  });
});
