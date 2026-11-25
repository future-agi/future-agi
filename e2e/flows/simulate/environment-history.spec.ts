import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedEnvironment } from '../../lib/environment-seed';

// Coverage this flow does not claim, and what covers it instead: agent version rows, Make default and the
// unsaved-changes bar's View changes (the seed has no agent version and no draft) are in
// frontend/.../workspace/versions/__tests__/versionsPanel.test.jsx and __tests__/EnvironmentWorkspace.test.jsx.

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;
// Paths from futureagi/simulate/views/harness_environment.py (versions, version_diff).
const versionsPath = (env: string) => `/simulate/api/harness-environments/${env}/versions/`;
const diffPath = (env: string, version: string) => `${versionsPath(env)}environment/${version}/diff/`;
// Copy from frontend/.../workspace/workspace.constants.js (VERSIONS_COPY, WORKSPACE_COPY).
const FIRST = 'This is the first version, so there is nothing earlier to compare with.';
const NO_AGENT = 'No agent versions yet.';
const IN_USE = 'runs use this';
const FULL_HISTORY = 'See full history';
// The rule each seeded version adds (lib/environment-seed.ts).
const rule = (n: number) => `rule added in v${n}`;

interface Version { id: string; label: string; is_default: boolean }
interface Versions { environment: Version[]; suite: Version[]; agent: Version[] }
interface Diff { diff: { contract: { rules: { added: string[]; removed: string[] } } } }

test('SIM-E2E-040: a person reads an environment\'s history, compares two versions and rolls back without losing the newer ones', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-040', area: 'simulate',
    userGoal: 'A developer sees how their environment changed from version to version, compares two versions, and '
      + 'rolls back to an earlier one while the newer ones stay in the history',
    steps: ['seed an environment with three environment versions and one scenario version (lib/environment-seed.ts)',
            'open its History tab', 'switch to Scenarios and open its only version', 'switch to Agent',
            'switch back to Environment and open v3', 'compare v3 with v1', 'roll back to v1 from its row\'s menu',
            'follow the version bar\'s scenarios link, then the header\'s See full history'],
    browserChecks: ['Environment lists exactly v3, v2 and v1, and only v3 says runs use this',
                    'the only scenario version says it is the first, and Agent says there are none',
                    'v3 shows only its own rule as added; compared with v1 it shows the rules of v2 and v3',
                    'after the roll back only v1 says runs use this, and all three rows stay',
                    'the bar\'s scenarios link opens Scenarios, and See full history opens Environment'],
    backendChecks: ['the versions API lists v3, v2 and v1 with v3 the default, one scenario version and no agent version',
                    'the diff API adds only v3\'s rule, and v2\'s and v3\'s rules against v1',
                    'after the roll back simulate_hosted_harness_job points at v1 with its scenario version unchanged, '
                      + 'and all three environment versions remain active'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Seeding (60 s) + nine UI_READY browser steps + headroom for the API and storage reads.
  test.setTimeout(660_000);
  const name = `e2e-sim40-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const seed = seedEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, name,
    environmentVersions: 3 });
  await testInfo.attach('seeded', { contentType: 'application/json',
    body: JSON.stringify({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, ...seed }) });
  const [v1, v2, v3] = seed.environmentVersionIds;

  await test.step('api: the three environment versions, newest first, v3 the default', async () => {
    const versions = await actor.api.get<Versions>(versionsPath(seed.environmentId));
    expect(versions.environment.map(({ id, label, is_default }) => ({ id, label, is_default }))).toEqual([
      { id: v3, label: 'v3', is_default: true },
      { id: v2, label: 'v2', is_default: false },
      { id: v1, label: 'v1', is_default: false },
    ]);
    expect(versions.suite.map((one) => one.id)).toEqual([seed.suiteVersionId]);
    expect(versions.agent).toEqual([]);
  });

  const rows = page.locator('[data-testid^="history-entry-"]');
  const row = (piece: string, id: string) => page.getByTestId(`history-entry-${piece}:${id}`);
  const filter = (kind: string) =>
    page.getByRole('group', { name: 'Show versions of' }).getByRole('button', { name: kind, exact: true });
  const toggle = (piece: string, id: string) => row(piece, id).locator('button[aria-expanded]');
  const inUse = page.locator('[data-testid^="history-entry-environment:"]')
    .filter({ has: page.getByText(IN_USE, { exact: true }) });
  const environmentRows = [v3, v2, v1].map((id) => `history-entry-environment:${id}`);
  const expectEnvironmentRows = () => expect.poll(
    () => rows.evaluateAll((all) => all.map((one) => one.getAttribute('data-testid'))), { timeout: UI_READY },
  ).toEqual(environmentRows);

  await test.step('ui: History opens on the environment versions', async () => {
    await page.goto(`/dashboard/simulate/environments/${seed.environmentId}?tab=versions`);
    await expectEnvironmentRows();
    await expect(inUse).toHaveCount(1);
    await expect(inUse).toHaveAttribute('data-testid', environmentRows[0]);
  }, { timeout: UI_READY });

  await test.step('ui: the only scenario version is the first', async () => {
    await filter('Scenarios').click({ timeout: UI_READY });
    await expect(rows).toHaveCount(1, { timeout: UI_READY });
    await toggle('suite', seed.suiteVersionId).click();
    await expect(row('suite', seed.suiteVersionId).getByText(FIRST)).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: there are no agent versions', async () => {
    await filter('Agent').click({ timeout: UI_READY });
    await expect(page.getByText(NO_AGENT)).toBeVisible({ timeout: UI_READY });
    await expect(rows).toHaveCount(0);
  }, { timeout: UI_READY });

  const added = row('environment', v3).getByTestId('diff-group-Rules').locator('ins');
  await test.step('ui: v3 shows what it added', async () => {
    await filter('Environment').click({ timeout: UI_READY });
    await toggle('environment', v3).click({ timeout: UI_READY });
    await expect(added).toHaveText([rule(3)], { timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: compared with v1 it shows what v2 and v3 added', async () => {
    await row('environment', v3).getByRole('combobox').click({ timeout: UI_READY });
    await page.getByRole('option', { name: 'v1', exact: true }).click({ timeout: UI_READY });
    await expect(added).toHaveText([rule(2), rule(3)], { timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('api: the diffs the page showed', async () => {
    const own = await actor.api.get<Diff>(diffPath(seed.environmentId, v3));
    expect(own.diff.contract.rules).toEqual({ added: [rule(3)], removed: [] });
    const sinceFirst = await actor.api.get<Diff>(diffPath(seed.environmentId, v3), { against: v1 });
    expect(sinceFirst.diff.contract.rules).toEqual({ added: [rule(2), rule(3)], removed: [] });
  });

  await test.step('ui: roll back to v1', async () => {
    await page.getByRole('button', { name: 'Version actions, Environment v1' }).click({ timeout: UI_READY });
    await page.getByRole('menuitem', { name: 'Roll back to environment v1' }).click({ timeout: UI_READY });
    await page.getByRole('dialog').getByRole('button', { name: 'Roll back to env v1' }).click({ timeout: UI_READY });
    await expect(row('environment', v1).getByText(IN_USE, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(inUse).toHaveCount(1);
    await expectEnvironmentRows();
  }, { timeout: UI_READY });

  await test.step('storage: the default is v1, the scenarios are unchanged and nothing is deleted', async () => {
    const jobs = await probe.pg<{ environment_version_id: string; suite_version_id: string }>(
      `SELECT environment_version_id::text, suite_version_id::text
         FROM simulate_hosted_harness_job WHERE id = $1`, [seed.environmentId]);
    expect(jobs).toEqual([{ environment_version_id: v1, suite_version_id: seed.suiteVersionId }]);
    const kept = await probe.pg<{ id: string; status: string; deleted: boolean }>(
      `SELECT id::text, status, deleted FROM simulate_harness_environment_version
        WHERE job_id = $1 ORDER BY version_number`, [seed.environmentId]);
    expect(kept).toEqual([v1, v2, v3].map((id) => ({ id, status: 'active', deleted: false })));
  });

  await test.step('ui: the version bar\'s scenarios link opens Scenarios', async () => {
    await page.getByRole('button', { name: 'scenarios v1', exact: true }).click({ timeout: UI_READY });
    await expect(filter('Scenarios')).toHaveAttribute('aria-pressed', 'true', { timeout: UI_READY });
    await expect(row('suite', seed.suiteVersionId)).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: the header\'s See full history opens Environment', async () => {
    await page.getByRole('button', { name: 'env v1', exact: true }).click({ timeout: UI_READY });
    await page.getByRole('button', { name: FULL_HISTORY }).click({ timeout: UI_READY });
    await expect(filter('Environment')).toHaveAttribute('aria-pressed', 'true', { timeout: UI_READY });
    await expectEnvironmentRows();
  }, { timeout: UI_READY });
});
