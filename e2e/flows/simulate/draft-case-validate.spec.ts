import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { proveCandidates, seedChatCase, seedHarnessEnvironment } from '../../lib/simulate-seed';

// Routes pinned from futureagi/simulate/views/harness_environment.py (drafts, drafts/validate, drafts/save,
// capabilities) and futureagi/simulate/views/harness_job.py (scenarios list).
const ENVIRONMENTS = '/simulate/api/harness-environments';
const JOBS = '/simulate/api/harness-jobs';
const UI_READY = 60_000;
// Copy pinned from frontend/src/sections/simulate/environments/workspace/scenarios/scenarios.constants.js
// (SCENARIOS_COPY.validateDrafts) and workspace.constants.js (DRAFTS_COPY.validation.ready, versionName, save).
const VALIDATE_DRAFTS = 'Validate with the unsaved changes';
const READY_NOTE = 'Validated. Name this version to save it.';
const VERSION_NAME = 'Name this version';

interface Candidates { environment_version_id: string; suite_version_id: string }
interface Drafts { validation: { state: string } | null; candidates: Candidates | null }
interface ScenarioRow { scenario_key: string; lifecycle: { state: string } | null }
interface ScenarioList { results: ScenarioRow[] }
interface Selection { selection: { running: number; held_back: { count: number } } }

test('SIM-E2E-012: a case the builder adds is validated with the unsaved changes, saved Active, and runs', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-012', area: 'simulate',
    userGoal: 'A builder validates a case the chat just added from its own drawer, saves the validated versions '
      + 'under a name, and the case is Active and runs',
    steps: ['seed a built, versioned Environment and a case the builder chat added to the suite draft in answer to a '
              + 'message (e2e/lib/simulate-seed.ts; no builder sandbox runs in this harness)',
            'open the Scenarios tab and the added case',
            'click Validate in the case drawer',
            'stand in for the validation sandbox: record every case proved on the candidate pairing as the gateway '
              + 'records a proofs file, and certify it (e2e/lib/harness_review_seed.py prove_candidates)',
            'name the version in the draft bar and Save',
            'read the Scenarios list and the run readiness for the added case'],
    backendChecks: ['the drawer Validate posts drafts/validate (202) and never scenarios/validate',
                    'the drafts read reports the candidate validation ready',
                    'the candidate pairing holds the added case proved',
                    'after Save the default suite is the candidate, its pairing holds the added case proved, '
                      + 'the list reads it Active and a run of it holds nothing back'],
    browserChecks: ['the added case reads as an unsaved change with a Validate that validates the unsaved changes',
                    'the draft bar reads "Validated. Name this version to save it." once the validation is ready'],
  }),
}, async ({ actor, page, probe }, testInfo) => {
  // Three docker exec seeds and two UI round trips.
  test.setTimeout(180_000);
  const environment = seedHarnessEnvironment({
    organizationId: actor.organizationId, workspaceId: actor.workspaceId, refunds: 1, lookups: 0,
  });
  const added = seedChatCase({ environmentId: environment.environmentId, userEmail: actor.email });
  await testInfo.attach('seed', { contentType: 'application/json', body: JSON.stringify({ environment, added }) });
  const base = `${ENVIRONMENTS}/${environment.environmentId}`;
  const proofOf = async (suiteVersionId: string, environmentVersionId: string) => (await probe.pg<{ proved: boolean | null }>(
    `SELECT (proofs->$3->>'proved')::boolean AS proved FROM simulate_harness_suite_validation
      WHERE suite_version_id = $1 AND environment_version_id = $2`,
    [suiteVersionId, environmentVersionId, added.scenarioKey],
  ))[0]?.proved ?? null;

  const savedValidations: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/scenarios/validate/')) savedValidations.push(request.url());
  });

  let candidates: Candidates | undefined;
  await test.step('ui: the added case validates the unsaved changes', async () => {
    await page.goto(`/dashboard/simulate/environments/${environment.environmentId}?tab=scenarios`);
    await page.getByText(added.name, { exact: true }).first().click({ timeout: UI_READY });
    const validate = page.getByTestId('lifecycle-validate-drafts');
    await expect(validate.getByRole('button', { name: VALIDATE_DRAFTS })).toBeEnabled({ timeout: UI_READY });
    const [response] = await Promise.all([
      page.waitForResponse((one) => one.url().includes(`${base}/drafts/validate/`), { timeout: UI_READY }),
      validate.getByRole('button', { name: VALIDATE_DRAFTS }).click(),
    ]);
    const body = await response.json() as { candidates: Candidates; result?: { candidates: Candidates } };
    candidates = body.result?.candidates ?? body.candidates;
    await testInfo.attach('validate', { contentType: 'application/json', body: JSON.stringify(body) });
    expect(response.status()).toBe(202);
    expect(savedValidations).toEqual([]);
  }, { timeout: UI_READY * 2 });

  proveCandidates({ environmentId: environment.environmentId });

  await test.step('api + pg: the candidate validation is ready and holds the added case proved', async () => {
    const drafts = await actor.api.get<Drafts>(`${base}/drafts/`);
    expect(drafts.validation?.state).toBe('ready');
    expect(drafts.candidates).toEqual(candidates);
    expect(await proofOf(candidates!.suite_version_id, candidates!.environment_version_id)).toBe(true);
  });

  const name = `Partial refunds ${environment.environmentId.slice(0, 8)}`;
  await test.step('ui: the draft bar saves the validated versions under a name', async () => {
    const bar = page.getByTestId('draft-bar');
    await expect(bar.getByRole('status')).toHaveText(READY_NOTE, { timeout: UI_READY });
    await bar.getByRole('textbox', { name: VERSION_NAME }).fill(name);
    const [response] = await Promise.all([
      page.waitForResponse((one) => one.url().includes(`${base}/drafts/save/`), { timeout: UI_READY }),
      bar.getByRole('button', { name: 'Save' }).click(),
    ]);
    expect(response.status()).toBe(200);
  }, { timeout: UI_READY * 2 });

  await test.step('pg + api: the saved default holds the case proved, Active, and a run of it holds nothing back', async () => {
    const [job] = await probe.pg<{ suite_version_id: string; environment_version_id: string }>(
      'SELECT suite_version_id::text, environment_version_id::text FROM simulate_hosted_harness_job WHERE id = $1',
      [environment.environmentId],
    );
    expect(job.suite_version_id).toBe(candidates!.suite_version_id);
    expect(await proofOf(job.suite_version_id, job.environment_version_id)).toBe(true);
    const list = await actor.api.get<ScenarioList>(`${JOBS}/${environment.environmentId}/scenarios/`);
    expect(list.results.find((row) => row.scenario_key === added.scenarioKey)?.lifecycle?.state).toBe('active');
    const readiness = await actor.api.post<Selection>(`${base}/capabilities/`, { scenario_ids: [added.scenarioKey] });
    expect([readiness.selection.running, readiness.selection.held_back.count]).toEqual([1, 0]);
  });
});
