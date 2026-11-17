import { test, expect } from '../../lib/scope-actors';

// GetHuggingFaceDatasetConfigView returns a pinned commit, split names and row counts.
test('Hub metadata changes with the selected revision', async ({ scopeActors }) => {
  test.setTimeout(180_000);
  const pins = [];
  for (const [revision, count] of [['v1', 2], ['v2', 3]] as const) {
    const response = await scopeActors.send<{ result: { dataset_info: {
      revision: string; splits: Record<string, string[]>; num_rows: Record<string, Record<string, number>>;
    } } }>(scopeActors.ownerA, 'POST', '/model-hub/develops/get-huggingface-dataset-config/', {
      dataset_path: 'fixture/e2e-hf-metadata', revision,
    });
    expect(response.status, JSON.stringify(response.body)).toBe(200);
    expect(response.body.result.dataset_info.splits).toEqual({ default: ['train'] });
    expect(response.body.result.dataset_info.num_rows).toEqual({ default: { train: count } });
    pins.push(response.body.result.dataset_info.revision);
  }
  expect(pins[0]).not.toBe(pins[1]);
});
