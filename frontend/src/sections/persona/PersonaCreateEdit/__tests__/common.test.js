import { describe, expect, it } from "vitest";
import { PersonCreateValidationSchema } from "../common";

const buildValidPersona = (overrides = {}) => ({
  multilingual: false,
  language: "English",
  simulationType: "voice",
  name: "Sample persona",
  description: "A realistic persona",
  gender: [],
  ageGroup: [],
  location: [],
  profession: [],
  personality: [{ value: "Friendly and cooperative" }],
  communicationStyle: ["Direct and concise"],
  accent: ["American"],
  conversationSpeed: [],
  backgroundSound: null,
  finishedSpeakingSensitivity: null,
  interruptSensitivity: null,
  customProperties: [],
  additionalInstruction: null,
  tone: "",
  verbosity: "",
  punctuation: "",
  typosFrequency: "",
  slangUsage: "",
  regionalMix: "",
  emojiUsage: "",
  ...overrides,
});

const findIssue = (result, path) =>
  result.error?.issues?.find((i) => i.path?.join(".") === path);

// ---------------------------------------------------------------------------
// Personality validation
// ---------------------------------------------------------------------------
describe("PersonCreateValidationSchema – personality", () => {
  it("rejects when personality is empty (voice persona)", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({ personality: [] }),
    );

    expect(result.success).toBe(false);
    const issue = findIssue(result, "personality");
    expect(issue).toBeDefined();
    expect(issue.message).toBe("At least one personality trait is required");
  });

  it("rejects when personality is empty (text persona)", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        simulationType: "text",
        personality: [],
        accent: [],
      }),
    );

    expect(result.success).toBe(false);
    const issue = findIssue(result, "personality");
    expect(issue).toBeDefined();
    expect(issue.message).toBe("At least one personality trait is required");
  });

  it("accepts when personality has a single trait", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        personality: [{ value: "Confident" }],
      }),
    );

    expect(result.success).toBe(true);
    expect(result.data.personality).toEqual(["Confident"]);
  });

  it("accepts when personality has multiple traits", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        personality: [
          { value: "Confident" },
          { value: "Analytical" },
          { value: "Friendly and cooperative" },
        ],
      }),
    );

    expect(result.success).toBe(true);
    expect(result.data.personality).toEqual([
      "Confident",
      "Analytical",
      "Friendly and cooperative",
    ]);
  });
});

// ---------------------------------------------------------------------------
// Communication style validation
// ---------------------------------------------------------------------------
describe("PersonCreateValidationSchema – communication style", () => {
  it("rejects when communicationStyle is empty (voice persona)", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({ communicationStyle: [] }),
    );

    expect(result.success).toBe(false);
    const issue = findIssue(result, "communicationStyle");
    expect(issue).toBeDefined();
    expect(issue.message).toBe("Communication style is required");
  });

  it("rejects when communicationStyle is empty (text persona)", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        simulationType: "text",
        communicationStyle: [],
        accent: [],
      }),
    );

    expect(result.success).toBe(false);
    const issue = findIssue(result, "communicationStyle");
    expect(issue).toBeDefined();
    expect(issue.message).toBe("Communication style is required");
  });

  it("accepts when communicationStyle has a single value", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        communicationStyle: ["Casual and friendly"],
      }),
    );

    expect(result.success).toBe(true);
    expect(result.data.communicationStyle).toEqual(["Casual and friendly"]);
  });

  it("accepts when communicationStyle has multiple values", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        communicationStyle: ["Direct and concise", "Technical"],
      }),
    );

    expect(result.success).toBe(true);
    expect(result.data.communicationStyle).toEqual([
      "Direct and concise",
      "Technical",
    ]);
  });
});

// ---------------------------------------------------------------------------
// Accent validation (voice-only)
// ---------------------------------------------------------------------------
describe("PersonCreateValidationSchema – accent", () => {
  it("rejects when accent is empty for a voice persona", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({ accent: [] }),
    );

    expect(result.success).toBe(false);
    const issue = findIssue(result, "accent");
    expect(issue).toBeDefined();
    expect(issue.message).toBe("Accent is required");
  });

  it("accepts when accent is empty for a text persona", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        simulationType: "text",
        accent: [],
      }),
    );

    expect(result.success).toBe(true);
    expect(result.data.accent).toBeNull();
  });

  it("accepts when accent is an empty array for a text persona", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        simulationType: "text",
        accent: [],
      }),
    );

    expect(result.success).toBe(true);
  });

  it("treats accent=null as a type error since accent must be an array", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        simulationType: "text",
        accent: null,
      }),
    );

    // null is not an array, so Zod rejects it with a type error
    expect(result.success).toBe(false);
  });

  it("accepts when accent has a single value for voice", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        accent: ["British"],
      }),
    );

    expect(result.success).toBe(true);
    expect(result.data.accent).toEqual(["British"]);
  });
});

// ---------------------------------------------------------------------------
// Combined validation — all three fields empty
// ---------------------------------------------------------------------------
describe("PersonCreateValidationSchema – all fields empty", () => {
  it("rejects with three errors when all behavioural fields are empty (voice)", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        personality: [],
        communicationStyle: [],
        accent: [],
      }),
    );

    expect(result.success).toBe(false);
    const paths = result.error.issues.map((i) => i.path?.join("."));
    expect(paths).toContain("personality");
    expect(paths).toContain("communicationStyle");
    expect(paths).toContain("accent");
  });

  it("rejects with two errors when all behavioural fields are empty (text)", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        simulationType: "text",
        personality: [],
        communicationStyle: [],
        accent: [],
      }),
    );

    expect(result.success).toBe(false);
    const paths = result.error.issues.map((i) => i.path?.join("."));
    expect(paths).toContain("personality");
    expect(paths).toContain("communicationStyle");
    // Accent should not appear for text personas
    expect(paths).not.toContain("accent");
  });
});

// ---------------------------------------------------------------------------
// Happy-path: fully filled forms
// ---------------------------------------------------------------------------
describe("PersonCreateValidationSchema – fully filled", () => {
  it("accepts a fully filled voice persona", () => {
    const result = PersonCreateValidationSchema.safeParse(buildValidPersona());

    expect(result.success).toBe(true);
    expect(result.data.personality).toEqual(["Friendly and cooperative"]);
    expect(result.data.communicationStyle).toEqual(["Direct and concise"]);
    expect(result.data.accent).toEqual(["American"]);
    expect(result.data.customProperties).toEqual({});
  });

  it("accepts a fully filled text persona without accent", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        simulationType: "text",
        accent: [],
      }),
    );

    expect(result.success).toBe(true);
    expect(result.data.personality).toEqual(["Friendly and cooperative"]);
    expect(result.data.communicationStyle).toEqual(["Direct and concise"]);
    expect(result.data.accent).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Partial fills: ensure ALL required fields must be present
// ---------------------------------------------------------------------------
describe("PersonCreateValidationSchema – partial fills are rejected", () => {
  it("rejects when only personality is filled (voice)", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        personality: [{ value: "Confident" }],
        communicationStyle: [],
        accent: [],
      }),
    );

    expect(result.success).toBe(false);
    const paths = result.error.issues.map((i) => i.path?.join("."));
    expect(paths).toContain("communicationStyle");
    expect(paths).toContain("accent");
    expect(paths).not.toContain("personality");
  });

  it("rejects when only communicationStyle is filled (voice)", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        personality: [],
        communicationStyle: ["Direct and concise"],
        accent: [],
      }),
    );

    expect(result.success).toBe(false);
    const paths = result.error.issues.map((i) => i.path?.join("."));
    expect(paths).toContain("personality");
    expect(paths).toContain("accent");
    expect(paths).not.toContain("communicationStyle");
  });

  it("rejects when only accent is filled (voice)", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        personality: [],
        communicationStyle: [],
        accent: ["American"],
      }),
    );

    expect(result.success).toBe(false);
    const paths = result.error.issues.map((i) => i.path?.join("."));
    expect(paths).toContain("personality");
    expect(paths).toContain("communicationStyle");
    expect(paths).not.toContain("accent");
  });

  it("rejects when personality + accent filled but communicationStyle empty (voice)", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        personality: [{ value: "Confident" }],
        communicationStyle: [],
        accent: ["American"],
      }),
    );

    expect(result.success).toBe(false);
    const paths = result.error.issues.map((i) => i.path?.join("."));
    expect(paths).toContain("communicationStyle");
    expect(paths).not.toContain("personality");
    expect(paths).not.toContain("accent");
  });

  it("rejects when personality + communicationStyle filled but accent empty (voice)", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        personality: [{ value: "Confident" }],
        communicationStyle: ["Direct and concise"],
        accent: [],
      }),
    );

    expect(result.success).toBe(false);
    const paths = result.error.issues.map((i) => i.path?.join("."));
    expect(paths).toContain("accent");
    expect(paths).not.toContain("personality");
    expect(paths).not.toContain("communicationStyle");
  });

  it("accepts when personality + communicationStyle filled and accent empty (text)", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        simulationType: "text",
        personality: [{ value: "Confident" }],
        communicationStyle: ["Direct and concise"],
        accent: [],
      }),
    );

    expect(result.success).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Non-behavioural validations are unchanged
// ---------------------------------------------------------------------------
describe("PersonCreateValidationSchema – non-behavioural validations", () => {
  it("rejects when name is empty", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({ name: "" }),
    );

    expect(result.success).toBe(false);
    expect(result.error.issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          message: "Name is required",
          path: ["name"],
        }),
      ]),
    );
  });

  it("rejects when description is empty", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({ description: "" }),
    );

    expect(result.success).toBe(false);
    expect(result.error.issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          message: "Description is required",
          path: ["description"],
        }),
      ]),
    );
  });

  it("rejects when multilingual is true and language is empty", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        multilingual: true,
        language: [],
      }),
    );

    expect(result.success).toBe(false);
    expect(result.error.issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          message: "Language is required",
          path: ["language"],
        }),
      ]),
    );
  });

  it("transforms customProperties array into an object", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        customProperties: [
          { key: "department", value: "engineering" },
          { key: "region", value: "us-east" },
        ],
      }),
    );

    expect(result.success).toBe(true);
    expect(result.data.customProperties).toEqual({
      department: "engineering",
      region: "us-east",
    });
  });
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------
describe("PersonCreateValidationSchema – edge cases", () => {
  it("returns multiple errors when multiple fields are invalid", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        name: "",
        personality: [],
        communicationStyle: [],
        accent: [],
      }),
    );

    expect(result.success).toBe(false);
    const messages = result.error.issues.map((i) => i.message);
    expect(messages).toContain("Name is required");
    expect(messages).toContain("At least one personality trait is required");
    expect(messages).toContain("Communication style is required");
    expect(messages).toContain("Accent is required");
  });

  it("handles personality with empty object values gracefully", () => {
    const result = PersonCreateValidationSchema.safeParse(
      buildValidPersona({
        personality: [{}],
      }),
    );

    // {} doesn't have {value: string}, so z.object({value: z.string()}) fails
    expect(result.success).toBe(false);
  });
});
