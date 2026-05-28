import { describe, expect, it } from "vitest";
import { getCreatedDatasetCopyId } from "./existingDatasetResponse";

describe("getCreatedDatasetCopyId", () => {
  it("reads the canonical snake_case add-as-new response id", () => {
    expect(
      getCreatedDatasetCopyId({
        data: {
          result: {
            dataset_id: "dataset-copy",
          },
        },
      }),
    ).toBe("dataset-copy");
  });

  it("keeps clone/duplicate id fallbacks for legacy callers", () => {
    expect(
      getCreatedDatasetCopyId({
        data: {
          result: {
            new_dataset_id: "dataset-clone",
          },
        },
      }),
    ).toBe("dataset-clone");
  });
});
