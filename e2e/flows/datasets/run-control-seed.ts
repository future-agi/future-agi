import { expect } from '@playwright/test';
import type { ScopeActor } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';

// Flow-owned local provider fixture; see run-control.README.md for its container recipe.
export const STANDIN = 'http://localhost:28082';
export const MODEL = 'gpt-4o';
export const API = '/model-hub/experiments/v2/'; // model_hub/urls.py
// Two variants produce 48 judge calls, below the default 50/minute quota.
export const ROWS = 24;
export type Run = { run_id: string; name: string; status: string };
export type Control = { result: { experiment_id: string; runs: Run[] } };
export type Cell = { id: string; row_id: string; value: string; status: string; updated_at: Date };

export async function seedRunControl(actor: ScopeActor, probe: StateProbe, suffix: string) {
  // CustomAIModelCreateRequestSerializer, matching managed-mock.ts registration;
  // only the flow-owned compose STANDIN is addressable, never a hosted provider.
  await actor.api.post('/model-hub/custom_models/create/', {
    model_provider: 'openai', model_name: MODEL, input_token_cost: 0, output_token_cost: 0,
    config_json: { key: 'e2e-mock', api_base: 'http://run-control-standin:8080/v1' },
  });
  // ManualDatasetCreateRequestSerializer and ManualDatasetCreateResponseSerializer.
  const created = await actor.api.post<{ result: { dataset_id: string } }>(
    '/model-hub/develops/create-dataset-manually/', {
      dataset_name: `e2e-control-${suffix}`, number_of_rows: ROWS, number_of_columns: 1,
    });
  const datasetId = created.result.dataset_id;
  const entries = [];
  for (const role of ['selected', 'sibling']) {
    const marker = `CONTROL:${suffix}:${role}`;
    // PromptTemplateViewSet.create_draft reads prompt_config[0] and is_draft;
    // publishing at creation is a supported public API, not a SQL fixture.
    const prompt = await actor.api.post<{ result: { root_template: string } }>(
      '/model-hub/prompt-templates/create-draft/', {
        name: `e2e-control-${role}-${suffix}`, is_draft: false,
        prompt_config: [{ messages: [{ role: 'user', content: marker }],
          configuration: { model: MODEL }, model_params: {} }],
      });
    const versions = await probe.pg<{ id: string }>(
      'SELECT id FROM model_hub_promptversion WHERE original_template_id=$1 AND template_version=$2 AND NOT deleted AND NOT is_draft',
      [prompt.result.root_template, 'v1']);
    expect(versions).toHaveLength(1);
    entries.push({ name: role, prompt_id: prompt.result.root_template,
      prompt_version: versions[0].id, model: MODEL, messages: [{ role: 'user', content: marker }] });
  }
  // EvalTemplateCreateV2RequestSerializer; output maps to each variant's actual output
  // (analyze_dependencies_activity treats the literal "output" as a dependent eval).
  const evaluator = await actor.api.post<{ result: { id: string } }>(
    '/model-hub/eval-templates/create-v2/', {
      name: `e2e-control-judge-${suffix}`, eval_type: 'llm', model: MODEL,
      instructions: 'CONTROL_JUDGE Return Pass with an explanation for {{output}}',
      output_type: 'pass_fail', pass_threshold: 0.5, check_internet: false,
      error_localizer_enabled: false,
    });
  // ExperimentCreateV2Serializer. The create response intentionally keeps the UUID
  // in its result string; the detail and run-control APIs carry structured identities.
  const experiment = await actor.api.post<{ result: string }>(API, {
    name: `e2e-control-${suffix}`, dataset_id: datasetId, experiment_type: 'llm',
    prompt_config: entries,
    user_eval_metrics: [{ template_id: evaluator.result.id, name: `judge-${suffix}`, model: MODEL,
      config: { mapping: { output: 'output' }, config: {}, reason_column: true } }],
  });
  const experimentId = /experiment_id=([0-9a-f-]{36})/.exec(experiment.result)?.[1];
  expect(experimentId, experiment.result).toBeTruthy();
  return { datasetId, experimentId: experimentId!, suffix };
}

export async function runCells(probe: StateProbe, runId: string) {
  // The variant's M2M contains outputs and its dependent evaluation/reason columns.
  return probe.pg<Cell & { source: string }>(`SELECT cell.id,cell.row_id,cell.value,
    cell.status,cell.updated_at,col.source FROM model_hub_cell cell
    JOIN model_hub_column col ON col.id=cell.column_id
    JOIN model_hub_experimentdatasettable_columns link ON link.column_id=col.id
    WHERE link.experimentdatasettable_id=$1 AND NOT col.deleted AND NOT cell.deleted
    ORDER BY cell.id`, [runId]);
}

export async function outputCells(probe: StateProbe, runId: string) {
  // ExperimentDatasetTable.columns M2M includes output columns; source distinguishes evals.
  return probe.pg<Cell>(`SELECT cell.id,cell.row_id,cell.value,cell.status,cell.updated_at
    FROM model_hub_cell cell JOIN model_hub_column col ON col.id=cell.column_id
    JOIN model_hub_experimentdatasettable_columns link ON link.column_id=col.id
    WHERE link.experimentdatasettable_id=$1 AND col.source='experiment'
      AND NOT col.deleted AND NOT cell.deleted ORDER BY cell.id`, [runId]);
}
