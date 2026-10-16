import { describe, it, expect } from "vitest";
import {
  getChangeText,
  getPerformanceMetricsLabel,
  formatIfFloat,
  transformToConversations,
} from "../common";

const chatPayload = {
  base_session_transcripts: [
    {
      id: "b1",
      role: "user",
      messages: ["Hi there"],
      created_at: "2026-08-17T10:00:00Z",
    },
    { id: "b2", role: "assistant", messages: ["Hello, how can I help?"] },
  ],
  comparison_call_transcripts: [
    { id: "r1", role: "user", messages: ["Hi there"] },
    { id: "r2", role: "assistant", messages: ["Hey, what do you need?"] },
  ],
};

describe("transformToConversations", () => {
  it("reads the snake_case keys the comparison endpoint returns", () => {
    const result = transformToConversations(chatPayload);

    expect(result.baselineSession.conversations).toHaveLength(2);
    expect(result.replayedSession.conversations).toHaveLength(2);
    expect(result.baselineSession.conversations[0].content).toBe("Hi there");
    expect(result.replayedSession.conversations[1].content).toBe(
      "Hey, what do you need?",
    );
  });

  it("still accepts an already camelised payload", () => {
    const result = transformToConversations({
      baseSessionTranscripts: chatPayload.base_session_transcripts,
      comparisonCallTranscripts: chatPayload.comparison_call_transcripts,
    });

    expect(result.baselineSession.conversations).toHaveLength(2);
    expect(result.replayedSession.conversations).toHaveLength(2);
  });

  it("returns empty sessions instead of throwing when data is missing", () => {
    const result = transformToConversations(undefined);

    expect(result.baselineSession.conversations).toEqual([]);
    expect(result.replayedSession.conversations).toEqual([]);
    expect(result.baselineSession.label).toBe("A");
    expect(result.replayedSession.label).toBe("B");
  });

  it("normalises voice roles onto the chat roles the columns render", () => {
    const result = transformToConversations({
      base_session_transcripts: [
        { id: "v1", role: "bot", messages: "Thanks for calling" },
        { id: "v2", role: "agent", messages: "I need help" },
        { id: "v3", role: "customer", messages: "Still there?" },
      ],
      comparison_call_transcripts: [],
    });

    const roles = result.baselineSession.conversations.map((c) => c.role);
    expect(roles).toEqual(["assistant", "user", "user"]);
  });

  it("handles voice string messages and chat array messages alike", () => {
    const result = transformToConversations({
      base_session_transcripts: [
        { id: "v1", role: "bot", messages: "One voice line" },
      ],
      comparison_call_transcripts: [
        { id: "c1", role: "assistant", messages: ["First", "Second"] },
      ],
    });

    expect(result.baselineSession.conversations).toHaveLength(1);
    expect(result.baselineSession.conversations[0].content).toBe(
      "One voice line",
    );
    expect(result.replayedSession.conversations).toHaveLength(2);
    expect(result.replayedSession.conversations[1].content).toBe("Second");
  });

  it("aligns each turn to the column its role belongs to", () => {
    const result = transformToConversations(chatPayload);
    const [first, second] = result.baselineSession.conversations;

    expect(first.align).toBe("flex-end");
    expect(first.agentName).toBe("User");
    expect(second.align).toBe("flex-start");
    expect(second.agentName).toBe("Assistant");
  });
});

describe("metric label helpers", () => {
  it("labels duration by modality", () => {
    expect(getPerformanceMetricsLabel("duration", false)).toBe("Chat Duration");
    expect(getPerformanceMetricsLabel("duration", true)).toBe("Call Duration");
  });

  it("exposes voice-only metrics only for voice", () => {
    expect(getPerformanceMetricsLabel("talk_ratio", true)).toBe("Talk Ratio");
    expect(getPerformanceMetricsLabel("talk_ratio", false)).toBe("talk_ratio");
  });

  it("describes the baseline by modality", () => {
    expect(getChangeText(false)).toBe("from baseline chat");
    expect(getChangeText(true)).toBe("from baseline call");
  });

  it("trims floats but leaves integers and non-numbers alone", () => {
    expect(formatIfFloat(1.23456)).toBe("1.23");
    expect(formatIfFloat(7)).toBe(7);
    expect(formatIfFloat("n/a")).toBe("n/a");
  });
});
