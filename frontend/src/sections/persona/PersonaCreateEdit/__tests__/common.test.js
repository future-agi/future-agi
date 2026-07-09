import { describe, it, expect } from "vitest";
import { PersonCreateValidationSchema } from "../common";

// Regression tests for issue #1389:
// Persona name/description must be trimmed of leading/trailing whitespace and
// whitespace-only values must be rejected by the required check.
describe("PersonCreateValidationSchema whitespace handling", () => {
  const validPersona = {
    multilingual: false,
    language: "en",
    simulationType: "text",
    name: "test",
    description: "A description",
    gender: [],
    ageGroup: [],
    location: [],
    profession: [],
    personality: [],
    communicationStyle: [],
    accent: [],
    customProperties: [],
    additionalInstruction: null,
  };

  it("trims surrounding whitespace from name and description", async () => {
    const result = await PersonCreateValidationSchema.safeParseAsync({
      ...validPersona,
      name: "  test  ",
      description: "  A description  ",
    });
    expect(result.success).toBe(true);
    expect(result.data.name).toBe("test");
    expect(result.data.description).toBe("A description");
  });

  it("preserves interior spaces", async () => {
    const result = await PersonCreateValidationSchema.safeParseAsync({
      ...validPersona,
      name: "hello world",
    });
    expect(result.success).toBe(true);
    expect(result.data.name).toBe("hello world");
  });

  it("rejects whitespace-only name", async () => {
    const result = await PersonCreateValidationSchema.safeParseAsync({
      ...validPersona,
      name: "   ",
    });
    expect(result.success).toBe(false);
  });

  it("rejects whitespace-only description", async () => {
    const result = await PersonCreateValidationSchema.safeParseAsync({
      ...validPersona,
      description: "   ",
    });
    expect(result.success).toBe(false);
  });
});
