import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';
import {
  attachIds, bindingProjection, scoreProjection, seedSummaryExperiment, storedBindings, storedScores,
  type SummaryBinding, type SummaryScore,
} from './summary-evaluators-fixture';

// README Writing a flow: per-action first paint budget; each UI step is explicitly bounded.
const UI_READY = 60_000;
// API seed allowance (not a CDC wait); cleanup matches MockUpstream.clearReplies' 10s deadline.
const SETUP = 180_000;
const CLEANUP = 10_000;
// model_hub/views/experiment_summary.py and frontend utils/axios.js.
const bindingsPath = (experimentId: string) => `/model-hub/experiments/v2/${experimentId}/summary-evaluators/`;
const scoresPath = (experimentId: string, runId: string) =>
  `/model-hub/experiments/v2/${experimentId}/runs/${runId}/summary-scores/`;
// CustomPromptEvaluator parses the registered numeric result directly; percentage is a unit score.
const EXPECTED_SCORE = 0.75;
// SummaryEvaluators.jsx submit; experiment_summary._population passes the complete run population.
const MAPPING = { rows: 'input.rows' };

type Envelope<T> = { status: boolean; result: T };
type Run = { id: string; status: string; completed_at: string | null; dataset_version_id: string;
  prompt_version_id: string; organization_id: string; workspace_id: string;
  metadata: { _futureagi_run_write: { plan: { identities: { example_id: string; trial_number: number }[] } } } };
type Item = { id: string; example_id: string; trial_number: number; row_id: string; status: string;
  input: { values: Record<string, string> }; output: string; error: unknown };

test('EXP-E2E-011: create a summary evaluator and see its sealed run result', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EXP-E2E-011', area: 'experiments',
    userGoal: 'Create a version-pinned experiment summary evaluator, run it on a sealed dataset run, and see the saved aggregate result',
    steps: [
      'API seed two marked dataset rows with weights 1 and 0.5, a scoped mock prompt, a saved prompt version, an LLM row evaluator and an experiment',
      'wait for the canonical run to seal with both planned rows completed and exact prompt outputs',
      'open Summary and add a named evaluator by searching the saved LLM template, pinning v1 and mapping rows to input.rows',
      'save the binding and click Evaluate in its run score row',
      'read the exact score 0.75 and minted reason, reload Summary and read both again',
      'read back the exact binding and score through the same APIs and compare their identities and timestamps with PostgreSQL',
    ],
    backendChecks: [
      'Storage lane, ASYNC_JOB 60s for native run sealing: one scoped completed run has completed_at and exactly both planned items with the seeded inputs and exact mock outputs',
      'Storage lane, within save UI_READY 60s after the binding response: the binding pins the selected template and version, rows mapping and actor organization/workspace',
      'Storage lane, EVAL_RESULT 90s for summary execution: exactly one completed result has score 0.75, the minted reason, metadata.usage 7 prompt / 7 completion / 14 total tokens, attempt 1 and no error code',
      'API lane, ASYNC_JOB 60s readback allowance after reload: summary evaluator and score lists equal the PostgreSQL projections including pinned IDs and microsecond timestamps',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // 180s API seed + 60s run sealing + nine 60s UI steps + 90s summary completion
  // + 60s API/storage readback + 10s mock cleanup = 940s. No unbudgeted chained polls.
  test.setTimeout(SETUP + POLL.ASYNC_JOB.timeout + 9 * UI_READY + POLL.EVAL_RESULT.timeout
    + POLL.ASYNC_JOB.timeout + CLEANUP);
  const prefix = `e2e-summary-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const mock = new MockUpstream();
  // Safety veto only; do not fulfill requests or replace any product response.
  for (const endpoint of [E2E.appUrl, E2E.apiUrl, E2E.mockLlmUrl, E2E.pgUrl]) {
    expect(new URL(endpoint).hostname, 'this draft is for the local walk stack only').toBe('localhost');
  }
  await page.context().route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
    ? route.continue() : route.abort('blockedbyclient'));
  page.setDefaultTimeout(UI_READY);
  const ui = expect.configure({ timeout: UI_READY });

  try {
    const seeded = await test.step('API: seed a dataset experiment and saved summary template',
      () => seedSummaryExperiment(actor, probe, mock, prefix, testInfo), { timeout: SETUP });
    const run = await test.step('Storage: wait for the complete sealed run population', async () => {
      const runs = () => probe.pg<Run>(
        `SELECT r.id, r.status, r.completed_at::text, r.dataset_version_id, r.prompt_version_id, r.metadata,
         d.organization_id, d.workspace_id FROM model_hub_experimentrun r
         JOIN model_hub_dataset_version v ON v.id=r.dataset_version_id
         JOIN model_hub_dataset d ON d.id=v.dataset_id
         WHERE r.experiment_id=$1 AND v.dataset_id=$2 AND NOT r.deleted AND NOT v.deleted AND NOT d.deleted
         ORDER BY r.id`, [seeded.experimentId, seeded.datasetId]);
      // Poll the state, not a count or a permissive terminal-status set. Failed is never a sealed success.
      await expect.poll(async () => (await runs()).map(run => ({ status: run.status, sealed: run.completed_at !== null })),
        { ...POLL.ASYNC_JOB, message: 'the native dataset run completes and seals before adding a summary binding' })
        .toEqual([{ status: 'completed', sealed: true }]);
      const [run] = await runs();
      await attachIds(testInfo, 'summary-sealed-run', { runId: run.id, datasetVersionId: run.dataset_version_id,
        status: run.status, completedAt: run.completed_at });
      expect({ prompt: run.prompt_version_id, org: run.organization_id, workspace: run.workspace_id }).toEqual({
        prompt: seeded.promptVersionId, org: actor.organizationId, workspace: actor.workspaceId,
      });
      // models/dataset_versions.py and native_run_binding.py preserve the source-row/example identity.
      const items = await probe.pg<Item>(
        `SELECT i.id, i.example_id, i.trial_number, v.legacy_row_id AS row_id, i.status, i.input, i.output, i.error
         FROM model_hub_experimentrunitem i JOIN model_hub_dataset_version_item v
           ON v.dataset_version_id=$2 AND v.example_id=i.example_id AND NOT v.deleted
         WHERE i.run_id=$1 AND NOT i.deleted ORDER BY i.example_id, i.trial_number`, [run.id, run.dataset_version_id]);
      expect(items.map(item => ({ example_id: item.example_id, trial_number: item.trial_number })))
        .toEqual([...run.metadata._futureagi_run_write.plan.identities]
          .sort((a, b) => a.example_id.localeCompare(b.example_id) || a.trial_number - b.trial_number));
      expect(items.map(item => item.row_id).sort()).toEqual([...seeded.rowIds].sort());
      expect(items.map(item => ({ row_id: item.row_id, trial: item.trial_number, status: item.status,
        input: item.input, output: item.output, error: item.error })).sort((a, b) => a.row_id.localeCompare(b.row_id)))
        .toEqual(seeded.rowIds.map((rowId, index) => ({ row_id: rowId, trial: 1, status: 'completed',
          input: { values: { [seeded.markerColumnId]: seeded.markers[index], [seeded.weightColumnId]: ['1', '0.5'][index] } },
          output: seeded.output, error: null })).sort((a, b) => a.row_id.localeCompare(b.row_id)));
      await attachIds(testInfo, 'summary-sealed-items', items);
      return run;
    }, { timeout: POLL.ASYNC_JOB.timeout });

    const bindingUrl = bindingsPath(seeded.experimentId);
    const scoreUrl = scoresPath(seeded.experimentId, run.id);
    // ExperimentSummaryView.jsx owns this route; no launch flags or launch-line wording assumptions.
    const summaryUrl = `/dashboard/develop/experiment/${seeded.experimentId}/summary`;
    await test.step('UI: open the experiment Summary', async () => {
      await page.goto(summaryUrl, { waitUntil: 'domcontentloaded', timeout: UI_READY });
      await ui(page.getByRole('tab', { name: 'Summary', exact: true })).toHaveAttribute('aria-selected', 'true');
      await ui(page.getByRole('button', { name: 'Add summary evaluator', exact: true })).toBeVisible();
    }, { timeout: UI_READY });
    await test.step('UI: add and name the summary evaluator', async () => {
      await page.getByRole('button', { name: 'Add summary evaluator', exact: true }).click();
      await page.getByRole('textbox', { name: 'Summary name', exact: true }).fill(seeded.summaryName);
      await ui(page.getByRole('textbox', { name: 'Summary name', exact: true })).toHaveValue(seeded.summaryName);
    }, { timeout: UI_READY });
    await test.step('UI: search and select the saved LLM evaluator', async () => {
      await page.getByRole('textbox', { name: 'Search evaluators', exact: true }).fill(seeded.templateName);
      await page.getByRole('combobox', { name: 'Evaluator', exact: true }).click();
      await page.getByRole('option', { name: seeded.templateName, exact: true }).click();
      // MUI Select includes its selected display value in aria-labelledby.
      await ui(page.getByRole('combobox', { name: `Evaluator ${seeded.templateName}`, exact: true }))
        .toHaveText(seeded.templateName);
    }, { timeout: UI_READY });
    await test.step('UI: pin the saved first version', async () => {
      await page.getByRole('combobox', { name: 'Pinned version', exact: true }).click();
      await page.getByRole('option', { name: 'v1', exact: true }).click();
      await ui(page.getByRole('combobox', { name: 'Pinned version v1', exact: true })).toHaveText('v1');
    }, { timeout: UI_READY });
    await test.step('UI: map the complete run rows', async () => {
      const mapping = page.getByRole('textbox', { name: 'Input mapping (JSON)', exact: true });
      await mapping.fill(JSON.stringify(MAPPING));
      await ui(mapping).toHaveValue('{"rows":"input.rows"}');
    }, { timeout: UI_READY });

    // SummaryEvaluators.jsx: table label and row name are semantic, and scoped to this minted binding.
    const table = page.getByRole('table', { name: 'Run summary scores', exact: true });
    const row = table.getByRole('row').filter({ has: page.getByRole('cell', {
      name: `${seeded.summaryName} · v1`, exact: true,
    }) });
    const binding = await test.step('UI and storage: save the pinned summary binding', async () => {
      const pending = page.waitForResponse(r => new URL(r.url()).pathname === bindingUrl
        && r.request().method() === 'POST', { timeout: UI_READY });
      const [response] = await Promise.all([pending,
        page.getByRole('button', { name: 'Save summary evaluator', exact: true }).click()]);
      expect(response.status()).toBe(201);
      expect(response.request().postDataJSON()).toEqual({ name: seeded.summaryName,
        template_id: seeded.templateId, version_id: seeded.versionId, mapping: MAPPING });
      const body = await response.json() as Envelope<SummaryBinding>;
      expect(body.status).toBe(true);
      await attachIds(testInfo, 'summary-binding', { bindingId: body.result.id, runId: run.id });
      const stored = await storedBindings(probe, seeded.experimentId);
      expect(stored).toHaveLength(1);
      const { experiment_id, definition, ...projection } = stored[0];
      expect(experiment_id).toBe(seeded.experimentId);
      expect({ name: projection.name, template: projection.template_id, version: projection.version_id,
        versionNumber: projection.version_number, mapping: projection.mapping }).toEqual({
        name: seeded.summaryName, template: seeded.templateId, version: seeded.versionId,
        versionNumber: 1, mapping: MAPPING,
      });
      expect({ mapping: definition.mapping, org: definition.organization_id, workspace: definition.workspace_id,
        template: definition.pin.template_id, version: definition.pin.evaluator_version_id }).toEqual({
        mapping: MAPPING, org: actor.organizationId, workspace: actor.workspaceId,
        template: seeded.templateId, version: seeded.versionId,
      });
      expect({ eval_type: definition.template.eval_type, model: definition.template.model,
        eval_type_id: definition.template.config.eval_type_id, output: definition.template.config.output,
        required_keys: definition.template.config.required_keys, rule_prompt: definition.template.config.rule_prompt })
        .toEqual({ eval_type: 'llm', model: seeded.summaryModel, eval_type_id: 'CustomPromptEvaluator',
          output: 'score', required_keys: ['rows'], rule_prompt: seeded.instructions });
      expect(bindingProjection(body.result)).toEqual(projection);
      await ui(table.getByRole('columnheader')).toHaveText(['Evaluator', `Run ${run.id}`]);
      await ui(row).toHaveCount(1);
      await ui(row.getByRole('button', { name: 'Evaluate', exact: true })).toBeEnabled();
      return projection;
    }, { timeout: UI_READY });

    const resultId = await test.step('UI: evaluate this binding on the sealed run', async () => {
      const pending = page.waitForResponse(r => new URL(r.url()).pathname === scoreUrl
        && r.request().method() === 'POST', { timeout: UI_READY });
      const [response] = await Promise.all([pending, row.getByRole('button', { name: 'Evaluate', exact: true }).click()]);
      expect(response.status()).toBe(202);
      expect(response.request().postDataJSON()).toEqual({ binding_id: binding.id, retry_failed: false });
      const body = await response.json() as Envelope<SummaryScore>;
      expect(body.status).toBe(true);
      expect({ run: body.result.run_id, binding: body.result.binding_id, version: body.result.version_id })
        .toEqual({ run: run.id, binding: binding.id, version: seeded.versionId });
      await attachIds(testInfo, 'summary-requested-result', { resultId: body.result.id, bindingId: binding.id, runId: run.id });
      return body.result.id;
    }, { timeout: UI_READY });

    const storedScore = await test.step('Storage: await one exact completed summary result', async () => {
      // LLM metadata includes a variable response_time; only the mock usage is deterministic.
      try {
        await expect.poll(async () => (await storedScores(probe, run.id)).map(score => ({ status: score.status,
          score: score.score, reason: score.reason, usage: score.metadata.usage, error: score.error_code,
          attempt: score.attempt, sealed: score.completed_at !== null })),
        { ...POLL.EVAL_RESULT, message: 'one completed whole-run summary, with no failed or repeated attempt' })
          .toEqual([{ status: 'completed', score: EXPECTED_SCORE, reason: seeded.reason,
            usage: { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 }, error: '', attempt: 1, sealed: true }]);
      } catch (error) {
        // Value-free diagnosis for the report: lifecycle fields only, never scores, reasons or metadata.
        const rows = (await storedScores(probe, run.id)).map(score => ({ status: score.status,
          error_code: score.error_code, attempt: score.attempt, sealed: score.completed_at !== null }));
        await testInfo.attach('diag-summary-poll', { contentType: 'application/json', body: JSON.stringify(rows) });
        throw error;
      }
      const [storedScore] = await storedScores(probe, run.id);
      expect({ id: storedScore.id, run: storedScore.run_id, binding: storedScore.binding_id,
        version: storedScore.version_id, versionNumber: storedScore.version_number, name: storedScore.name })
        .toEqual({ id: resultId, run: run.id, binding: binding.id, version: seeded.versionId,
          versionNumber: 1, name: seeded.summaryName });
      // Normalization validates all clocks, including the non-null execution start and completion.
      expect(scoreProjection(storedScore)).toEqual(storedScore);
      await attachIds(testInfo, 'summary-stored-result', storedScore);
      return storedScore;
    }, { timeout: POLL.EVAL_RESULT.timeout });

    await test.step('UI: show the exact aggregate and explanation', async () => {
      // No score hook exists. Exact cell text: score, reason, then the Metadata disclosure the cell
      // renders for non-empty metadata (SummaryEvaluators.jsx), taken from the stored row so
      // nondeterministic fields such as response_time match exactly; no error or retry text.
      await ui(row.getByRole('cell')).toHaveText([`${seeded.summaryName} · v1`,
        `${EXPECTED_SCORE}${seeded.reason}Metadata${JSON.stringify(storedScore.metadata, null, 2)}`]);
    }, { timeout: UI_READY });
    await test.step('UI: reload and retain the exact saved result', async () => {
      await page.reload({ waitUntil: 'domcontentloaded', timeout: UI_READY });
      await ui(row.getByRole('cell')).toHaveText([`${seeded.summaryName} · v1`,
        `${EXPECTED_SCORE}${seeded.reason}Metadata${JSON.stringify(storedScore.metadata, null, 2)}`]);
    }, { timeout: UI_READY });

    await test.step('API: binding and score readback agree exactly with PostgreSQL', async () => {
      const [bindingList, scoreList] = await Promise.all([
        actor.api.get<Envelope<{ bindings: SummaryBinding[] }>>(bindingUrl),
        actor.api.get<Envelope<{ scores: SummaryScore[] }>>(scoreUrl),
      ]);
      expect(bindingList.status).toBe(true);
      expect(scoreList.status).toBe(true);
      expect(bindingList.result.bindings.map(bindingProjection)).toEqual([binding]);
      expect(scoreList.result.scores.map(scoreProjection)).toEqual([storedScore]);
      // Reload must not create a second result or increment the execution attempt.
      expect(await storedScores(probe, run.id)).toEqual([storedScore]);
      await attachIds(testInfo, 'summary-api-readback', { binding: bindingList.result, scores: scoreList.result });
    }, { timeout: POLL.ASYNC_JOB.timeout });
  } finally {
    // Only this minted mock scenario is removed; retain the product rows for failure diagnosis.
    await mock.clearReplies(prefix);
  }
});
