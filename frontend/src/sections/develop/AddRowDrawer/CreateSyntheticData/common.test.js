import { describe, expect, it } from "vitest";
import { getSyntheticDefaultValues } from "./common";

describe("getSyntheticDefaultValues", () => {
  it("normalizes canonical snake_case synthetic config responses", () => {
    expect(
      getSyntheticDefaultValues({
        dataset: {
          name: "Synthetic QA",
          description: "Synthetic description",
          objective: "Synthetic objective",
          patterns: "Synthetic pattern",
        },
        kb_id: "kb-snake",
        num_rows: 12,
        columns: [
          {
            name: "answer",
            data_type: "Text",
            description: "Generated answer",
            property: {
              min_length: 4,
              max_length: 20,
            },
          },
        ],
      }),
    ).toEqual({
      name: "Synthetic QA",
      description: "Synthetic description",
      kb_id: "kb-snake",
      useCase: "Synthetic objective",
      pattern: "Synthetic pattern",
      rowNumber: 12,
      columns: [
        {
          name: "answer",
          data_type: "Text",
          description: "Generated answer",
          property: [
            { type: "min_length", value: 4 },
            { type: "max_length", value: 20 },
          ],
        },
      ],
    });
  });

  it("keeps legacy camelCase fallbacks", () => {
    expect(
      getSyntheticDefaultValues({
        dataset: { name: "Legacy", description: "Legacy description" },
        kbId: "kb-camel",
        numRows: 10,
        columns: [{ name: "legacy", dataType: "Text", property: {} }],
      }),
    ).toMatchObject({
      kb_id: "kb-camel",
      rowNumber: 10,
      columns: [{ name: "legacy", data_type: "Text", property: [] }],
    });
  });
});
