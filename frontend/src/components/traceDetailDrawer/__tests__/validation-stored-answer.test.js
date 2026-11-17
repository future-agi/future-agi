// The trace annotate form accepts a text answer left as it was saved, even when
// the label's limits changed since; a changed answer is checked as before.
import { describe, expect, it } from "vitest";
import { createFormSchema } from "../validation/validation";

const LABELS = [
  {
    id: "comment",
    name: "Comment",
    type: "text",
    settings: { minLength: 50, maxLength: 1000 },
  },
];
const SAVED = { comment: "short note", notes: "" };

describe("trace annotate form: a text answer saved before its limits changed", () => {
  it("accepts the saved answer unchanged", () => {
    const schema = createFormSchema(LABELS, SAVED);
    expect(schema.safeParse({ comment: "short note", notes: "" }).success).toBe(
      true,
    );
  });

  it("refuses the answer changed to another text that breaks the limits", () => {
    const schema = createFormSchema(LABELS, SAVED);
    const result = schema.safeParse({
      comment: "still far too short.",
      notes: "",
    });
    expect(result.success).toBe(false);
    expect(result.error.issues[0].path).toEqual(["comment"]);
  });

  it("still refuses emptying a saved answer", () => {
    const schema = createFormSchema(LABELS, SAVED);
    expect(schema.safeParse({ comment: "", notes: "" }).success).toBe(false);
  });

  it("checks a first answer against the limits", () => {
    const schema = createFormSchema(LABELS, { notes: "" });
    expect(schema.safeParse({ comment: "short note", notes: "" }).success).toBe(
      false,
    );
  });
});
