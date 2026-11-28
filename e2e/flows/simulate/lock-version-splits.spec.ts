import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// Coverage this flow does not claim, and what covers it instead:
// - Unlock (admin only, a member refused), a second lock refused, a newer default refused as
//   version_conflict, a suite too small to lock, and a split that never moves once given:
//   simulate/tests/test_harness_locked_versions.py.
// - The Reference passes check: this seed writes no suite validation, so the card reads "Not proved"
//   and the flow does not look at it.
//
// Deliberate failures, one run per check; each run must fail on that check and no earlier one:
// 1. seed 10 scorable scenarios instead of 12: the card reads "Can't lock yet" and the preview is refused.
// 2. have the lock POST name a stale suite version id: the card never reads Locked.
// 3. mark every seeded scenario's sub_goals empty: no split rows are written.
// 4. change the test count the formula expects by one: the split rows disagree.
// 5. delete the locked-version check in simulate/services/harness_versioning/locked_versions.py
//    (require_no_locked_version): the DELETE answers 204.

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;
// Routes, pinned from simulate/views/harness_environment.py (versions, versions/lock-preview,
// versions/lock, destroy).
const ENVIRONMENTS = '/simulate/api/harness-environments';
const versions = (env: string) => `${ENVIRONMENTS}/${env}/versions/`;
const LOCK_PREVIEW = (env: string) => `${ENVIRONMENTS}/${env}/versions/lock-preview/`;
const LOCK = (env: string) => `${ENVIRONMENTS}/${env}/versions/lock/`;
// Copy, pinned from frontend/src/sections/simulate/environments/workspace/overview/overview.constants.js
// (LOCK_COPY).
const LOCK_BUTTON = 'Lock versions';
const LOCK_ACTION = 'Lock versions';
// The default pair, as the card names it: "environment vN, scenarios vN".
const pairOf = (environmentLabel: string, suiteLabel: string) =>
  `environment ${environmentLabel}, scenarios ${suiteLabel}`;
// A locked version's label is "YYYY.M.N" (locked_versions.py::_next_label).
const LABEL = /^\d{4}\.\d{1,2}\.\d+$/;
// Scorable scenarios the seed holds: 6 refunds + 6 lookups, each with one sub-goal
// (e2e/lib/harness_environment_seed.py::_scenario). The test split follows
// simulate/services/harness_versioning/locked_versions.py::target_test_size; the lock needs at least 4.
const REFUNDS = 6;
const LOOKUPS = 6;
const SCORABLE = REFUNDS + LOOKUPS;
const TEST_TASKS = Math.min(Math.ceil(0.3 * SCORABLE), Math.max(60, Math.ceil(0.2 * SCORABLE)));
const TRAIN_TASKS = SCORABLE - TEST_TASKS;

interface LockedVersion {
  id: string; label: string; environment_version_id: string; suite_version_id: string;
  environment_label: string; locked_at: string; unlocked_at: string | null; train: number; test: number;
}
interface Lock {
  environment_version_id: string; suite_version_id: string; proved: boolean; can_lock: boolean;
  refusal: { code: string; message: string } | null; train: number | null; test: number | null;
  test_target: number | null;
}
interface PieceVersion { id: string; label: string | null; is_default: boolean }
interface Versions { environment: PieceVersion[]; suite: PieceVersion[]; locked_versions: LockedVersion[] }

test(
  'SIM-E2E-042: an author locks an environment\'s default versions and sees the locked version\'s label with the train and test split',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'SIM-E2E-042',
      area: 'simulate',
      userGoal:
        'An author whose environment has enough scorable scenarios opens its Overview, sees the version is ready to lock, locks it, and reads its label with the train and test task counts; the environment then cannot be deleted until the version is unlocked',
      steps: [
        'seed a built, versioned environment with 12 scorable scenarios (e2e/lib/harness_environment_seed.py)',
        "read the environment's versions: nothing is locked",
        'read the lock preview: the default pair can be locked',
        'open the Overview tab and read the "Lock environment <vN>, scenarios <vN>" card as Ready',
        'choose Lock versions on the card and confirm Lock versions in the dialog',
        'read the card as "Locked: environment <vN>, scenarios <vN>", with no status badge and the lock line',
        'delete the environment through the API',
      ],
      backendChecks: [
        'before the lock the versions read has no locked version and no lock key, and the versions/lock-preview read says can_lock is true with 8 train and 4 test tasks',
        'from the confirm click on, the only mutating /simulate/ request is one POST to the versions/lock route, naming the default environment and suite version ids, answered 200 with one locked version',
        'simulate_harness_locked_version has one row for the environment, unlocked_at NULL, with the default environment and suite version ids and a YYYY.M.N label',
        'simulate_harness_task_split has one row per seeded scenario, each first given by that locked version, and the test rows number what target_test_size gives (4 of 12)',
        'simulate_harness_locked_version_member holds that locked version\'s roster: one row per seeded scenario, each with the split simulate_harness_task_split gave it',
        'the versions read lists that locked version with the same label and the 8 train and 4 test counts, and the versions/lock-preview read refuses with version_already_locked',
        'deleting the environment answers 409 version_locked, "Lock <label> covers this version. A workspace admin or owner must unlock it first.", and the environment is still readable',
      ],
      browserChecks: [
        'the card reads "Lock environment <vN>, scenarios <vN>" (the default pair) and Ready, with "Lock versions" enabled',
        'the dialog reads "Lock environment <vN>, scenarios <vN>?"; after confirming the card title is "Locked: environment <vN>, scenarios <vN>", it shows no status badge and its line reads "Lock <label> · <d Mon> · Train 8 · Test 4 scenarios · scorer environment <vN>"',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    // Seeding (one container exec) + a page load, a dialog and a lock round trip.
    test.setTimeout(300_000);
    const seed = seedHarnessEnvironment({
      organizationId: actor.organizationId, workspaceId: actor.workspaceId,
      refunds: REFUNDS, lookups: LOOKUPS,
    });
    const env = seed.environmentId;
    expect(seed.scenarioKeys).toHaveLength(SCORABLE);
    await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify(seed) });

    // Before: nothing locked, and the default pair is lockable with the split the lock would give.
    const before = await actor.api.get<Versions>(versions(env));
    expect(before.locked_versions).toEqual([]);
    const defaultLabel = (pieces: PieceVersion[], id: string) => {
      const found = pieces.find((one) => one.is_default);
      expect(found?.id).toBe(id);
      expect(found?.label).toMatch(/^v\d+$/);
      return found?.label as string;
    };
    const pair = pairOf(
      defaultLabel(before.environment, seed.environmentVersionId),
      defaultLabel(before.suite, seed.suiteVersionId),
    );
    expect(before).not.toHaveProperty('lock');
    const preview = await actor.api.get<Lock>(LOCK_PREVIEW(env));
    expect(preview).toMatchObject({
      environment_version_id: seed.environmentVersionId, suite_version_id: seed.suiteVersionId,
      can_lock: true, refusal: null, train: TRAIN_TASKS, test: TEST_TASKS, test_target: TEST_TASKS,
    });

    // The card sits on the environment's Overview; it has no test id, so it is found as the innermost
    // block holding the Reference passes table and the lock (or, once locked, unlock) button.
    const card = page.locator('div').filter({ hasText: 'Reference passes' })
      .filter({ has: page.getByRole('button', { name: /^(Lock versions|Unlock)$/ }) }).last();

    await test.step('open Overview: the version is ready to lock', async () => {
      await page.goto(`/dashboard/simulate/environments/${env}?tab=overview`);
      await expect(card.getByText(`Lock ${pair}`, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(card.getByText('Ready', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(card.getByRole('button', { name: LOCK_BUTTON })).toBeEnabled();
      await expect(card.getByText('Locked', { exact: true })).toHaveCount(0);
    });

    // Check 2 records from here: every mutating request the lock makes.
    const sent: { method: string; path: string; body: unknown }[] = [];
    page.on('request', (request) => {
      if (request.method() !== 'GET' && request.url().includes('/simulate/')) {
        sent.push({ method: request.method(), path: new URL(request.url()).pathname,
          body: request.postDataJSON() });
      }
    });

    await test.step('lock the version', async () => {
      await card.getByRole('button', { name: LOCK_BUTTON }).click({ timeout: UI_READY });
      const dialog = page.getByRole('dialog').filter({ hasText: `Lock ${pair}?` });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      const answered = page.waitForResponse((r) => r.url().endsWith(LOCK(env))
        && r.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: LOCK_ACTION, exact: true }).click();
      expect((await answered).status()).toBe(200);
    });

    // Check 2.
    expect(sent.map((s) => `${s.method} ${s.path}`)).toEqual([`POST ${LOCK(env)}`]);
    expect(sent[0].body).toEqual({
      environment_version_id: seed.environmentVersionId, suite_version_id: seed.suiteVersionId,
    });

    // Check 3: one locked version, on the default pair.
    const lockedVersions = await probe.pg<{ id: string; label: string; environment_version_id: string;
      suite_version_id: string; unlocked_at: Date | null }>(
      `SELECT id::text, label, environment_version_id::text, suite_version_id::text, unlocked_at
         FROM simulate_harness_locked_version WHERE environment_id = $1`, [env]);
    expect(lockedVersions).toHaveLength(1);
    const [lockedVersion] = lockedVersions;
    expect(lockedVersion).toMatchObject({
      environment_version_id: seed.environmentVersionId, suite_version_id: seed.suiteVersionId,
      unlocked_at: null,
    });
    expect(lockedVersion.label).toMatch(LABEL);
    await testInfo.attach('locked-version', { contentType: 'application/json',
      body: JSON.stringify({ lockedVersionId: lockedVersion.id, label: lockedVersion.label }) });

    // Check 4: every seeded scenario has its split, first given by this locked version, and the test rows
    // number what the formula gives.
    const splits = await probe.pg<{ scenario_key: string; split: string; first_locked_version_id: string }>(
      `SELECT h.scenario_key, s.split, s.first_locked_version_id::text
         FROM simulate_harness_task_split s
         JOIN simulate_hosted_harness_scenario h ON h.id = s.scenario_id
        WHERE h.job_id = $1`, [env]);
    expect(splits.map((one) => one.scenario_key).sort()).toEqual([...seed.scenarioKeys].sort());
    expect(new Set(splits.map((one) => one.first_locked_version_id))).toEqual(new Set([lockedVersion.id]));
    expect(splits.filter((one) => one.split === 'test')).toHaveLength(TEST_TASKS);
    expect(splits.filter((one) => one.split === 'train')).toHaveLength(TRAIN_TASKS);
    // The lock's roster records the same members and splits.
    const roster = await probe.pg<{ scenario_key: string; split: string }>(
      `SELECT h.scenario_key, m.split
         FROM simulate_harness_locked_version_member m
         JOIN simulate_hosted_harness_scenario h ON h.id = m.scenario_id
        WHERE m.locked_version_id = $1`, [lockedVersion.id]);
    const byKey = (rows: { scenario_key: string; split: string }[]) =>
      Object.fromEntries(rows.map((one) => [one.scenario_key, one.split]));
    expect(byKey(roster)).toEqual(byKey(splits));

    // Check 5: the read the card draws from.
    const after = await actor.api.get<Versions>(versions(env));
    expect(after.locked_versions).toHaveLength(1);
    expect(after.locked_versions[0]).toMatchObject({
      id: lockedVersion.id, label: lockedVersion.label, unlocked_at: null, train: TRAIN_TASKS, test: TEST_TASKS,
    });
    // The lock is on the default pair, whose environment version is its scorer.
    expect(pair.startsWith(`environment ${after.locked_versions[0].environment_label},`)).toBe(true);
    const previewAfter = await actor.api.get<Lock>(LOCK_PREVIEW(env));
    expect(previewAfter).toMatchObject({ can_lock: false, refusal: { code: 'version_already_locked' } });

    await test.step('read the locked card', async () => {
      await expect(card.getByText(`Locked: ${pair}`, { exact: true })).toBeVisible({ timeout: UI_READY });
      // The title says Locked: a locked card shows no status badge.
      await expect(card.getByText('Locked', { exact: true })).toHaveCount(0);
      await expect(card.getByText('Ready', { exact: true })).toHaveCount(0);
      await expect(card.getByText(new RegExp(
        `^Lock ${lockedVersion.label.replace(/\./g, '\\.')} · (?:\\d{1,2} [A-Z][a-z]{2}|[A-Z][a-z]{2} \\d{1,2}) · Train ${TRAIN_TASKS} · Test ${TEST_TASKS} scenarios · scorer environment ${after.locked_versions[0].environment_label}$`,
      ))).toBeVisible();
      await expect(card.getByRole('button', { name: LOCK_BUTTON })).toHaveCount(0);
    });

    // Check 6: a locked version holds the environment. The refusal's body is read as the
    // simulate/tests/test_harness_locked_versions.py delete test reads it: error and message.
    const refused = await actor.api.request<{ error: string; message: string }>('delete', `${ENVIRONMENTS}/${env}/`);
    expect(refused.status).toBe(409);
    expect(refused.body).toMatchObject({ error: 'version_locked', message: `Lock ${lockedVersion.label} covers this version. A workspace admin or owner must unlock it first.` });
    const still = await actor.api.get<Versions>(versions(env));
    expect(still.locked_versions.map((one) => one.id)).toEqual([lockedVersion.id]);
  },
);
