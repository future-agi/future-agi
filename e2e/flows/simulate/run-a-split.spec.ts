import type { Request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// Coverage this flow does not claim, and what covers it instead:
// - The train split, a run of chosen scenarios of a split, a split refused on unlocked versions, a coding
//   agent's split refused, and a re-run keeping its split: simulate/tests/test_harness_versioning_picks.py
//   and run/__tests__/RunDialog.test.jsx.
// - Compare withholding its verdicts across splits or locked versions: simulate/tests/test_harness_run_compare.py
//   and runs/summary/__tests__/RunCompare.test.jsx.
// - What the run does once it starts: the E2E stack has no simulation runner (see
//   epoch-limits-rerun.spec.ts), so the flow asserts up to the persisted run job and its reads, never
//   its calls or scores.
//
// Deliberate failures, one run per check; each run must fail on that check and no earlier one.
// Not run locally: CI's E2E job is this flow's only run, and none of these has been watched failing.
// 1. leave the split out of the body in frontend/src/api/simulate-environments/harnessEnvironments.js
//    (runHarnessEnvironment): the POST body has no split and its versions answer split null.
// 2. drop the `member_split == split` filter in simulate/services/harness_versioning/locked_versions.py
//    (select_split): the run records all 12 scenarios, not the 4 test ones.
// 3. drop "split" and "locked_version" from the run's update_fields in simulate/services/hosted_harness.py
//    (create_selected_harness_run): the 202 still names them, the job row has neither.
// 4. drop lockedVersionSplitInline from the header in workspace/runs/detail/RunDetail.jsx: the sub-line's
//    hover card has no "lock <label> · test".
// 5. drop LockedVersionSplitTag from workspace/runs/summary/SummaryTable.jsx: the run's row has no tag.

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;
// Routes, pinned from simulate/views/harness_environment.py (versions, versions/lock, run).
const ENVIRONMENTS = '/simulate/api/harness-environments';
const versions = (env: string) => `${ENVIRONMENTS}/${env}/versions/`;
const LOCK = (env: string) => `${ENVIRONMENTS}/${env}/versions/lock/`;
const RUN = (env: string) => `${ENVIRONMENTS}/${env}/run/`;
// Copy, pinned from frontend/src/sections/simulate/environments/workspace/workspace.constants.js
// (WORKSPACE_COPY.run, RUN_DIALOG_COPY.title, RUN_DIALOG_COPY.split, lockedVersionSplitLabel,
// lockedVersionSplitInline). The run button reads Run, Run anyway or "Run <n> scenarios"
// (RUN_DIALOG_COPY.run, runAnyway, runCount), as readiness has it.
const RUN_BUTTON = 'Run simulation';
const DIALOG_TITLE = 'Run simulation';
const SPLIT_FIELD = /^Split/;
const RUN_ACTION = /^Run( anyway| \d+ scenarios?)?$/;
const testOption = (n: number) => `Test split · ${n} scenario${n === 1 ? '' : 's'}`;
const testLine = (label: string, n: number) => `Runs the test split of lock ${label}: ${n} scenario${n === 1 ? '' : 's'}.`;
const rowTag = (label: string) => `Lock ${label} · Test`;
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// Scorable scenarios the seed holds: 6 refunds + 6 lookups, each with one sub-goal
// (e2e/lib/harness_environment_seed.py::_scenario). The test split follows
// simulate/services/harness_versioning/locked_versions.py::target_test_size, as SIM-E2E-042 pins it.
const REFUNDS = 6;
const LOOKUPS = 6;
const SCORABLE = REFUNDS + LOOKUPS;
const TEST_TASKS = Math.min(Math.ceil(0.3 * SCORABLE), Math.max(60, Math.ceil(0.2 * SCORABLE)));

interface LockedVersion { id: string; label: string; unlocked_at: string | null; train: number; test: number }
interface PieceVersion { id: string; label: string | null; is_default: boolean }
interface Versions { environment: PieceVersion[]; suite: PieceVersion[]; locked_versions: LockedVersion[] }
interface Ref { id: string; label: string | null }
// HarnessRunCreateResponseSerializer and HarnessRunPinnedVersionsSerializer
// (simulate/serializers/harness_job.py).
interface RunCreated {
  environment_id: string; job_id: string; run_test_id: string; test_execution_id: string;
  scenario_count: number; trials: number;
  versions: { agent: Ref | null; environment: Ref | null; suite: Ref | null; split: string | null;
    locked_version: { id: string; label: string; locked: boolean } | null };
}

test(
  'SIM-E2E-045: an author runs the test split of a locked version and sees the run tagged with the lock and the split',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'SIM-E2E-045',
      area: 'simulate',
      userGoal:
        'An author whose environment\'s default versions are locked starts a run of only the lock\'s test split from the Run dialog, and the run, its header and the runs table name the lock and the split',
      steps: [
        'seed a built, versioned environment with 12 scorable scenarios (e2e/lib/harness_environment_seed.py)',
        'lock its default environment and scenario versions through the API (versions/lock)',
        'open the environment and choose Run simulation',
        'in the Run dialog pick "Test split · 4 scenarios" in the Split field, read the split line, and choose Run',
        'land on the run\'s page, read its title, and hover its sub-line',
        'open the Runs tab and read the run\'s row',
      ],
      backendChecks: [
        'the lock answers 200 with one locked version on the default pair, split 8 train and 4 test',
        'from the Run click to its answer, the only mutating request is one POST to run/ with body {trials: 1, split: "test"}, answered 202 with the default agent, environment and scenario versions, split test and the locked version, still locked',
        'simulate_hosted_harness_job holds the run with split test and that locked version, and its selected scenarios are exactly the lock\'s test-split members in simulate_harness_locked_version_member; the 202 counts 4 scenarios',
      ],
      browserChecks: [
        'the dialog offers "Test split · 4 scenarios" and, once picked, reads "Runs the test split of lock <label>: 4 scenarios."',
        'the run title reads "Run <n>", its sub-line starts "agent <vN> · env <vN>", and the sub-line\'s hover card reads "scenarios <vN> · lock <label> · test"',
        'the run\'s row in the runs table carries the "Lock <label> · Test" tag',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    // Seeding (one container exec), a lock, a page load, a dialog, a run start and two page reads.
    test.setTimeout(300_000);
    const seed = seedHarnessEnvironment({
      organizationId: actor.organizationId, workspaceId: actor.workspaceId,
      refunds: REFUNDS, lookups: LOOKUPS,
    });
    const env = seed.environmentId;
    expect(seed.scenarioKeys).toHaveLength(SCORABLE);
    await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify(seed) });

    // Check 1: lock the default pair, as SIM-E2E-042's dialog does.
    const locked = await actor.api.request<Versions>('post', LOCK(env), {
      data: { environment_version_id: seed.environmentVersionId, suite_version_id: seed.suiteVersionId },
    });
    expect(locked.status).toBe(200);
    expect(locked.body.locked_versions).toHaveLength(1);
    const [lockedVersion] = locked.body.locked_versions;
    expect(lockedVersion).toMatchObject({ unlocked_at: null, train: SCORABLE - TEST_TASKS, test: TEST_TASKS });
    const labelOf = (pieces: PieceVersion[], id: string) => pieces.find((one) => one.id === id)?.label;
    const environmentLabel = labelOf(locked.body.environment, seed.environmentVersionId);
    const suiteLabel = labelOf(locked.body.suite, seed.suiteVersionId);
    expect(environmentLabel).toMatch(/^v\d+$/);
    expect(suiteLabel).toMatch(/^v\d+$/);
    await testInfo.attach('locked-version', { contentType: 'application/json',
      body: JSON.stringify({ lockedVersionId: lockedVersion.id, label: lockedVersion.label }) });

    const dialog = page.getByRole('dialog', { name: DIALOG_TITLE });
    await test.step('open the Run dialog and pick the test split', async () => {
      await page.goto(`/dashboard/simulate/environments/${env}`);
      await page.getByRole('button', { name: RUN_BUTTON }).click({ timeout: UI_READY });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('combobox', { name: SPLIT_FIELD }).click({ timeout: UI_READY });
      await page.getByRole('option', { name: testOption(TEST_TASKS), exact: true }).click({ timeout: UI_READY });
      await expect(dialog.getByText(testLine(lockedVersion.label, TEST_TASKS), { exact: true }))
        .toBeVisible({ timeout: UI_READY });
    });

    // Check 2 records from the Run click to its answer: every mutating request the run start makes.
    const sent: { method: string; path: string; body: unknown }[] = [];
    const record = (request: Request) => {
      if (request.method() !== 'GET' && request.url().includes('/simulate/')) {
        sent.push({ method: request.method(), path: new URL(request.url()).pathname, body: request.postDataJSON() });
      }
    };
    const created = await test.step('start the run', async () => {
      const button = dialog.getByRole('button', { name: RUN_ACTION });
      await expect(button).toBeEnabled({ timeout: UI_READY });
      page.on('request', record);
      const answered = page.waitForResponse((r) => r.url().endsWith(RUN(env))
        && r.request().method() === 'POST', { timeout: UI_READY });
      await button.click();
      const response = await answered;
      page.off('request', record);
      expect(response.status()).toBe(202);
      return (await response.json()) as RunCreated;
    });
    await testInfo.attach('run', { contentType: 'application/json', body: JSON.stringify(created) });

    // Check 2.
    expect(sent.map((s) => `${s.method} ${s.path}`)).toEqual([`POST ${RUN(env)}`]);
    expect(sent[0].body).toEqual({ trials: 1, split: 'test' });
    expect(created).toMatchObject({ environment_id: env, trials: 1 });
    expect(created.versions).toMatchObject({
      agent: { id: seed.agentVersionId },
      environment: { id: seed.environmentVersionId, label: environmentLabel },
      suite: { id: seed.suiteVersionId, label: suiteLabel },
      split: 'test',
      locked_version: { id: lockedVersion.id, label: lockedVersion.label, locked: true },
    });

    // Check 3: the run job records the split and the lock, and runs exactly the lock's test split.
    const jobs = await probe.pg<{ split: string | null; locked_version_id: string | null; keys: string[] | null }>(
      `SELECT split, locked_version_id::text, payload->'metadata'->'selected_scenario_keys' AS keys
         FROM simulate_hosted_harness_job WHERE id = $1 AND organization_id = $2`,
      [created.job_id, actor.organizationId]);
    expect(jobs).toHaveLength(1);
    expect([jobs[0].split, jobs[0].locked_version_id]).toEqual(['test', lockedVersion.id]);
    const testMembers = await probe.pg<{ scenario_key: string }>(
      `SELECT h.scenario_key
         FROM simulate_harness_locked_version_member m
         JOIN simulate_hosted_harness_scenario h ON h.id = m.scenario_id
        WHERE m.locked_version_id = $1 AND m.split = 'test'`, [lockedVersion.id]);
    expect(testMembers).toHaveLength(TEST_TASKS);
    expect([...(jobs[0].keys ?? [])].sort()).toEqual(testMembers.map((one) => one.scenario_key).sort());
    expect(created.scenario_count).toBe(TEST_TASKS);

    const agentLabel = created.versions.agent?.label ?? '-';
    const ordinal = await test.step('read the run header and its hover card', async () => {
      await expect(page).toHaveURL(
        new RegExp(`/dashboard/simulate/environments/${env}/runs/${created.run_test_id}/${created.test_execution_id}`),
        { timeout: UI_READY },
      );
      // The title is "Run <n>". The sub-line keeps agent, env and when; its hover card names the rest of
      // what the run was pinned to: scenarios version, lock and split (RunDetail.jsx, runDetails).
      const title = page.getByText(/^Run \d+$/);
      await expect(title).toBeVisible({ timeout: UI_READY });
      const subLine = page.getByText(new RegExp(`^agent ${escape(agentLabel)} · env ${environmentLabel}(?: · |$)`));
      await subLine.hover();
      await expect(page.getByRole('tooltip')).toContainText(
        `scenarios ${suiteLabel} · lock ${lockedVersion.label} · test`,
        { timeout: UI_READY },
      );
      return /^Run (\d+)$/.exec(((await title.textContent()) ?? '').trim())?.[1];
    });
    expect(ordinal).toMatch(/^\d+$/);

    await test.step('read the run\'s row in the runs table', async () => {
      await page.goto(`/dashboard/simulate/environments/${env}?tab=runs`);
      // The row number badge sits right before the label ("1Run 1 · agent v1"), so match the label itself.
      const row = page.getByRole('row').filter({ has: page.getByText(new RegExp(`^Run ${ordinal}(?: · |$)`)) });
      await expect(row.getByText(rowTag(lockedVersion.label), { exact: true })).toBeVisible({ timeout: UI_READY });
    });
  },
);
