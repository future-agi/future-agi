import { describe, expect, it } from "vitest";
import { PersonCreateValidationSchema } from "./common";
import { AGENT_TYPES } from "src/sections/agents/constants";

const validVoicePersona = {
  simulationType: AGENT_TYPES.VOICE,
  name: "Support caller",
  description: "A sample persona for validation tests",
  gender: [],
  ageGroup: [],
  location: ["United States"],
  profession: [],
  personality: [{ value: "Friendly and cooperative" }],
  communicationStyle: ["Direct and concise"],
  accent: ["american"],
  conversationSpeed: [],
  backgroundSound: null,
  finishedSpeakingSensitivity: 1,
  interruptSensitivity: 1,
  customProperties: [],
  additionalInstruction: null,
  multilingual: false,
  language: "english",
  tone: "",
  verbosity: "",
  punctuation: "",
  typosFrequency: "",
  slangUsage: "",
  regionalMix: "",
  emojiUsage: "",
};

const errorMessagesFor = (payload) => {
  const result = PersonCreateValidationSchema.safeParse(payload);

  expect(result.success).toBe(false);
  return result.error.issues.map((issue) => issue.message);
};

describe("PersonCreateValidationSchema", () => {
  it("requires behavioural selections for voice personas", () => {
    const messages = errorMessagesFor({
      ...validVoicePersona,
      personality: [],
      communicationStyle: [],
      accent: [],
    });

    expect(messages).toEqual(
      expect.arrayContaining([
        "Personality is required",
        "Communication Style is required",
        "Accent is required",
      ]),
    );
  });

  it("does not require accent for chat personas", () => {
    const result = PersonCreateValidationSchema.safeParse({
      ...validVoicePersona,
      simulationType: AGENT_TYPES.CHAT,
      accent: [],
    });

    expect(result.success).toBe(true);
  });
});
