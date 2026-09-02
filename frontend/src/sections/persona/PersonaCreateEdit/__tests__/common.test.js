import { describe, expect, it } from "vitest";
import { PersonCreateValidationSchema } from "../common";

// A fully valid, single-language persona payload. Individual tests override
// one field to isolate the behavioural-settings requirement.
const validPersona = (overrides = {}) => ({
  multilingual: false,
  language: "en",
  simulationType: "text",
  name: "Test Persona",
  description: "A test persona",
  gender: [],
  ageGroup: [],
  location: [],
  profession: [],
  personality: [{ value: "friendly" }],
  communicationStyle: ["formal"],
  accent: [],
  customProperties: [],
  additionalInstruction: null,
  ...overrides,
});

describe("PersonCreateValidationSchema behavioural settings", () => {
  it("rejects a persona with no personality selected", () => {
    const result = PersonCreateValidationSchema.safeParse(
      validPersona({ personality: [] }),
    );

    // Pre-fix this parsed successfully (empty array transformed to null).
    expect(result.success).toBe(false);
    expect(JSON.stringify(result.error.issues)).toContain(
      "Select at least one personality",
    );
  });

  it("rejects a persona with no communication style selected", () => {
    const result = PersonCreateValidationSchema.safeParse(
      validPersona({ communicationStyle: [] }),
    );

    expect(result.success).toBe(false);
    expect(JSON.stringify(result.error.issues)).toContain(
      "Select at least one communication style",
    );
  });

  it("accepts a persona with at least one personality and communication style", () => {
    const result = PersonCreateValidationSchema.safeParse(validPersona());

    expect(result.success).toBe(true);
  });
});
