import { describe, expect, it } from "vitest";
import { catalogEntry } from "../index";
import { formValuesFor, parseTopicList } from "../form-model";
import { mapEditForSave, ruleBodyForSave, storedActionOf } from "../rule-model";

describe("Stored check display is not a rewrite", () => {
  it.each([
    [
      "keyword-blocklist",
      { words: ["alpha,beta", " keep spaces "], unrelated: { a: 1 } },
    ],
    [
      "topic-restriction",
      { forbidden_topics: [{ name: "billing", keywords: ["alpha,beta"] }] },
    ],
    ["language-detection", { allowed_scripts: ["LATIN"], unrelated: [2, 1] }],
  ])(
    "keeps %s config byte-exact on no-op and action-only edits",
    (catalogName, config) => {
      const meta = catalogEntry(catalogName);
      const check = {
        enabled: true,
        action: "block",
        confidence_threshold: 0.8,
        config,
      };
      const form = formValuesFor(
        meta,
        check,
        (value) => storedActionOf(value, { asMap: true }),
        0,
      );
      expect(mapEditForSave({ check, meta, catalogName, form })).toBeNull();
      expect(
        mapEditForSave({
          check,
          meta,
          catalogName,
          form: { ...form, action: "warn" },
        }),
      ).toEqual({
        name: catalogName,
        config: { action: "warn" },
      });
      const rule = {
        name: catalogName,
        enabled: true,
        mode: "sync",
        action: "block",
        threshold: 0.8,
        config,
      };
      const saved = { rules: [rule] };
      const body = ruleBodyForSave({
        saved,
        check,
        meta,
        catalogName,
        form: { ...form, action: "warn" },
      });
      expect(JSON.stringify(body.config.config)).toBe(JSON.stringify(config));
      expect(body.config.action).toBe("warn");
      expect(body.config).not.toHaveProperty("stage");
    },
  );

  it("rewrites only an edited field, retaining a noncanonical stored number", () => {
    const meta = {
      fields: [
        { key: "words", type: "multiline-list" },
        { key: "max", type: "number" },
      ],
    };
    const check = {
      enabled: true,
      action: "block",
      config: { words: ["alpha,beta"], max: "003", hidden: true },
    };
    const form = formValuesFor(
      meta,
      check,
      (value) => storedActionOf(value, { asMap: true }),
      0,
    );
    form.fields.words = "changed";
    expect(
      mapEditForSave({ check, meta, catalogName: "custom", form }).config
        .config,
    ).toEqual({
      words: ["changed"],
      max: "003",
      hidden: true,
    });
  });

  it("keeps named empty and bare topics and rejects nameless lines", () => {
    expect(
      parseTopicList("billing:\n: invoice\ngambling\nLegal: lawsuit, attorney"),
    ).toEqual([
      { name: "billing", keywords: ["billing"] },
      { name: "gambling", keywords: ["gambling"] },
      { name: "Legal", keywords: ["lawsuit", "attorney"] },
    ]);
  });
});
