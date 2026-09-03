import { describe, expect, it } from "vitest";
import { getCreatedRowsDatasetId } from "./createDatasetRowsResponse";

describe("getCreatedRowsDatasetId", () => {
  it("reads the canonical snake_case selected rows duplicate response", () => {
    expect(
      getCreatedRowsDatasetId({
        data: {
          result: {
            new_dataset_id: "dataset-snake",
          },
        },
      }),
    ).toBe("dataset-snake");
  });

  it("keeps legacy camelCase and generic dataset id fallbacks", () => {
    expect(
      getCreatedRowsDatasetId({
        data: {
          result: {
            newDatasetId: "dataset-camel",
          },
        },
      }),
    ).toBe("dataset-camel");

    expect(
      getCreatedRowsDatasetId({
        data: {
          result: {
            dataset_id: "dataset-generic",
          },
        },
      }),
    ).toBe("dataset-generic");
  });
});
