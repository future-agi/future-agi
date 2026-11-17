import { describe, it, expect } from "vitest";
import { AddRowUsingAiValidationSchema } from "../validation";

// Messages the "Add rows using AI" form shows under its row count.
const numRowsErrors = (numRows) => {
  const result = AddRowUsingAiValidationSchema.safeParse({
    description: "Callers who ask to reschedule",
    numRows,
  });
  if (result.success) return [];
  return result.error.issues
    .filter((issue) => issue.path[0] === "numRows")
    .map((issue) => issue.message);
};

describe("Add rows using AI: row count validation", () => {
  it("asks for at least 10 rows in plain words", () => {
    expect(numRowsErrors(3)).toEqual(["Enter at least 10 rows"]);
  });

  it("asks for 20,000 rows or fewer in plain words", () => {
    expect(numRowsErrors(20001)).toEqual(["Enter 20,000 rows or fewer"]);
  });

  // Guard: the rule itself is unchanged (holds at the base too).
  it("still accepts 10 and 20,000", () => {
    expect(numRowsErrors(10)).toEqual([]);
    expect(numRowsErrors(20000)).toEqual([]);
  });
});
