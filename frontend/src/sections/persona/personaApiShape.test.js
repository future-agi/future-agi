import { describe, expect, it } from "vitest";
import { normalizePersona } from "./personaApiShape";

describe("normalizePersona", () => {
  it("adds UI aliases for canonical persona API fields", () => {
    const persona = normalizePersona({
      id: "persona-1",
      name: "QA Persona",
      persona_type: "workspace",
      persona_type_display: "Workspace",
      age_group: ["25-32"],
      occupation: ["Engineer"],
      communication_style: ["Direct and concise"],
      conversation_speed: ["1.0"],
      background_sound: false,
      finished_speaking_sensitivity: ["5"],
      interrupt_sensitivity: ["6"],
      additional_instruction: "Stay concise.",
      metadata: { source: "api" },
      simulation_type: "voice",
      is_default: false,
      created_at: "2026-05-25T00:00:00Z",
      updated_at: "2026-05-25T01:00:00Z",
      slang_usage: "light",
      typos_frequency: "rare",
      regional_mix: "light",
      emoji_usage: "never",
    });

    expect(persona).toMatchObject({
      personaType: "workspace",
      personaTypeDisplay: "Workspace",
      ageGroup: ["25-32"],
      profession: ["Engineer"],
      communicationStyle: ["Direct and concise"],
      conversationSpeed: ["1.0"],
      backgroundSound: false,
      finishedSpeakingSensitivity: ["5"],
      interruptSensitivity: ["6"],
      additionalInstruction: "Stay concise.",
      customProperties: { source: "api" },
      simulationType: "voice",
      isDefault: false,
      createdAt: "2026-05-25T00:00:00Z",
      updatedAt: "2026-05-25T01:00:00Z",
      slangUsage: "light",
      typosFrequency: "rare",
      regionalMix: "light",
      emojiUsage: "never",
    });
    expect(persona.age_group).toEqual(["25-32"]);
    expect(persona.occupation).toEqual(["Engineer"]);
  });
});
