import { expect } from '@playwright/test';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';

// ApiKeyRequestSerializer accepts JSON provider config; no custom executor is used.
// The standard text executor reaches this isolated OpenAI-format LLM STANDIN.
// Structured OpenAI keys use `key` (LLM.call_llm and RunPrompt normalization).
export const MODEL = 'gpt-4o-mini';
export const CREATE = '/model-hub/experiments/v2/'; // model_hub/urls.py
export const STANDIN = 'http://localhost:28082';
export const ROWS = 6;
export type ScopeDetail = { result: { id: string; snapshot_dataset: string;
  run_scope: { source_count: number; eligible_count: number; selected_count: number;
    selected_row_ids: string[]; sample: { size: number; seed: number } | null };
  limits: Record<string, number>; error_policy: string; metadata: Record<string, string> } };

export async function seedScope(actor: ScopeActor, probe: StateProbe, suffix: string, actors: ScopeActors) {
  await actor.api.post('/model-hub/api-keys/', { provider: 'openai',
    config_json: { key: 'e2e-mock', api_base: 'http://scope-standin:8080/v1' } });
  // ManualDatasetCreateRequestSerializer: public seed, not replacement dataset SQL.
  const created = await actor.api.post<{ result: { dataset_id: string } }>(
    '/model-hub/develops/create-dataset-manually/', {
      dataset_name: `e2e-scope-${suffix}`, number_of_rows: ROWS, number_of_columns: 1,
    });
  const datasetId = created.result.dataset_id;
  const rows = await probe.pg<{ id: string }>(
    'SELECT id FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted ORDER BY "order",id', [datasetId]);
  expect(rows).toHaveLength(ROWS);
  const columns = await probe.pg<{ id: string }>('SELECT id FROM model_hub_column WHERE dataset_id=$1 AND NOT deleted', [datasetId]);
  expect(columns).toHaveLength(1);
  // DatasetUpdateCellValueRequestSerializer: distinguish every source row on the public path.
  for (const row of rows) {
    const written = await actors.send(actor, 'POST', `/model-hub/develops/${datasetId}/update_cell_value/`, {
      row_id: row.id, column_id: columns[0].id, new_value: `source-${row.id}`,
    });
    expect(written.status).toBe(200);
  }
  // PromptTemplateViewSet.create_draft supports publishing on creation.
  const promptName = `e2e-scope-prompt-${suffix}`;
  const prompt = await actor.api.post<{ result: { root_template: string } }>(
    '/model-hub/prompt-templates/create-draft/', {
      name: promptName, is_draft: false, prompt_config: [{
        messages: [{ role: 'system', content: 'Return the marker.' },
          { role: 'user', content: `SCOPE:${suffix}` }],
        configuration: { model: MODEL }, model_params: { max_tokens: 1000 },
      }],
    });
  const versions = await probe.pg<{ id: string }>(
    `SELECT id FROM model_hub_promptversion WHERE original_template_id=$1
     AND template_version='v1' AND NOT deleted AND NOT is_draft`, [prompt.result.root_template]);
  expect(versions).toHaveLength(1);
  // EvalTemplateCreateV2RequestSerializer and UserEvalMutationRequestSerializer.
  const evaluator = await actor.api.post<{ result: { id: string } }>(
    '/model-hub/eval-templates/create-v2/', {
      name: `e2e-scope-eval-${suffix}`, eval_type: 'llm', model: MODEL,
      instructions: 'SCOPE_JUDGE Return Pass for {{output}}', output_type: 'pass_fail',
      pass_threshold: 0.5, check_internet: false, error_localizer_enabled: false,
    });
  const evalName = `scope-${suffix}`;
  await actor.api.post(`/model-hub/develops/${datasetId}/add_user_eval/`, {
    name: evalName, template_id: evaluator.result.id, model: MODEL, run: false,
    config: { mapping: { output: 'output' }, config: {}, reason_column: false },
  });
  const metrics = await probe.pg<{ id: string }>(
    'SELECT id FROM model_hub_userevalmetric WHERE dataset_id=$1 AND name=$2 AND NOT deleted', [datasetId, evalName]);
  expect(metrics).toHaveLength(1);
  const defaults = await actors.send(actor, 'PUT', `/model-hub/develops/${datasetId}/edit_dataset_behavior/`, {
    experiment_default_eval_ids: [metrics[0].id],
  });
  expect(defaults.status).toBe(200);
  return { datasetId, columnId: columns[0].id, rowIds: rows.map(row => row.id), promptId: prompt.result.root_template,
    promptVersion: versions[0].id, promptName, evalName, suffix };
}

export async function resultCells(probe: StateProbe, experimentId: string) {
  // Experiment output and evaluation columns belong to the immutable snapshot.
  return probe.pg<{ value: string; status: string; source: string; value_infos: string; column_id: string }>(
    `SELECT c.value,c.status,col.source,c.value_infos,c.column_id FROM model_hub_cell c
     JOIN model_hub_column col ON col.id=c.column_id
     JOIN model_hub_experimentstable e ON e.snapshot_dataset_id=c.dataset_id
     WHERE e.id=$1 AND NOT c.deleted AND NOT col.deleted
       AND col.source IN ('experiment','experiment_evaluation') ORDER BY col.source,c.row_id`, [experimentId]);
}
