import { createHash, randomUUID } from 'node:crypto';
import type { TestInfo } from '@playwright/test';
import { expect } from '../../lib/fixtures';
import type { TestActor } from '../../lib/provisioning';
import type { StateProbe } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// ExperimentCreateV2Serializer, ExternalRunCreateRequestSerializer and urls.py.
export const EXPERIMENTS = '/model-hub/experiments/v2/';
export const RUNS = '/model-hub/experiment-runs/';
// Native prompt execution and its evaluation can finish sequentially (POLL: 60 + 90 seconds).
export const NATIVE_TERMINAL = 150_000;
type Metric = { metric_id: string; evaluator_version_id: string };
type Result<T> = { result: T };

export async function seedCIComparison(actor: TestActor, probe: StateProbe, info: TestInfo) {
  const prefix = `e2e-ci-comment-${info.workerIndex}-${Date.now().toString(36)}`;
  const attach = (name: string, ids: unknown) => info.attach(name, {
    contentType: 'application/json', body: JSON.stringify(ids),
  });
  // CustomAIModelCreateRequestSerializer: only the owned scoped OpenAI-compatible mock.
  // Do not use registerMockModel: its stack inspection reads deployment configuration.
  const modelName = `${prefix}-model`;
  const model = await actor.api.post<Result<{ data: { id: string } }>>('/model-hub/custom_models/create/', {
    model_provider: 'openai', model_name: modelName, input_token_cost: 0, output_token_cost: 0,
    config_json: { key: 'e2e-ci-comment-placeholder', api_base: `${new MockUpstream().baseUrl(prefix)}/v1` },
  });
  await attach('model-id', { modelId: model.result.data.id });
  // ManualDatasetCreateRequestSerializer; one column and four empty rows.
  const dataset = await actor.api.post<Result<{ dataset_id: string }>>('/model-hub/develops/create-dataset-manually/', {
    dataset_name: `${prefix}-dataset`, number_of_rows: 4, number_of_columns: 1,
  });
  const datasetId = dataset.result.dataset_id;
  await attach('dataset-id', { datasetId });
  const rows = await probe.pg<{ id: string }>(
    'SELECT id FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted ORDER BY "order", id', [datasetId]);
  const columns = await probe.pg<{ id: string }>(
    'SELECT id FROM model_hub_column WHERE dataset_id=$1 AND NOT deleted', [datasetId]);
  expect(rows).toHaveLength(4);
  expect(columns).toHaveLength(1);
  for (const [i, row] of rows.entries()) {
    // DatasetUpdateCellValueRequestSerializer.
    await actor.api.post(`/model-hub/develops/${datasetId}/update_cell_value/`, {
      row_id: row.id, column_id: columns[0].id, new_value: `${prefix}-input-${i}`,
    });
  }
  // EvalTemplateCreateV2RequestSerializer: the echo mock leaves parseable verdict JSON.
  const evaluation = await actor.api.post<Result<{ id: string }>>('/model-hub/eval-templates/create-v2/', {
    name: `${prefix}-seed-eval`, eval_type: 'llm', model: modelName, output_type: 'pass_fail',
    instructions: 'Reply with exactly this JSON: {"result":"Pass","explanation":"saw {{output}}"}',
    pass_threshold: 0.5, check_internet: false, error_localizer_enabled: false,
  });
  // PromptTemplateViewSet.create_draft; same saved-version wire as EXP-E2E-007.
  const prompt = await actor.api.post<Result<{ id: string }>>('/model-hub/prompt-templates/create-draft/', {
    name: `${prefix}-prompt`, is_draft: false, variable_names: {}, prompt_config: [{
      messages: [{ role: 'user', content: [{ type: 'text', text: `${prefix}-native` }] }],
      configuration: { model: modelName, model_detail: { value: modelName }, temperature: 0, max_tokens: 32 },
    }],
  });
  const versions = await probe.pg<{ id: string }>(
    `SELECT id FROM model_hub_promptversion WHERE original_template_id=$1
     AND template_version='v1' AND NOT deleted`, [prompt.result.id]);
  expect(versions).toHaveLength(1);
  await attach('prompt-and-evaluation-ids', {
    promptId: prompt.result.id, promptVersionId: versions[0].id, evaluationId: evaluation.result.id,
  });
  await actor.api.post(EXPERIMENTS, {
    name: `${prefix}-experiment`, dataset_id: datasetId, experiment_type: 'llm',
    prompt_config: [{ name: `${prefix}-native`, prompt_id: prompt.result.id, prompt_version: versions[0].id,
      model: modelName, model_params: { temperature: 0, max_tokens: 32 } }],
    user_eval_metrics: [{ template_id: evaluation.result.id, name: `${prefix}-seed-metric`,
      model: modelName, config: { mapping: { output: columns[0].id } } }],
  });
  const experiments = await probe.pg<{ id: string }>(
    'SELECT id FROM model_hub_experimentstable WHERE dataset_id=$1 AND name=$2 AND NOT deleted',
    [datasetId, `${prefix}-experiment`]);
  expect(experiments).toHaveLength(1);
  const experimentId = experiments[0].id;
  await attach('experiment-id', { experimentId });
  // Seeding only: native inference is not the claim of this custom-upload flow.
  await expect.poll(async () => (await probe.pg<{ status: string }>(
    'SELECT status FROM model_hub_experimentstable WHERE id=$1 AND NOT deleted', [experimentId]))[0]?.status,
  { timeout: NATIVE_TERMINAL, intervals: [1_000, 2_000, 5_000] }).toMatch(/^(Completed|Failed|Error|Cancelled)$/);

  const metrics: Metric[] = [];
  for (const name of ['a', 'b']) {
    // ExternalMetricCreateRequestSerializer: identical numeric direction, independently pinned evaluators.
    const metric = await actor.api.post<Result<Metric>>(`/model-hub/datasets/${datasetId}/external-metrics/`, {
      name: `${prefix}-${name}`, output_type: 'numeric', higher_is_better: true,
      source_hash: createHash('sha256').update(`${prefix}-${name}-scorer`).digest('hex'),
    });
    metrics.push(metric.result);
  }
  const examples = rows.map(() => randomUUID());
  // DatasetVersionCreateSerializer: immutable examples linked to the API-created rows.
  const version = await actor.api.post<Result<{ id: string }>>(`/model-hub/datasets/${datasetId}/versions/`, {
    description: `${prefix}-comparison`, items: rows.map((row, i) => ({
      legacy_row_id: row.id, example_id: examples[i], input: { text: `${prefix}-input-${i}` },
    })),
  });
  const baselineId = randomUUID();
  const candidateId = randomUUID();
  for (const runId of [baselineId, candidateId]) {
    const run = await actor.api.post<Result<{ id: string; experiment_id: string; evaluator_bindings: Metric[] }>>(RUNS, {
      run_id: runId, dataset_version_id: version.result.id, experiment_id: experimentId,
      evaluator_metric_ids: metrics.map(m => m.metric_id),
      expected_items: examples.map(example_id => ({ example_id, trial_number: 1 })),
    });
    expect(run.result.id).toBe(runId);
    expect(run.result.experiment_id).toBe(experimentId);
    expect(run.result.evaluator_bindings.map(({ metric_id, evaluator_version_id }) =>
      ({ metric_id, evaluator_version_id })).sort((a, b) => a.metric_id.localeCompare(b.metric_id)))
      .toEqual(metrics.map(({ metric_id, evaluator_version_id }) => ({ metric_id, evaluator_version_id }))
        .sort((a, b) => a.metric_id.localeCompare(b.metric_id)));
  }
  await attach('comparison-ids', { datasetId, experimentId, datasetVersionId: version.result.id,
    baselineId, candidateId, exampleIds: examples, rowIds: rows.map(r => r.id),
    metrics: metrics.map(({ metric_id, evaluator_version_id }) => ({ metric_id, evaluator_version_id })) });
  // ExternalRunResultsRequestSerializer. Each metric contributes one of every comparison state.
  const items = (run: 'baseline' | 'candidate') => examples.map((example_id, i) => {
    const value = (run === 'baseline' ? [0, 1, 0.5, null] : [1, 0, 0.5, 1])[i];
    return { example_id, trial_number: 1, receipt_key: `${prefix}-${run}-${i}`, status: 'completed',
      output: `${prefix}-${run}-${i}`, score_refs: metrics.map(m => ({
        metric_id: m.metric_id, evaluator_version_id: m.evaluator_version_id,
        status: value === null ? 'not_assessed' : 'assessed', value,
      })) };
  });
  await actor.api.post(`${RUNS}${baselineId}/results/`, { items: items('baseline') });
  return { datasetId, experimentId, versionId: version.result.id, baselineId, candidateId,
    examples, metrics, candidateItems: items('candidate') };
}

// Independent literal oracle pinned from RunSources/snippets/ciComment.js.
// These are inert production commands: never run curl/gh or relax the HTTPS guard.
export function expectedSnippet(experiment: string, baseline: string): string {
  return [
    '# Add after evaluation/upload in a pull_request job on ubuntu-latest.',
    '# The Your code step id is fi_evaluation; it outputs run_id after success.',
    '# Job permissions: contents: read, pull-requests: write.',
    '# FI_API_URL is the API base URL; FI_APP_URL is the app origin.',
    '- name: Comment on evaluation results against the baseline',
    "  if: ${{ success() && github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name == github.repository }}",
    '  env:',
    '    FI_RUN_ID: ${{ steps.fi_evaluation.outputs.run_id }}',
    '    FI_EVALUATION_OUTCOME: ${{ steps.fi_evaluation.outcome }}',
    '    FI_API_URL: ${{ vars.FI_API_URL }}',
    '    FI_APP_URL: ${{ vars.FI_APP_URL }}',
    '    FI_API_KEY: ${{ secrets.FI_API_KEY }}',
    '    FI_SECRET_KEY: ${{ secrets.FI_SECRET_KEY }}',
    '    GH_TOKEN: ${{ github.token }}',
    '    GH_REPO: ${{ github.repository }}',
    '    PR_NUMBER: ${{ github.event.pull_request.number }}',
    '  shell: bash',
    '  run: |',
    '    set -euo pipefail',
    '    if [[ "${FI_EVALUATION_OUTCOME:-}" != "success" ]]; then',
    '      echo "Evaluation/upload did not succeed; no comment posted." >&2',
    '      exit 1',
    '    fi',
    '    : "${FI_RUN_ID:?Evaluation/upload must output run_id for this execution}"',
    '    if [[ ! "$FI_RUN_ID" =~ ^[[:xdigit:]]{8}(-[[:xdigit:]]{4}){3}-[[:xdigit:]]{12}$ ]]; then',
    '      echo "Evaluation/upload returned an invalid run ID." >&2',
    '      exit 1',
    '    fi',
    '    shopt -s nocasematch',
    `    if [[ "$FI_RUN_ID" == "${baseline}" ]]; then`,
    '      echo "Candidate must differ from the baseline run." >&2',
    '      exit 1',
    '    fi',
    '    : "${FI_API_URL:?Set repository variable FI_API_URL}"',
    '    : "${FI_APP_URL:?Set repository variable FI_APP_URL}"',
    '    : "${FI_API_KEY:?Set repository secret FI_API_KEY}"',
    '    : "${FI_SECRET_KEY:?Set repository secret FI_SECRET_KEY}"',
    '    response="$(mktemp)"',
    '    body="$(mktemp)"',
    '    trap \'rm -f "$response" "$body"\' EXIT',
    "    curl --fail --silent --show-error --max-time 60 --proto '=https' --get \\",
    `      "\${FI_API_URL%/}/model-hub/experiments/v2/${experiment}/ci-summary/comment/" \\`,
    '      -H "X-Api-Key: $FI_API_KEY" \\',
    '      -H "X-Secret-Key: $FI_SECRET_KEY" \\',
    '      --data-urlencode "run_id=$FI_RUN_ID" \\',
    `      --data-urlencode "baseline_run_id=${baseline}" \\`,
    '      --data-urlencode "max_regressed=1" \\',
    '      --data-urlencode "top_n=1" \\',
    '      --output "$response"',
    '    jq -er --arg app "${FI_APP_URL%/}" \'',
    '      . as $summary | .body | split("](" + $summary.results_path + ")")',
    '      | join("](" + $app + $summary.results_path + ")")',
    '    \' "$response" > "$body"',
    '    gh pr comment "$PR_NUMBER" --body-file "$body"',
    '    jq -e \'.passed == true\' "$response" > /dev/null',
    '',
  ].join('\n');
}
