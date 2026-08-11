import { describe, expect, it } from "vitest";

import { getAttributesDefinition } from "../prototypeObserveUtils";

describe("getAttributesDefinition", () => {
  it("routes mixed scalar storage families through typed autocomplete", () => {
    const [group] = getAttributesDefinition([
      {
        key: "mixed.value",
        type: "number",
        types: ["number", "string", "boolean", "number"],
      },
    ]);

    expect(group.dependents[0]).toEqual(
      expect.objectContaining({
        propertyId: "mixed.value",
        filterType: { type: "text" },
        asyncOptions: true,
        attributeTypes: ["number", "string", "boolean"],
        attributeTypesExact: false,
      }),
    );
  });

  it("keeps exact singleton numeric attributes on the numeric editor", () => {
    const [group] = getAttributesDefinition([
      {
        key: "attempt",
        type: "number",
        types: ["number"],
        types_exact: true,
      },
    ]);

    expect(group.dependents[0]).toEqual(
      expect.objectContaining({
        filterType: { type: "number" },
        attributeTypes: ["number"],
        attributeTypesExact: true,
      }),
    );
    expect(group.dependents[0]).not.toHaveProperty("asyncOptions");
  });
});
