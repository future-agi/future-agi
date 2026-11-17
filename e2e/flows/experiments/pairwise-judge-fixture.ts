import type { TestInfo } from '@playwright/test';
import { expect } from '../../lib/fixtures';
import type { TestActor } from '../../lib/provisioning';
import type { StateProbe } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';

// ManuallyCreateDatasetView, PromptTemplateViewSet.create_draft and
// ExperimentCreateV2Serializer; also used by evaluation-column-scope.spec.ts.
const DATASETS = '/model-hub/develops/create-dataset-manually/';
const PROMPTS = '/model-hub/prompt-templates/create-draft/';
export const EXPERIMENTS = '/model-hub/experiments/v2/';
// EvalTemplateCreateV2RequestSerializer and separate_evals.py version views.
const TEMPLATES = '/model-hub/eval-templates/create-v2/';
const versionsPath = (id: string) => `/model-hub/eval-templates/${id}/versions/`;
type Envelope<T> = { result: T };
interface Version {
  id: string; version_number: number; model: string;
  config_snapshot: { eval_type_id: string; output: string; choices: string[];
    required_keys: string[]; multi_choice: boolean; few_shot_examples: unknown[] };
}
export interface Run {
  id: string; prompt_version_id: string; dataset_version_id: string; status: string;
}
export interface RunItem {
  run_id: string; example_id: string; trial_number: number; status: string;
  input: unknown; output: string;
}
export interface Example { example_id: string; input: unknown; legacy_row_id: string }
export interface CatalogRun { run_id: string; label: string; comparison_group: string | null }
export interface Verdict {
  example_id: string; trial_number: number; status: string; winner: string; reason: string; error: string;
}
export interface JudgeItem extends Verdict {
  snapshot: { input: unknown; output_a: string; output_b: string };
  presentation_order: string; finished_at: string | null;
}
export interface Job {
  id: string; experiment_id: string; organization_id: string; workspace_id: string;
  run_a_id: string; run_b_id: string; judge_version_id: string; dataset_version_id: string;
  status: string; finished_at: string | null;
}
export interface Detail {
  id: string; source: string; run_a: string; run_b: string; judge_version: string;
  dataset_version: string; status: string; counts: Record<string, number>; total: number;
  next_offset: number | null; items: (Verdict & { presentation_order: string })[];
}
export const attach = (info: TestInfo, name: string, value: unknown) =>
  info.attach(name, { contentType: 'application/json', body: JSON.stringify(value, null, 2) });

export async function seedPairwise(actor: TestActor, probe: StateProbe, mock: MockUpstream,
  prefix: string, info: TestInfo) {
  const model = `${prefix}-model`;
  const preferredOutput = `${prefix}-preferred-output`;
  const otherOutput = `${prefix}-other-output`;
  const reason = `${prefix}-preferred-is-clearer`;
  const rowMarker = `${prefix}-row-evaluator`;
  const markers = [`${prefix}-produce-preferred`, `${prefix}-produce-other`];
  // mock-llm/server.mjs: ordered non-stream user_contains rules. The evaluator
  // renders XML input tags; match BOTH presentations, not always the literal A.
  await mock.registerReplyRules(prefix, [
    { user_contains: `<output_a>${preferredOutput}</output_a>`, body: JSON.stringify({ result: 'A', explanation: reason }) },
    { user_contains: `<output_b>${preferredOutput}</output_b>`, body: JSON.stringify({ result: 'B', explanation: reason }) },
    { user_contains: rowMarker, body: JSON.stringify({ result: 'Pass', explanation: `${prefix}-row-pass` }) },
    { user_contains: markers[0], body: preferredOutput },
    { user_contains: markers[1], body: otherOutput },
  ]);
  // CustomModelCreateSerializer. Only the existing stack-local fixture alias;
  // walk-lead qualifies topology separately. No managed inspector bypass.
  const custom = await actor.api.post<Envelope<{ data: { id: string } }>>('/model-hub/custom_models/create/', {
    model_provider: 'openai', model_name: model, input_token_cost: 0, output_token_cost: 0,
    config_json: { key: 'pairwise-local-fixture-only', api_base: `${mock.baseUrl(prefix)}/v1` },
  });
  await attach(info, 'seeded-local-model', { modelId: custom.result.data.id, model, scenario: prefix });
  const ds = await actor.api.post<Envelope<{ dataset_id: string }>>(DATASETS,
    { dataset_name: `${prefix}-dataset`, number_of_rows: 2, number_of_columns: 1 });
  const datasetId = ds.result.dataset_id;
  await attach(info, 'seeded-dataset', { datasetId, organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  // develop_dataset.py Row/Column, scoped through the newly minted dataset.
  const scope = [datasetId, actor.organizationId, actor.workspaceId];
  const rows = await probe.pg<{ id: string }>(`SELECT r.id FROM model_hub_row r
    JOIN model_hub_dataset d ON d.id=r.dataset_id
    WHERE d.id=$1 AND d.organization_id=$2 AND d.workspace_id=$3 AND NOT d.deleted AND NOT r.deleted ORDER BY r.id`, scope);
  const columns = await probe.pg<{ id: string }>(`SELECT c.id FROM model_hub_column c
    JOIN model_hub_dataset d ON d.id=c.dataset_id
    WHERE d.id=$1 AND d.organization_id=$2 AND d.workspace_id=$3 AND NOT d.deleted AND NOT c.deleted`, scope);
  expect(rows).toHaveLength(2);
  expect(columns).toHaveLength(1);
  const questions = rows.map((_, i) => `${prefix}-question-${i + 1}`);
  for (const [i, row] of rows.entries()) {
    await actor.api.post(`/model-hub/develops/${datasetId}/update_cell_value/`,
      { row_id: row.id, column_id: columns[0].id, new_value: questions[i] });
  }
  await attach(info, 'seeded-rows', { rows, columns, questions });
  const prompts: { id: string; versionId: string; name: string }[] = [];
  for (const [i, marker] of markers.entries()) {
    const name = `${prefix}-${i === 0 ? 'preferred' : 'other'}`;
    const prompt = await actor.api.post<Envelope<{ id: string }>>(PROMPTS, {
      name, is_draft: false, variable_names: {}, prompt_config: [{
        messages: [{ role: 'user', content: [{ type: 'text', text: marker }] }],
        configuration: { model, model_detail: { value: model }, temperature: 0, max_tokens: 64 },
      }],
    });
    const versions = await probe.pg<{ id: string }>(`SELECT v.id FROM model_hub_promptversion v
      JOIN model_hub_prompttemplate p ON p.id=v.original_template_id
      WHERE p.id=$1 AND p.organization_id=$2 AND p.workspace_id=$3 AND v.template_version='v1' AND NOT v.deleted`,
    [prompt.result.id, actor.organizationId, actor.workspaceId]);
    expect(versions).toHaveLength(1);
    prompts.push({ id: prompt.result.id, versionId: versions[0].id, name });
    await attach(info, `seeded-prompt-${i}`, prompts[i]);
  }
  const rowEval = await actor.api.post<Envelope<{ id: string }>>(TEMPLATES, {
    name: `${prefix}-row-eval`, eval_type: 'llm', model, output_type: 'pass_fail',
    instructions: `${rowMarker}: assess {{output}}.`, pass_threshold: 0.5,
    check_internet: false, error_localizer_enabled: false,
  });
  const rowVersions = await actor.api.get<Envelope<{ versions: Version[] }>>(versionsPath(rowEval.result.id));
  expect(rowVersions.result.versions.map(v => v.version_number)).toEqual([1]);
  const rowVersionId = rowVersions.result.versions[0].id;
  await attach(info, 'seeded-row-evaluator', { id: rowEval.result.id, versionId: rowVersionId });

  // require_version in services/pairwise_judges.py requires a SINGLE pinned
  // CustomPromptEvaluator with exactly these keys and single A/B/tie choices.
  const judge = await actor.api.post<Envelope<{ id: string }>>(TEMPLATES, {
    name: `${prefix}-judge`, eval_type: 'llm', model, output_type: 'deterministic',
    instructions: 'Compare {{output_a}} with {{output_b}} for {{input}}.',
    choice_scores: { A: 1, B: 0, tie: 0.5 }, multi_choice: false, few_shot_examples: [],
    pass_threshold: 0.5, check_internet: false, error_localizer_enabled: false, template_format: 'mustache',
  });
  const initial = await actor.api.get<Envelope<{ versions: Version[] }>>(versionsPath(judge.result.id));
  expect(initial.result.versions.map(v => v.version_number)).toEqual([1]);
  const saved = await actor.api.post<Envelope<{ id: string; version_number: number }>>(`${versionsPath(judge.result.id)}create/`, {});
  const judgeVersionId = saved.result.id;
  await attach(info, 'seeded-pairwise-version', { templateId: judge.result.id, ...saved.result });
  expect(saved.result.version_number).toBe(2);
  const listed = await actor.api.get<Envelope<{ versions: Version[] }>>(versionsPath(judge.result.id));
  expect(listed.result.versions.map(v => v.version_number)).toEqual([2, 1]);
  const version = listed.result.versions.find(v => v.id === judgeVersionId)!;
  expect(version).toBeDefined();
  expect(version.model).toBe(model);
  const cfg = version.config_snapshot;
  expect({ eval_type_id: cfg.eval_type_id, output: cfg.output, choices: [...cfg.choices].sort(),
    required_keys: [...cfg.required_keys].sort(), multi_choice: cfg.multi_choice, few_shot_examples: cfg.few_shot_examples })
    .toEqual({ eval_type_id: 'CustomPromptEvaluator', output: 'choices', choices: ['A', 'B', 'tie'],
      required_keys: ['input', 'output_a', 'output_b'], multi_choice: false, few_shot_examples: [] });
  const persisted = await probe.pg<{ id: string; eval_type: string; template_type: string; model: string; config_snapshot: unknown }>(
    `SELECT v.id,t.eval_type,t.template_type,v.model,v.config_snapshot FROM model_hub_eval_template_version v
     JOIN model_hub_evaltemplate t ON t.id=v.eval_template_id
     WHERE v.id=$1 AND t.id=$2 AND t.organization_id=$3 AND t.workspace_id=$4 AND NOT v.deleted AND NOT t.deleted`,
    [judgeVersionId, judge.result.id, actor.organizationId, actor.workspaceId]);
  expect(persisted).toEqual([{ id: judgeVersionId, eval_type: 'llm', template_type: 'single', model, config_snapshot: cfg }]);

  const created = await actor.api.post<Envelope<string>>(EXPERIMENTS, {
    name: `${prefix}-experiment`, dataset_id: datasetId, experiment_type: 'llm',
    prompt_config: prompts.map(p => ({ name: p.name, prompt_id: p.id, prompt_version: p.versionId,
      model, model_params: { temperature: 0, max_tokens: 64 } })),
    user_eval_metrics: [{ template_id: rowEval.result.id, pinned_version_id: rowVersionId,
      name: `${prefix}-binding`, model, config: { mapping: { output: columns[0].id } } }],
  });
  // ExperimentsTableV2View.post returns a message, not an object envelope ID.
  const match = /experiment_id=([0-9a-f-]{36})/.exec(created.result);
  expect(match).not.toBeNull();
  const experimentId = match![1];
  const seeded = { datasetId, columnId: columns[0].id, rowIds: rows.map(r => r.id), questions, prompts, experimentId,
    judgeVersionId, model, preferredOutput, otherOutput, reason };
  await attach(info, 'seeded-experiment', seeded);
  return seeded;
}

// Canonical native execution tables: models/experiments.py. No fixture writes.
export async function nativeState(probe: StateProbe, actor: TestActor, experimentId: string) {
  const scope = [experimentId, actor.organizationId, actor.workspaceId];
  const runs = await probe.pg<Run>(`SELECT r.id,r.prompt_version_id,r.dataset_version_id,r.status
    FROM model_hub_experimentrun r JOIN model_hub_experimentstable e ON e.id=r.experiment_id
    JOIN model_hub_dataset d ON d.id=e.dataset_id
    WHERE e.id=$1 AND d.organization_id=$2 AND d.workspace_id=$3 AND NOT r.deleted AND NOT e.deleted AND NOT d.deleted ORDER BY r.prompt_version_id`, scope);
  const items = await probe.pg<RunItem>(`SELECT i.run_id,i.example_id,i.trial_number,i.status,i.input,i.output
    FROM model_hub_experimentrunitem i JOIN model_hub_experimentrun r ON r.id=i.run_id
    JOIN model_hub_experimentstable e ON e.id=r.experiment_id JOIN model_hub_dataset d ON d.id=e.dataset_id
    WHERE e.id=$1 AND d.organization_id=$2 AND d.workspace_id=$3 AND NOT i.deleted AND NOT r.deleted
    ORDER BY i.run_id,i.example_id,i.trial_number`, scope);
  return { runs, items };
}

export async function judgeState(probe: StateProbe, actor: TestActor, experimentId: string, jobId: string) {
  // PairwiseJudgeJob has no soft-delete fields. Scope both the row and dataset.
  const scope = [jobId, experimentId, actor.organizationId, actor.workspaceId];
  const jobs = await probe.pg<Job>(`SELECT j.id,j.experiment_id,j.organization_id,j.workspace_id,
    j.run_a_id,j.run_b_id,j.judge_version_id,j.dataset_version_id,j.status,j.finished_at::text
    FROM model_hub_pairwisejudgejob j JOIN model_hub_experimentstable e ON e.id=j.experiment_id
    JOIN model_hub_dataset d ON d.id=e.dataset_id
    WHERE j.id=$1 AND e.id=$2 AND j.organization_id=$3 AND j.workspace_id=$4
      AND d.organization_id=$3 AND d.workspace_id=$4 AND NOT e.deleted AND NOT d.deleted`, scope);
  const items = await probe.pg<JudgeItem>(`SELECT i.example_id,i.trial_number,i.status,i.winner,i.reason,i.error,
    i.snapshot,i.presentation_order,i.finished_at::text FROM model_hub_pairwisejudgeitem i
    JOIN model_hub_pairwisejudgejob j ON j.id=i.job_id
    WHERE j.id=$1 AND j.experiment_id=$2 AND j.organization_id=$3 AND j.workspace_id=$4
    ORDER BY i.example_id,i.trial_number`, scope);
  return { jobs, items };
}

export function verdictProjection(item: Verdict): Verdict {
  const { example_id, trial_number, status, winner, reason, error } = item;
  return { example_id, trial_number, status, winner, reason, error };
}
