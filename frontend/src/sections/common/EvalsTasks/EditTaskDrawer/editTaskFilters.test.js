import { describe, expect, it } from "vitest";

import { buildEvalTaskEditFilters } from "./editTaskFilters";

describe("buildEvalTaskEditFilters", () => {
  it("persists edited attributes under the canonical nested filters key", () => {
    const payload = buildEvalTaskEditFilters(
      {
        project: "11111111-1111-4111-8111-111111111111",
        filters: [
          {
            property: "attributes",
            propertyId: "prompt_slug",
            filterConfig: {
              filterType: "text",
              filterOp: "in",
              filterValue: ["synthetic_prompt_v2"],
            },
          },
        ],
      },
      "2026-01-01T00:00:00.000Z",
      "2026-06-30T23:59:59.999Z",
    );

    expect(payload.filters).toEqual([
      {
        column_id: "prompt_slug",
        filter_config: {
          col_type: "SPAN_ATTRIBUTE",
          filter_type: "text",
          filter_op: "in",
          filter_value: ["synthetic_prompt_v2"],
        },
      },
    ]);
    expect(payload).not.toHaveProperty("span_attributes_filters");
  });
});
