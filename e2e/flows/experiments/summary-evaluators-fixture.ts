import type { TestInfo } from '@playwright/test';
import { expect } from '../../lib/fixtures';
import type { TestActor } from '../../lib/provisioning';
import type { StateProbe } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// Wire contracts: ManualDatasetCreateRequestSerializer, DatasetUpdateCellValueRequestSerializer,
// EvalTemplateCreateV2RequestSerializer (model_hub/serializers/contracts.py),
// PromptTemplateViewSet.create_draft and ExperimentCreateV2Serializer.
const DATASETS = '/model-hub/develops/create-dataset-manually/';
const TEMPLATES = '/model-hub/eval-templates/create-v2/';
const PROMPTS = '/model-hub/prompt-templates/create-draft/';
const EXPERIMENTS = '/model-hub/experiments/v2/';
type Envelope<T> = { result: T };

export const attachIds = (info: TestInfo, name: string, value: unknown) =>
  info.attach(name, { contentType: 'application/json', body: JSON.stringify(value, null, 2) });

// API-only seed. No SQL writes, fabricated terminal states, launch overrides, or deployment changes.
// Prerequisite: the walk stack must already support Python code evaluations (executor or its
// existing local fallback); this fixture intentionally does not enable either implementation.
export async function seedSummaryExperiment(actor: TestActor, probe: StateProbe, mock: MockUpstream,
  prefix: string, info: TestInfo) {
  const output = `${prefix}-completed-output`;
  const reason = `${prefix}-mean-of-two-sealed-rows`;
  const markers = [`${prefix}-alpha`, `${prefix}-beta`];
  const templateName = `${prefix}-mean`;
  const summaryName = `${prefix}-summary`;
  const dataset = await actor.api.post<Envelope<{ dataset_id: string }>>(DATASETS,
    { dataset_name: `${prefix}-dataset`, number_of_rows: 2, number_of_columns: 2 });
  const datasetId = dataset.result.dataset_id;
  await attachIds(info, 'summary-dataset', { datasetId, organizationId: actor.organizationId,
    workspaceId: actor.workspaceId, markers });
  const columns = await probe.pg<{ id: string; name: string }>(
    'SELECT id, name FROM model_hub_column WHERE dataset_id=$1 AND NOT deleted ORDER BY name', [datasetId]);
  expect(columns.map(c => c.name)).toEqual(['Column 1', 'Column 2']);
  const [markerColumn, weightColumn] = columns;
  const rows = await probe.pg<{ id: string }>(
    'SELECT id FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted ORDER BY "order", id', [datasetId]);
  expect(rows).toHaveLength(2);
  for (const [index, row] of rows.entries()) {
    for (const [columnId, value] of [[markerColumn.id, markers[index]], [weightColumn.id, ['1', '0.5'][index]]]) {
      await actor.api.post(`/model-hub/develops/${datasetId}/update_cell_value/`,
        { row_id: row.id, column_id: columnId, new_value: value });
    }
  }
  await attachIds(info, 'summary-source-rows', { rows, columns });

  // Existing MockUpstream named reply contract, server.mjs: non-stream assistant content.
  await mock.registerReply(prefix, 'prompt', output);
  const model = `${prefix}-model`;
  // CustomAIModelCreateView wraps data.id in result, and validates with a live completion.
  // The public-classified mock alias comes from lib/upstream, never a real provider.
  const custom = await actor.api.post<Envelope<{ data: { id: string } }>>('/model-hub/custom_models/create/', {
    model_provider: 'openai', model_name: model, input_token_cost: 0, output_token_cost: 0,
    config_json: { key: 'e2e-mock', api_base: `${mock.baseUrl(prefix, { reply: 'prompt' })}/v1` },
  });
  await attachIds(info, 'summary-model', { modelId: custom.result.data.id, model, scenario: prefix });
  const rowEval = await actor.api.post<Envelope<{ id: string }>>(TEMPLATES, {
    name: `${prefix}-row`, eval_type: 'code', code_language: 'python', output_type: 'pass_fail',
    code: 'def evaluate(output):\n    return bool(output)',
  });
  const prompt = await actor.api.post<Envelope<{ id: string }>>(PROMPTS, {
    name: `${prefix}-prompt`, is_draft: false, variable_names: {}, prompt_config: [{
      messages: [{ role: 'user', content: [{ type: 'text', text: `${prefix}-input` }] }],
      configuration: { model, model_detail: { value: model }, temperature: 0, max_tokens: 64 },
    }],
  });
  const versions = await probe.pg<{ id: string }>(
    `SELECT v.id FROM model_hub_promptversion v JOIN model_hub_prompttemplate p ON p.id=v.original_template_id
     WHERE p.id=$1 AND p.organization_id=$2 AND p.workspace_id=$3 AND v.template_version='v1' AND NOT v.deleted`,
    [prompt.result.id, actor.organizationId, actor.workspaceId]);
  expect(versions).toHaveLength(1);
  await attachIds(info, 'summary-prompt-and-row-evaluator', {
    promptId: prompt.result.id, promptVersionId: versions[0].id, rowTemplateId: rowEval.result.id,
  });

  const experimentName = `${prefix}-experiment`;
  await actor.api.post(EXPERIMENTS, {
    name: experimentName, dataset_id: datasetId, experiment_type: 'llm',
    prompt_config: [{ name: `${prefix}-run`, prompt_id: prompt.result.id, prompt_version: versions[0].id,
      model, model_params: { temperature: 0, max_tokens: 64 } }],
    user_eval_metrics: [{ template_id: rowEval.result.id, name: `${prefix}-row-check`,
      config: { mapping: { output: markerColumn.id } } }],
  });
  const experiments = await probe.pg<{ id: string; snapshot_dataset_id: string }>(
    `SELECT e.id, e.snapshot_dataset_id FROM model_hub_experimentstable e
     JOIN model_hub_dataset d ON d.id=e.dataset_id
     WHERE e.dataset_id=$1 AND e.name=$2 AND d.organization_id=$3 AND d.workspace_id=$4 AND NOT e.deleted`,
    [datasetId, experimentName, actor.organizationId, actor.workspaceId]);
  expect(experiments).toHaveLength(1);
  const experimentId = experiments[0].id;
  await attachIds(info, 'summary-experiment', { experimentId, snapshotDatasetId: experiments[0].snapshot_dataset_id });

  // native_run_binding.persist_snapshot_identity stores input.values keyed by SOURCE column UUID.
  // experiment_summary._population supplies those exact inputs plus terminal outputs to evaluate(rows).
  // custom_code_eval accepts {score, reason}; FunctionEvaluator metadata None is normalized to {}.
  // The expected 0.75 is deliberately NOT interpolated into this code: it must calculate the mean.
  const code = `def evaluate(rows):
    if len(rows) != 2:
        raise ValueError("Expected both sealed rows")
    markers = sorted([row["input"]["values"][${JSON.stringify(markerColumn.id)}] for row in rows])
    if markers != ${JSON.stringify([...markers].sort())}:
        raise ValueError("Wrong input population")
    for row in rows:
        if row["status"] != "completed" or row["output"] != ${JSON.stringify(output)}:
            raise ValueError("Expected completed prompt outputs")
    weights = [float(row["input"]["values"][${JSON.stringify(weightColumn.id)}]) for row in rows]
    return {"score": sum(weights) / len(rows), "reason": ${JSON.stringify(reason)}}
`;
  // The UI picker uses ordinary versioned eval-templates, NOT eval-summary-templates CRUD.
  const template = await actor.api.post<Envelope<{ id: string }>>(TEMPLATES, {
    name: templateName, eval_type: 'code', code_language: 'python', code,
    output_type: 'percentage', is_draft: false,
  });
  const templateId = template.result.id;
  const evalVersions = await probe.pg<{ id: string; version_number: number; code: string }>(
    `SELECT id, version_number, config_snapshot->>'code' AS code FROM model_hub_eval_template_version
     WHERE eval_template_id=$1 AND NOT deleted ORDER BY version_number`, [templateId]);
  expect(evalVersions.map(v => ({ version_number: v.version_number, code: v.code })))
    .toEqual([{ version_number: 1, code }]);
  const versionId = evalVersions[0].id;
  await attachIds(info, 'summary-evaluator-template', { templateId, versionId, templateName, summaryName });
  return { datasetId, experimentId, templateId, versionId, templateName, summaryName, output, reason,
    markers, markerColumnId: markerColumn.id, weightColumnId: weightColumn.id,
    rowIds: rows.map(r => r.id), promptVersionId: versions[0].id };
}

// Exact microsecond UTC comparison: PG to_char below and DRF ISO-8601 timestamps.
// Do not use Date.toISOString(), which would silently discard stored microseconds.
export function utcClock(value: string): string {
  const match = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(?:Z|\+00:00)$/.exec(value);
  if (!match) throw new Error('Expected a UTC summary timestamp');
  return `${match[1]}.${(match[2] ?? '').padEnd(6, '0')}Z`;
}

// model_hub/models/experiment_summary.py and serializers/experiment_summary.py.
export interface SummaryBinding {
  id: string; name: string; template_id: string; version_id: string; version_number: number;
  mapping: Record<string, string>; created_at: string;
}
export interface SummaryScore {
  id: string; binding_id: string; run_id: string; version_id: string; version_number: number;
  name: string; status: string; score: number | null; reason: string; metadata: Record<string, unknown>;
  error_code: string; attempt: number; created_at: string; started_at: string; completed_at: string;
  interrupted: boolean; publication_pending: boolean;
}
export const bindingProjection = (binding: SummaryBinding): SummaryBinding =>
  ({ ...binding, created_at: utcClock(binding.created_at) });
export const scoreProjection = (score: SummaryScore): SummaryScore => ({ ...score,
  created_at: utcClock(score.created_at), started_at: utcClock(score.started_at), completed_at: utcClock(score.completed_at),
});

export const storedBindings = (probe: StateProbe, experimentId: string) => probe.pg<SummaryBinding & {
  experiment_id: string; definition: { mapping: Record<string, string>; organization_id: string; workspace_id: string;
    pin: { template_id: string; evaluator_version_id: string }; template: { config: { code: string } } };
}>(`SELECT b.id, b.experiment_id, b.name, b.version_id, b.definition,
    v.eval_template_id AS template_id, v.version_number, b.definition->'mapping' AS mapping,
    to_char(b.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS created_at
    FROM model_hub_experimentsummarybinding b JOIN model_hub_eval_template_version v ON v.id=b.version_id
    WHERE b.experiment_id=$1 ORDER BY b.id`, [experimentId]);

export const storedScores = (probe: StateProbe, runId: string) => probe.pg<SummaryScore>(
  `SELECT s.id, s.binding_id, s.run_id, s.version_id, v.version_number, b.name,
    s.status, s.score, s.reason, s.metadata, s.error_code, s.attempt,
    to_char(s.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS created_at,
    to_char(s.started_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS started_at,
    to_char(s.completed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS completed_at,
    false AS interrupted, false AS publication_pending
   FROM model_hub_experimentsummaryresult s
   JOIN model_hub_experimentsummarybinding b ON b.id=s.binding_id
   JOIN model_hub_eval_template_version v ON v.id=s.version_id WHERE s.run_id=$1 ORDER BY s.id`, [runId]);
