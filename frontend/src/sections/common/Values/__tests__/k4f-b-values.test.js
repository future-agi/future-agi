// integration (RV-02): Chat and Auto read a session object whose conversation is
// in `turns` or `messages`.
import { describe, expect, it } from "vitest";
import { sniffValue, wrappedChatOf } from "../sniff";
import { effectiveMode, modeAvailability } from "../viewModes";

const session = {
  turns: [
    { n: 1, role: "customer", content: "Locked out since the SSO change." },
    {
      n: 2,
      role: "agent",
      content: "Let me check.",
      tool_calls: [{ name: "lookup_org", args: { org: "Northwind" } }],
    },
  ],
  resolution: "Fail",
  failing_turn: 2,
};

describe("RV-02 wrapped conversations", () => {
  it("an object with turns is chat; roles and tool calls are normalised", () => {
    const s = sniffValue(session);
    expect(s.kind).toBe("chat");
    expect(s.meta.wrapped).toBe("turns");
    expect(s.meta.turns).toBe(2);
    expect(s.normalized.map((m) => m.role)).toEqual(["user", "assistant"]);
    expect(s.normalized[1].tool_calls[0].function).toEqual({
      name: "lookup_org",
      arguments: JSON.stringify({ org: "Northwind" }),
    });
    expect(s.meta.extra).toEqual({ resolution: "Fail", failing_turn: 2 });
    expect(modeAvailability(s).chat).toBeNull();
    expect(effectiveMode("chat", s)).toBe("chat");
  });
  it("an object with messages is chat; JSON text too", () => {
    const v = { messages: [{ role: "user", content: "hi" }], id: "x" };
    expect(sniffValue(v).kind).toBe("chat");
    expect(sniffValue(JSON.stringify(v)).kind).toBe("chat");
  });
  it("other objects stay objects", () => {
    expect(sniffValue({ turns: [] }).kind).toBe("object");
    expect(sniffValue({ turns: [1, 2] }).kind).toBe("object");
    expect(sniffValue({ a: 1 }).kind).toBe("object");
    expect(wrappedChatOf({ messages: "no" })).toBeNull();
  });
});
