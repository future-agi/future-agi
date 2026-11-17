import { test, expect } from '../../lib/scope-actors';

// HuggingFaceAddRowsRequestSerializer; the destination is checked before Hub access.
test('Hugging Face revision requests preserve destination isolation', async ({ scopeActors }, testInfo) => {
  test.setTimeout(180_000);
  const owner = scopeActors.ownerA;
  const created = await owner.api.post<{ result: { dataset_id: string } }>(
    '/model-hub/develops/create-dataset-manually/', {
      dataset_name: `e2e-hf-scope-${testInfo.workerIndex}-${Date.now().toString(36)}`,
      number_of_rows: 1, number_of_columns: 1,
    });
  const id = created.result.dataset_id;
  await testInfo.attach('scope', { contentType: 'application/json', body: JSON.stringify({
    id, organizationId: owner.organizationId, workspaceId: owner.workspaceId,
  }) });
  for (const actor of [scopeActors.ownerB, scopeActors.withWorkspace(owner, scopeActors.emptyWorkspace.id)]) {
    const result = await scopeActors.send(actor, 'POST', `/model-hub/develops/${id}/add_rows_from_huggingface/`, {
      huggingface_dataset_name: 'fixture/revisions', huggingface_dataset_config: 'default',
      huggingface_dataset_split: 'train', num_rows: 1, revision: 'release-v1',
    });
    expect(result.status).toBe(404);
  }
});
