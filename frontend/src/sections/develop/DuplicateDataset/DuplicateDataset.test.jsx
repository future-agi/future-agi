import { describe, expect, it } from "vitest";
import { getClonedDatasetInfo } from "./duplicateDatasetResponse";

describe("getClonedDatasetInfo", () => {
  it("reads canonical snake_case clone response metadata", () => {
    expect(
      getClonedDatasetInfo({
        result: {
          dataset_id: "cloned-dataset",
          dataset_name: "Cloned Dataset",
        },
      }),
    ).toEqual({
      datasetId: "cloned-dataset",
      datasetName: "Cloned Dataset",
    });
  });

  it("keeps legacy camelCase metadata fallbacks", () => {
    expect(
      getClonedDatasetInfo({
        result: {
          datasetId: "legacy-cloned-dataset",
          datasetName: "Legacy Clone",
        },
      }),
    ).toEqual({
      datasetId: "legacy-cloned-dataset",
      datasetName: "Legacy Clone",
    });
  });
});
