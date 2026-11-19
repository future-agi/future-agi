import { describe, expect, it } from "vitest";

import {
  getAnnotationDefinition,
  getAttributesDefinition,
} from "../prototypeObserveUtils";
import { AnnotationLabelTypes } from "../constants";

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
        registryId: "custom_attribute:mixed.value",
        filterType: { type: "text" },
        asyncOptions: true,
        attributeTypes: ["number", "string", "boolean"],
        attributeTypesExact: false,
      }),
    );
  });

  it("uses a catalog registry id without replacing the native attribute key", () => {
    const [group] = getAttributesDefinition([
      {
        key: "model",
        type: "string",
        property_id: "custom_attribute:model",
      },
    ]);

    expect(group.dependents[0]).toMatchObject({
      propertyId: "model",
      registryId: "custom_attribute:model",
    });
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

describe("getAnnotationDefinition", () => {
  it("keeps the annotation UUID native while retaining its registry identity", () => {
    const definition = getAnnotationDefinition({
      id: "annotation-label-id",
      name: "Quality",
      property_id: "annotation:annotation-label-id",
      annotationLabelType: AnnotationLabelTypes.CATEGORICAL,
      settings: { options: [{ label: "Helpful" }] },
    });

    expect(definition).toMatchObject({
      propertyId: "annotation-label-id",
      registryId: "annotation:annotation-label-id",
    });
    expect(definition.dependents[0]).toMatchObject({
      propertyId: "annotation-label-id**Helpful",
      registryId: "annotation:annotation-label-id",
    });
  });
});
