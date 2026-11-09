import { expect } from '@playwright/test';
import type { ScopeActors } from '../../../lib/scope-actors';
import type { StateProbe } from '../../../lib/state-probe';
import { inspectManagedMock, registerMockModel, MOCK_MODEL } from '../../../lib/managed-mock';

// CreateEmptyDatasetRequestSerializer, ExperimentCreateV2Serializer,
// PromptTemplateViewSet.create_draft and PromptHistoryExecutionViewSet.list.
export async function seedSource(scopes: ScopeActors, probe: StateProbe, index: number, name: string) {
  const actor = index === 0 ? scopes.ownerA : scopes.ownerB;
  const receipt = inspectManagedMock();
  const model = await registerMockModel(scopes.owners[index], probe, () => expect(inspectManagedMock()).toEqual(receipt));
  const dataset = await actor.api.post<{ result: { dataset_id: string } }>('/model-hub/develops/create-empty-dataset/', {
    new_dataset_name: `${name}-dataset`, row: 0,
  });
  const prompt = await actor.api.post<{ result: { id: string } }>('/model-hub/prompt-templates/create-draft/', {
    name: `${name}-prompt`, is_draft: false,
    prompt_config: [{ messages: [{ role: 'user', content: `${name}-input` }],
      configuration: { model: MOCK_MODEL, temperature: 0, max_tokens: 128, tools: [] } }],
  });
  const versions = await actor.api.get<{ results: { id: string; is_draft: boolean }[] }>('/model-hub/prompt-history-executions/', { template_id: prompt.result.id });
  expect(versions.results).toHaveLength(1);
  expect(versions.results[0].is_draft).toBe(false);
  // EvalTemplateCreateV2Serializer; no real provider route is allowed.
  const evaluator = await actor.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
    name: `${name}-judge`, eval_type: 'llm', model: MOCK_MODEL, output_type: 'deterministic',
    instructions: `Reply with exactly this JSON: {"result":"Pass","explanation":"${name}"}\nMapped input: {{output}}`,
    pass_threshold: 0.5, choice_scores: { Pass: 1, Fail: 0 }, check_internet: false,
    error_localizer_enabled: false, template_format: 'mustache',
  });
  await model.assertReady();
  const experiment = await actor.api.post<{ result: string }>('/model-hub/experiments/v2/', {
    name: `${name}-experiment`, dataset_id: dataset.result.dataset_id, experiment_type: 'llm',
    prompt_config: [{ name: 'Original', prompt_id: prompt.result.id, prompt_version: versions.results[0].id, model: MOCK_MODEL }],
    user_eval_metrics: [{ name: 'output-check', template_id: evaluator.result.id, model: MOCK_MODEL, config: { mapping: { output: 'output' } } }],
  });
  const experimentId = /experiment_id=([0-9a-f-]{36})/.exec(experiment.result)?.[1];
  expect(experimentId).toBeTruthy();
  return { datasetId: dataset.result.dataset_id, experimentId: experimentId!, name,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId };
}
