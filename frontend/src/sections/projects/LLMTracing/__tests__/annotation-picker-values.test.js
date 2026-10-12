import { describe, it, expect } from "vitest";
import {
  getPickerOptionLabel,
  getPickerOptionValue,
  getPickerValueIdentity,
  normalizePickerValues,
} from "../filterValuePickerUtils";

describe("OQA-10 exact stored annotation picker values", () => {
  it.each(["True ", " True", " True "])(
    "preserves stored %j only on opt-in without changing its label",
    (value) => {
      const option = { value, label: "True", type: "string" };
      expect(getPickerOptionValue(value, { preserveWhitespace: true })).toBe(
        value,
      );
      expect(getPickerOptionValue(option, { preserveWhitespace: true })).toBe(
        value,
      );
      expect(getPickerOptionLabel(option)).toBe("True");
      expect(getPickerOptionValue(option)).toBe("True");
    },
  );

  it("keeps whitespace-distinct stored choices and scalar types separate", () => {
    const values = [
      "True",
      "True ",
      " True",
      " ",
      "True ",
      "false",
      false,
      0,
      "0",
      null,
      Number.NaN,
    ];
    expect(normalizePickerValues(values, { preserveWhitespace: true })).toEqual(
      ["True", "True ", " True", " ", "false", false, 0, "0"],
    );
    expect(normalizePickerValues(values)).toEqual([
      "True",
      "false",
      false,
      0,
      "0",
    ]);
    expect(getPickerValueIdentity("True", "string")).not.toBe(
      getPickerValueIdentity("True ", "string"),
    );
    expect(normalizePickerValues(["  typed value  "])).toEqual(["typed value"]);
  });
});
