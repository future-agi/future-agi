import { describe, expect, it } from "vitest";
import {
  buildCustomPropertyPayload,
  getDefaultValueError,
} from "./customPropertyForm";

describe("CreateEditPropertyDialog helpers", () => {
  it("rejects defaults that do not match the selected property type", () => {
    expect(
      getDefaultValueError({
        property_type: "number",
        allowed_values: [],
        default_value: "abc",
      }),
    ).toBe("Default must be a number");

    expect(
      getDefaultValueError({
        property_type: "boolean",
        allowed_values: [],
        default_value: "yes",
      }),
    ).toBe("Default must be true or false");

    expect(
      getDefaultValueError({
        property_type: "enum",
        allowed_values: ["alpha", "beta"],
        default_value: "gamma",
      }),
    ).toBe("Default must match an allowed value");
  });

  it("coerces valid defaults before submitting to the API", () => {
    expect(
      buildCustomPropertyPayload({
        name: "priority",
        description: "",
        property_type: "number",
        required: false,
        allowed_values: ["ignored"],
        default_value: "42",
      }),
    ).toMatchObject({
      allowed_values: [],
      default_value: 42,
    });

    expect(
      buildCustomPropertyPayload({
        name: "flag",
        description: "",
        property_type: "boolean",
        required: false,
        allowed_values: [],
        default_value: " false ",
      }),
    ).toMatchObject({
      default_value: false,
    });
  });
});
