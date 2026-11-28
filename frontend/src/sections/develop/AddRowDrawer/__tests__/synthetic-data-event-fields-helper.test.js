import { describe, expect, it } from "vitest";
import {
  syntheticDataColumnEventFields,
  syntheticDataEventFields,
} from "../syntheticDataEventFields";

const MARK = "SYNTHETIC_DATASET_TEXT_e512";

const payload = {
  dataset: {
    name: `Dataset ${MARK}`,
    description: `Description ${MARK}`,
    objective: `Use case ${MARK}`,
    patterns: "   ",
  },
  num_rows: 30,
  kb_id: "kb-9",
  columns: [
    {
      id: "column-1",
      name: `city_${MARK}`,
      data_type: "text",
      description: `Column description ${MARK}`,
      property: { min_length: 2, [`Region ${MARK}`]: MARK },
      is_new: true,
      skip: true,
    },
    {
      name: `custom_${MARK}`,
      data_type: `kind ${MARK}`,
      property: {},
    },
  ],
};

describe("syntheticDataEventFields", () => {
  it("sends counts, types, flags and ids only", () => {
    const fields = syntheticDataEventFields(payload, {
      editMode: true,
      datasetId: "dataset-1",
    });

    expect(fields).toEqual({
      row_count: 30,
      column_count: 2,
      column_types: ["text", "other"],
      property_count: 2,
      property_types: ["min_length", "custom"],
      knowledge_base_id: "kb-9",
      has_knowledge_base: true,
      has_use_case: true,
      has_patterns: false,
      edit_mode: true,
      dataset_id: "dataset-1",
    });
    expect(JSON.stringify(fields)).not.toContain(MARK);
  });

  it("reads the property list form of a column", () => {
    expect(
      syntheticDataColumnEventFields([
        {
          name: `a_${MARK}`,
          data_type: "float",
          property: [
            { type: "max_length", value: "9" },
            { type: `Mood ${MARK}`, value: MARK },
            { type: "", value: "" },
          ],
        },
      ]),
    ).toEqual([
      { Column_Type: "float", Property_Types: ["max_length", "custom"] },
    ]);
  });

  it("handles a missing payload", () => {
    expect(syntheticDataEventFields(undefined)).toEqual({
      row_count: 0,
      column_count: 0,
      column_types: [],
      property_count: 0,
      property_types: [],
      has_knowledge_base: false,
      has_use_case: false,
      has_patterns: false,
      edit_mode: false,
    });
    expect(syntheticDataColumnEventFields(undefined)).toEqual([]);
  });
});
