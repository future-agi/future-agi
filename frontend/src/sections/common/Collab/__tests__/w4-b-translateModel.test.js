// W4-8 (Lane B): the section translate menu and block copy.
import { describe, expect, it } from "vitest";
import {
  otherLanguagesLine,
  sectionFields,
  sectionLanguage,
  sectionTargets,
  translationStatus,
  translationTitle,
} from "../translateModel";

const languages = [
  { code: "en", name: "English" },
  { code: "es", name: "Spanish" },
  { code: "fr", name: "French" },
  { code: "de", name: "German" },
  { code: "hi", name: "Hindi" },
];

describe("translateModel", () => {
  it("offers what any field of the section offers, English first for non-English", () => {
    const fields = [
      {
        column_id: "c1",
        name: "customer_message",
        language: "es",
        available: ["en"],
      },
      {
        column_id: "c3",
        name: "expected_reply",
        language: "es",
        available: ["en"],
      },
      { column_id: "c9", name: "other", language: null, available: [] },
    ];
    expect(
      sectionFields(fields, ["customer_message"]).map((f) => f.column_id),
    ).toEqual(["c1"]);
    expect(sectionLanguage(fields)).toBe("es");
    expect(sectionTargets(fields, languages)).toEqual([
      { code: "en", name: "English", fields: ["c1", "c3"] },
    ]);
    const en = [
      {
        column_id: "c1",
        name: "m",
        language: "en",
        available: ["hi", "es", "de"],
      },
    ];
    expect(sectionTargets(en, languages).map((t) => t.code)).toEqual([
      "es",
      "de",
      "hi",
    ]);
    expect(sectionTargets([], languages)).toEqual([]);
  });

  it("writes the block copy", () => {
    expect(
      translationTitle(
        { name: "customer_message", source_language: "es" },
        { languages, target: "en" },
      ),
    ).toBe("customer_message · Spanish → English");
    expect(translationStatus("gpt-4o-mini", false)).toBe(
      "Machine translation · gpt-4o-mini · not saved",
    );
    expect(translationStatus("gpt-4o-mini", true)).toBe(
      "Machine translation · gpt-4o-mini · cached",
    );
    expect(
      otherLanguagesLine({
        dataset_language_name: "English",
        other_language_rows: { count: 47 },
      }),
    ).toBe("This dataset is mostly English. 47 rows are in other languages");
    expect(
      otherLanguagesLine({
        dataset_language_name: "English",
        other_language_rows: { count: 1 },
      }),
    ).toBe("This dataset is mostly English. 1 row is in other languages");
    expect(
      otherLanguagesLine({
        dataset_language_name: "English",
        other_language_rows: { count: 0 },
      }),
    ).toBe("");
  });
});
