import { describe, it, expect } from "vitest";
import {
  getPickerOptionLabel,
  getPickerOptionSearchText,
  getPickerOptionSecondaryLabel,
  getPickerOptionType,
  getPickerOptionValue,
  getPickerValueIdentity,
  usesFreeTextValue,
} from "../filterValuePickerUtils";

describe("OBS007 typed scalar labels", () => {
  it.each([["7", 7, "number"], ["0", 0, "number"], ["false", false, "boolean"]])(
    "distinguishes %s from its %s scalar without changing value or search identity",
    (text, scalar, type) => {
      const options = [
        { value: text, label: text, type: "string" },
        { value: scalar, label: text, type },
      ];
      expect(options.map((option) => getPickerOptionSecondaryLabel(option, { showType: true })))
        .toEqual(["string", type]);
      expect(options.map(getPickerOptionLabel)).toEqual([text, text]);
      expect(options.map(getPickerOptionValue)).toEqual([text, scalar]);
      expect(options.map(getPickerOptionSearchText)).toEqual([`${text} ${text}`, `${text} ${text}`]);
      const identities = options.map((option) =>
        getPickerValueIdentity(getPickerOptionValue(option), getPickerOptionType(option)));
      expect(new Set(identities).size).toBe(2);
    },
  );

  it("keeps native descriptions unchanged and adds only supported storage types on opt-in", () => {
    const option = { value: "7", label: "7", description: "external identifier", type: "string" };
    expect(getPickerOptionSecondaryLabel(option)).toBe("external identifier");
    expect(getPickerOptionSecondaryLabel(option, { showType: true })).toBe("external identifier · string");
    expect(getPickerOptionSecondaryLabel({ value: "7", type: "unsupported" }, { showType: true })).toBe("");
    expect(getPickerOptionSecondaryLabel("7", { showType: true })).toBe("");
  });
});

describe("usesFreeTextValue — text vs values-picker decision", () => {
  it("text fields always use the free-text input", () => {
    expect(usesFreeTextValue("text", "traces")).toBe(true);
    expect(usesFreeTextValue("text", "dataset")).toBe(true);
  });

  it("string fields use the picker in Observe sources", () => {
    for (const source of ["traces", "sessions", "users"]) {
      expect(usesFreeTextValue("string", source)).toBe(false);
    }
  });

  it("string fields stay free text in non-Observe sources", () => {
    for (const source of ["dataset", "simulation", "experiment"]) {
      expect(usesFreeTextValue("string", source)).toBe(true);
    }
  });
});
