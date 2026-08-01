import { describe, expect, it } from "vitest";

import { getAttributesDefinition } from "../prototypeObserveUtils";

describe("getAttributesDefinition", () => {
  it("keeps discovered types and restores saved keys missing from a bounded sample", () => {
    const [definition] = getAttributesDefinition(
      [{ key: "final_status", type: "string" }],
      [
        {
          column_id: "custom.score",
          filter_config: { filter_type: "number" },
          _meta: { parentProperty: "Attribute" },
        },
      ],
    );

    expect(definition.allowCustomDependent).toBe(true);
    expect(definition.dependents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          propertyId: "final_status",
          filterType: { type: "text" },
        }),
        expect.objectContaining({
          propertyId: "custom.score",
          filterType: { type: "number" },
        }),
      ]),
    );
  });
});
