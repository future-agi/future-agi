import { describe, expect, it } from "vitest";
import {
  buildCustomPropertyPayload,
  hasStoredDefault,
} from "./customPropertyForm";

describe("CreateEditPropertyDialog helpers", () => {
  // A Default is not applied anywhere: the payload never carries one, so the
  // API keeps a stored default as it is unless the user removes it.
  it("sends no default unless the user removes a stored one", () => {
    const form = {
      name: "priority",
      description: "",
      property_type: "number",
      required: false,
      allowed_values: ["ignored"],
    };
    const payload = buildCustomPropertyPayload(form);
    expect(payload).toEqual({
      name: "priority",
      description: "",
      property_type: "number",
      required: false,
      allowed_values: [],
    });
    expect(
      buildCustomPropertyPayload({ ...form, clearStoredDefault: true }),
    ).toMatchObject({ default_value: null });
  });

  it("keeps Required and the allowed values of an enum", () => {
    expect(
      buildCustomPropertyPayload({
        name: "tier",
        description: "",
        property_type: "enum",
        required: true,
        allowed_values: ["gold", "silver"],
      }),
    ).toMatchObject({ required: true, allowed_values: ["gold", "silver"] });
  });

  it("knows a stored default, including false and 0", () => {
    expect(hasStoredDefault({ default_value: "us" })).toBe(true);
    expect(hasStoredDefault({ default_value: false })).toBe(true);
    expect(hasStoredDefault({ default_value: 0 })).toBe(true);
    expect(hasStoredDefault({ default_value: null })).toBe(false);
    expect(hasStoredDefault({})).toBe(false);
  });
});
