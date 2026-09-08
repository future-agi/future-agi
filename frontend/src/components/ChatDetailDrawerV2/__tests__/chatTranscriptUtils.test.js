import { describe, expect, it } from "vitest";
import { getChatTurnContent } from "../chatTranscriptUtils";

// Shapes below are taken from real `ChatMessageSerializer` rows: `content` is
// the full message-dict list (tool entries included) and `messages` is the
// flat list[str] of extracted bodies.
describe("getChatTurnContent", () => {
  it("reads a plain single-message turn", () => {
    expect(
      getChatTurnContent({
        role: "user",
        messages: ["Hey, can u help me cancel order 499-20A??"],
        content: [
          { role: "user", content: "Hey, can u help me cancel order 499-20A??" },
        ],
      }),
    ).toBe("Hey, can u help me cancel order 499-20A??");
  });

  it("excludes tool results, keeping only the speech", () => {
    const turn = {
      role: "assistant",
      messages: ["I've submitted a cancellation request!"],
      content: [
        {
          role: "assistant",
          content: "I've submitted a cancellation request!",
          tool_calls: [{ id: "call_1", type: "function", function: {} }],
        },
        {
          role: "tool",
          content: "{'ok': True, 'request_id': 'CAN-98411'}",
          tool_call_id: "call_1",
        },
      ],
    };

    expect(getChatTurnContent(turn)).toBe(
      "I've submitted a cancellation request!",
    );
    expect(getChatTurnContent(turn)).not.toMatch(/CAN-98411/);
  });

  // A turn can hold more than one reply — reading messages[0] dropped
  // everything after the first.
  it("keeps every text reply in a multi-message turn", () => {
    const text = getChatTurnContent({
      role: "assistant",
      messages: ["Checking your order…", "ORD-1 shipped", "It shipped yesterday."],
      content: [
        { role: "assistant", content: "Checking your order…" },
        { role: "assistant", content: "", tool_calls: [{ id: "call_1" }] },
        { role: "tool", content: "ORD-1 shipped", tool_call_id: "call_1" },
        { role: "assistant", content: "It shipped yesterday." },
      ],
    });

    expect(text).toContain("Checking your order…");
    expect(text).toContain("It shipped yesterday.");
    // The tool result must not read as something the agent said.
    expect(text).not.toContain("ORD-1 shipped");
  });

  it("skips a tool-call turn that carries no text of its own", () => {
    expect(
      getChatTurnContent({
        role: "user",
        messages: ["Thanks!! catch ya later"],
        content: [
          { role: "user", tool_calls: [{ id: "call_2" }] },
          { role: "tool", content: "Success.", tool_call_id: "call_2" },
          { role: "user", content: "Thanks!! catch ya later" },
        ],
      }),
    ).toBe("Thanks!! catch ya later");
  });

  it("falls back to messages when content is not a dict list", () => {
    expect(getChatTurnContent({ messages: ["only here"] })).toBe("only here");
    expect(getChatTurnContent({})).toBe("");
    expect(getChatTurnContent(null)).toBe("");
  });
});
