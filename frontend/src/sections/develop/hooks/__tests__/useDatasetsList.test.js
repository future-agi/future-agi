import { describe, expect, it } from "vitest";

import { normalizeDatasetRow } from "../useDatasetsList";

describe("normalizeDatasetRow", () => {
  it("adapts canonical dataset-list API rows for the table columns", () => {
    expect(
      normalizeDatasetRow({
        id: "dataset-1",
        name: "QA dataset",
        number_of_datapoints: 4,
        number_of_experiments: 2,
        number_of_optimisations: 1,
        derived_datasets: 3,
        created_at: "2026-05-21 12:00",
        dataset_type: "GenerativeLLM",
      }),
    ).toMatchObject({
      id: "dataset-1",
      name: "QA dataset",
      numberOfDatapoints: 4,
      numberOfExperiments: 2,
      numberOfOptimisations: 1,
      derivedDatasets: 3,
      createdAt: "2026-05-21 12:00",
      datasetType: "GenerativeLLM",
    });
  });

  it("preserves zero values instead of falling back to defaults", () => {
    expect(
      normalizeDatasetRow({
        number_of_datapoints: 0,
        number_of_experiments: 0,
        number_of_optimisations: 0,
        derived_datasets: 0,
      }),
    ).toMatchObject({
      numberOfDatapoints: 0,
      numberOfExperiments: 0,
      numberOfOptimisations: 0,
      derivedDatasets: 0,
    });
  });
});
