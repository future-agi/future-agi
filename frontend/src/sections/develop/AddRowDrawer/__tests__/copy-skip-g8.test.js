// v2.1 g8 (rows:copy-from-dataset, proof walk #5): "Skip" can be picked for
// any number of source fields without tripping the duplicate-name check.
import { describe, expect, it } from "vitest";
import { existingDatasetValidationSchema, SKIP_FIELD } from "../validation";

const form = (mapping) => ({
  name: "",
  value: "v",
  dataType: "text",
  config: { mapping },
});
const all = (mapping) =>
  Object.fromEntries(Object.keys(mapping).map((k) => [k, true]));
const messages = (r) => (r.success ? [] : r.error.issues.map((i) => i.message));

describe("copy from dataset: Skip per field", () => {
  it("accepts three skipped fields next to a mapped one", () => {
    const mapping = {
      question: "col-q",
      citation: SKIP_FIELD,
      policy_section: SKIP_FIELD,
      topic: SKIP_FIELD,
    };
    const r = existingDatasetValidationSchema("ds-1", all(mapping)).safeParse(
      form(mapping),
    );
    expect(messages(r)).toEqual([]);
    expect(r.success).toBe(true);
  });

  it("still flags two real fields mapped to the same column", () => {
    const mapping = { question: "col-q", answer: "col-q", topic: SKIP_FIELD };
    const r = existingDatasetValidationSchema("ds-1", all(mapping)).safeParse(
      form(mapping),
    );
    expect(r.success).toBe(false);
    const paths = r.error.issues.map((i) => i.path.at(-1));
    expect(paths).toEqual(expect.arrayContaining(["question", "answer"]));
    expect(paths).not.toContain("topic");
  });

  it("still requires a ticked field to be mapped or skipped", () => {
    const mapping = { question: "", topic: SKIP_FIELD };
    const r = existingDatasetValidationSchema("ds-1", all(mapping)).safeParse(
      form(mapping),
    );
    expect(messages(r)).toContain("This field is required");
  });
});
