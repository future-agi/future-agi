import { describe, expect, it } from "vitest";
import {
  getPickerOptionValue,
  getPickerValueIdentity,
  normalizePickerValues,
} from "../filterValuePickerUtils";

describe("OBS-SW-58 exact retained string values", () => {
  it.each([
    ["leading spaces", "  text"],
    ["trailing spaces", "text  "],
    ["leading and trailing whitespace", "\t text \r\n"],
    ["a multiline prompt", "Summarize this.\n"],
    ["a whitespace-only string", " \t\n"],
  ])("preserves %s at the option boundary", (_description, value) => {
    expect(getPickerOptionValue(value)).toBe(value);
    expect(
      getPickerOptionValue({ value, label: "Display label", type: "string" }),
    ).toBe(value);
  });

  it("preserves whitespace in a label-only option value", () => {
    expect(getPickerOptionValue({ label: "  label\n" })).toBe("  label\n");
  });

  it("preserves whitespace when normalizing scalar and retained values", () => {
    const value = " \tSummarize this.\n";
    expect(normalizePickerValues(value)).toEqual([value]);
    expect(normalizePickerValues({ value, type: "string" })).toEqual([value]);
    expect(normalizePickerValues([value])).toEqual([value]);
    expect(normalizePickerValues([{ value, type: "string" }])).toEqual([value]);
    expect(normalizePickerValues([" \t\n"])).toEqual([" \t\n"]);
  });

  it("deduplicates only identical scalars without merging whitespace variants", () => {
    expect(
      normalizePickerValues([
        "Summarize this.\n",
        "Summarize this.",
        " Summarize this.",
        { value: "Summarize this.\n", label: "Duplicate", type: "string" },
        { value: " Summarize this.", type: "string" },
        "Summarize this.",
        0,
        "0",
        false,
        "false",
        { value: 0 },
        { value: false },
        null,
        undefined,
        "",
        Number.NaN,
        Number.POSITIVE_INFINITY,
        {},
        { value: [] },
        { value: {} },
      ]),
    ).toEqual([
      "Summarize this.\n",
      "Summarize this.",
      " Summarize this.",
      0,
      "0",
      false,
      "false",
    ]);
  });

  it("keeps whitespace-distinct catalog identities even with identical labels", () => {
    const values = ["True", "True ", " True", "\tTrue\n"];
    const identities = values.map((value) =>
      getPickerValueIdentity(
        getPickerOptionValue({ value, label: "True", type: "string" }),
        "string",
      ),
    );
    expect(new Set(identities).size).toBe(values.length);
  });
});
