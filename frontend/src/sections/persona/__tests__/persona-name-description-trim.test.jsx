import { describe, expect, it } from "vitest";

import { PersonCreateValidationSchema } from "../PersonaCreateEdit/common";

// What the create form submits for a single-language text persona, with every
// field other than name and description left at its default.
const basePersona = {
  simulationType: "text",
  multilingual: false,
  language: "en",
  name: "test",
  description: "A patient caller",
  gender: [],
  ageGroup: [],
  location: [],
  profession: [],
  personality: [],
  communicationStyle: [],
  accent: [],
  conversationSpeed: [],
  backgroundSound: null,
  finishedSpeakingSensitivity: 1,
  interruptSensitivity: 1,
  customProperties: [],
  additionalInstruction: null,
  tone: "",
  verbosity: "",
  punctuation: "",
  typosFrequency: "",
  slangUsage: "",
  regionalMix: "",
  emojiUsage: "",
};

function messagesFor(result, field) {
  return result.error.issues
    .filter((issue) => issue.path.join(".") === field)
    .map((issue) => issue.message);
}

describe("PersonCreateValidationSchema — name and description whitespace (#1389)", () => {
  it("trims surrounding spaces from the name", () => {
    const result = PersonCreateValidationSchema.safeParse({
      ...basePersona,
      name: " test ",
    });

    expect(result.success).toBe(true);
    expect(result.data.name).toBe("test");
  });

  it("keeps spaces inside the name", () => {
    const result = PersonCreateValidationSchema.safeParse({
      ...basePersona,
      name: "hello   world",
    });

    expect(result.success).toBe(true);
    expect(result.data.name).toBe("hello   world");
  });

  it("rejects a name that is only spaces", () => {
    const result = PersonCreateValidationSchema.safeParse({
      ...basePersona,
      name: "   ",
    });

    expect(result.success).toBe(false);
    expect(messagesFor(result, "name")).toContain("Name is required");
  });

  it("trims surrounding spaces from the description", () => {
    const result = PersonCreateValidationSchema.safeParse({
      ...basePersona,
      description: "  hello  ",
    });

    expect(result.success).toBe(true);
    expect(result.data.description).toBe("hello");
  });

  it("rejects a description that is only spaces", () => {
    const result = PersonCreateValidationSchema.safeParse({
      ...basePersona,
      description: "   ",
    });

    expect(result.success).toBe(false);
    expect(messagesFor(result, "description")).toContain(
      "Description is required",
    );
  });
});
