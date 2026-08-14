import { describe, expect, it } from "vitest";
import { getCreatedDatasetId } from "./manualCreateDatasetResponse";

describe("getCreatedDatasetId", () => {
  it("reads the canonical snake_case manual dataset create response", () => {
    expect(
      getCreatedDatasetId({
        data: {
          result: {
            dataset_id: "dataset-snake",
          },
        },
      }),
    ).toBe("dataset-snake");
  });

  it("keeps the legacy camelCase response fallback", () => {
    expect(
      getCreatedDatasetId({
        data: {
          result: {
            datasetId: "dataset-camel",
          },
        },
      }),
    ).toBe("dataset-camel");
  });
});
