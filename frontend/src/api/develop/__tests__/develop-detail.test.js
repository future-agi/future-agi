import { describe, expect, it } from "vitest";

import { normalizeDatasetListItem } from "../develop-detail";

describe("normalizeDatasetListItem", () => {
  it("adapts canonical dataset-name API rows for existing picker consumers", () => {
    expect(
      normalizeDatasetListItem({
        dataset_id: "dataset-1",
        name: "QA dataset",
        model_type: "GenerativeLLM",
      }),
    ).toMatchObject({
      dataset_id: "dataset-1",
      datasetId: "dataset-1",
      name: "QA dataset",
      modelType: "GenerativeLLM",
    });
  });

  it("does not overwrite existing camelCase values from local state", () => {
    expect(
      normalizeDatasetListItem({
        id: "fallback-id",
        datasetId: "local-id",
        name: "Local dataset",
      }),
    ).toMatchObject({
      datasetId: "local-id",
      name: "Local dataset",
    });
  });
});
