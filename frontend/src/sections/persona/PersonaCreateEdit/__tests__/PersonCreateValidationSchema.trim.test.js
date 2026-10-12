import { describe, it, expect } from "vitest";
import { PersonCreateValidationSchema } from "../common";

// Regression guard for #1389: the persona name must be trimmed. An all-
// whitespace name is only rejected if .trim() runs before .min(1); without the
// fix, "   " (length 3) satisfies .min(1) and a blank-looking persona is saved.
const issueFields = (result) =>
  result.success
    ? []
    : result.error.issues.map((i) => i.path[i.path.length - 1]);

const basePersona = {
  multilingual: false,
  language: "en",
  simulationType: "text",
  description: "a description",
  gender: [],
  ageGroup: [],
  location: [],
  profession: [],
  personality: [],
  communicationStyle: [],
  accent: [],
};

describe("PersonCreateValidationSchema — trims name (#1389)", () => {
  it("rejects an all-whitespace name", () => {
    const result = PersonCreateValidationSchema.safeParse({
      ...basePersona,
      name: "   ",
    });
    expect(issueFields(result)).toContain("name");
  });

  it("does not flag a name that is valid once trimmed", () => {
    const result = PersonCreateValidationSchema.safeParse({
      ...basePersona,
      name: "  Ada  ",
    });
    expect(issueFields(result)).not.toContain("name");
  });
});
