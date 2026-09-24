import { describe, it, expect } from "vitest";
import {
  modalityOf,
  sourceForKey,
  mappingRowsFor,
  humanizeMappingTerm,
} from "../evalSourceMapping";

describe("evalSourceMapping (§10 modality mapping)", () => {
  it("reads the environment modality (voice vs text)", () => {
    expect(modalityOf({ agentType: "voice" })).toBe("voice");
    expect(modalityOf({ agentType: "chat" })).toBe("text");
    expect(modalityOf({ agentType: "text" })).toBe("text");
    expect(modalityOf(undefined)).toBe("text");
  });

  it("resolves voice keys to the recording, text keys to the transcript", () => {
    expect(sourceForKey("conversation", "voice")).toBe("voice_recording");
    expect(sourceForKey("input_audio", "voice")).toBe("voice_recording");
    expect(sourceForKey("conversation", "text")).toBe("transcript");
    expect(sourceForKey("output", "text")).toBe("transcript");
  });

  it("maps prompt keys to agent_prompt in both modalities", () => {
    expect(sourceForKey("agent_prompt", "voice")).toBe("agent_prompt");
    expect(sourceForKey("system_prompt", "text")).toBe("agent_prompt");
  });

  it("returns null for a key with no source in that modality", () => {
    // input_audio is voice-only; a text run has no audio for it.
    expect(sourceForKey("input_audio", "text")).toBeNull();
    expect(sourceForKey("nonsense", "voice")).toBeNull();
  });

  it("builds read-only rows for an eval's required keys", () => {
    expect(mappingRowsFor(["agent_prompt", "conversation"], "voice")).toEqual([
      { key: "agent_prompt", value: "agent_prompt" },
      { key: "conversation", value: "voice_recording" },
    ]);
  });

  it("humanizes snake_case terms", () => {
    expect(humanizeMappingTerm("voice_recording")).toBe("Voice recording");
    expect(humanizeMappingTerm("agent_prompt")).toBe("Agent prompt");
  });
});
