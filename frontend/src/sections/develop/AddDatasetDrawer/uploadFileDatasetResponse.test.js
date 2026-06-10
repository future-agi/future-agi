import { describe, expect, it } from "vitest";
import { getUploadedDatasetId } from "./uploadFileDatasetResponse";

describe("getUploadedDatasetId", () => {
  it("reads the canonical snake_case local-file dataset create response", () => {
    expect(
      getUploadedDatasetId({
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
      getUploadedDatasetId({
        data: {
          result: {
            datasetId: "dataset-camel",
          },
        },
      }),
    ).toBe("dataset-camel");
  });
});
