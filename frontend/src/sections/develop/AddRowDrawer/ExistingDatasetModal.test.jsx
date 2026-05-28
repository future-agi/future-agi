import { describe, expect, it } from "vitest";
import { getExistingDatasetConfigValues } from "./existingDatasetMapping";
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

describe("getExistingDatasetConfigValues", () => {
  const sourceColumns = [
    { id: "source-1", name: "Column 1" },
    { id: "source-2", name: "Column 2" },
    { id: "source-3", name: "Source only" },
  ];

  it("defaults add-as-new mappings to source column names", () => {
    expect(getExistingDatasetConfigValues(sourceColumns)).toEqual({
      mapping: {
        "Column 1": "Column 1",
        "Column 2": "Column 2",
        "Source only": "Source only",
      },
    });
  });

  it("prefills same-name target column ids for existing dataset imports", () => {
    expect(
      getExistingDatasetConfigValues(sourceColumns, {
        isAddingToExistingDataset: true,
        targetColumns: [
          { id: "target-1", name: "Column 1" },
          { id: "target-2", name: "Column 2" },
        ],
      }),
    ).toEqual({
      mapping: {
        "Column 1": "target-1",
        "Column 2": "target-2",
        "Source only": "",
      },
    });
  });
});
