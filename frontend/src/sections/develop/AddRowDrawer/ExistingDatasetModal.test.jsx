import { describe, expect, it } from "vitest";
import { getExistingDatasetConfigValues } from "./existingDatasetMapping";
import { getAddRowsFromExistingPayload } from "./existingDatasetPayload";
import { getCreatedDatasetCopyId } from "./existingDatasetResponse";
import { existingDatasetValidationSchema } from "./validation";

describe("existingDatasetValidationSchema num_rows", () => {
  const schema = existingDatasetValidationSchema("target-dataset", {
    "Column 1": true,
  });
  const formValues = {
    name: "",
    value: "source-dataset",
    dataType: "importData",
    config: { mapping: { "Column 1": "target-1" } },
  };

  it.each(["", undefined, 1, 5])("accepts a row count of %s", (numRows) => {
    const result = schema.safeParse({ ...formValues, num_rows: numRows });

    expect(result.success).toBe(true);
  });

  it.each([0, -1, 2.5, NaN])("rejects a row count of %s", (numRows) => {
    const result = schema.safeParse({ ...formValues, num_rows: numRows });

    expect(result.success).toBe(false);
    expect(result.error.issues.map((issue) => issue.path)).toEqual([
      ["num_rows"],
    ]);
  });
});

describe("getAddRowsFromExistingPayload", () => {
  const columnMapping = { "source-1": "target-1" };

  it("sends the row count when one is entered", () => {
    expect(
      getAddRowsFromExistingPayload({
        sourceDatasetId: "source-dataset",
        columnMapping,
        numRows: 5,
      }),
    ).toEqual({
      source_dataset_id: "source-dataset",
      column_mapping: columnMapping,
      num_rows: 5,
    });
  });

  it.each(["", undefined, null])(
    "leaves num_rows out when the row count is %s",
    (numRows) => {
      expect(
        getAddRowsFromExistingPayload({
          sourceDatasetId: "source-dataset",
          columnMapping,
          numRows,
        }),
      ).toEqual({
        source_dataset_id: "source-dataset",
        column_mapping: columnMapping,
      });
    },
  );
});

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
