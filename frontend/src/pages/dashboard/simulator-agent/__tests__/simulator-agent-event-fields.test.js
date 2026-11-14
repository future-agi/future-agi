import { describe, expect, it } from "vitest";
import { simulatorAgentEventFields } from "../simulatorAgentEventFields";

const PROMPT_TEXT = "SYNTHETIC AGENT PROMPT 9b3c";
const FIRST_LINE = "SYNTHETIC FIRST LINE 9b3c";

describe("simulatorAgentEventFields", () => {
  it("sends the agent settings and text lengths, not the prompt or first message", () => {
    const fields = simulatorAgentEventFields({
      name: "Synthetic agent",
      prompt: PROMPT_TEXT,
      model: "synthetic-model",
      agentType: "voice",
      llmTemperature: 0.7,
      voiceProvider: "elevenlabs",
      voiceName: "marissa",
      interruptSensitivity: 5,
      conversationSpeed: 1,
      finishedSpeakingSensitivity: 5,
      maxCallDurationInMinutes: 5,
      initialMessageDelay: 1,
      initialMessage: FIRST_LINE,
    });

    expect(fields).toEqual({
      model: "synthetic-model",
      agentType: "voice",
      llmTemperature: 0.7,
      voiceProvider: "elevenlabs",
      voiceName: "marissa",
      interruptSensitivity: 5,
      conversationSpeed: 1,
      finishedSpeakingSensitivity: 5,
      maxCallDurationInMinutes: 5,
      initialMessageDelay: 1,
      prompt_length: PROMPT_TEXT.length,
      has_initial_message: true,
    });
    expect(JSON.stringify(fields)).not.toContain(PROMPT_TEXT);
    expect(JSON.stringify(fields)).not.toContain(FIRST_LINE);
    expect(JSON.stringify(fields)).not.toContain("Synthetic agent");
  });

  it("handles empty form values", () => {
    expect(simulatorAgentEventFields(undefined)).toMatchObject({
      prompt_length: 0,
      has_initial_message: false,
    });
  });
});
