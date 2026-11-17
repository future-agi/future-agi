// integration review fix (RV-02): an object read as chat because it holds a
// `turns` / `messages` list is still an object — Pretty, Tree, JSON and YAML
// stay available on it (a request payload `{model, temperature, messages}`
// as much as an ES session), and the other keys ride `meta.extra`.
import { describe, expect, it } from "vitest";
import { sniffValue } from "../sniff";
import { effectiveMode, modeAvailability } from "../viewModes";

const payload = {
  model: "gpt-4o-mini",
  temperature: 0.2,
  messages: [
    { role: "system", content: "You are a support agent." },
    { role: "user", content: "Where is my refund?" },
  ],
};
const session = {
  channel: "chat",
  turns: [
    { role: "customer", content: "Locked out since the SSO change." },
    { role: "agent", content: "Let me check." },
  ],
  resolution: "Fail",
  failing_turn: 2,
};

describe("RV-02 · a wrapped conversation keeps the object modes", () => {
  it.each([
    ["a request payload", payload, ["model", "temperature"]],
    ["an ES session", session, ["channel", "resolution", "failing_turn"]],
  ])("%s: Chat, Pretty, Tree, JSON and YAML all apply", (_, value, keys) => {
    const s = sniffValue(value);
    expect(s.kind).toBe("chat");
    const a = modeAvailability(s);
    ["chat", "pretty", "tree", "json", "yaml", "raw"].forEach((m) =>
      expect({ m, reason: a[m] }).toEqual({ m, reason: null }),
    );
    expect(effectiveMode("pretty", s)).toBe("pretty");
    expect(Object.keys(s.meta.extra)).toEqual(keys);
  });

  it("JSON text of a wrapped conversation reads the same way", () => {
    const s = sniffValue(JSON.stringify(payload));
    expect(s.kind).toBe("chat");
    expect(modeAvailability(s).pretty).toBeNull();
  });

  it("a plain message list is not an object: Pretty keeps its reason", () => {
    const s = sniffValue(payload.messages);
    expect(s.kind).toBe("chat");
    expect(modeAvailability(s).pretty).toBe("Pretty needs an object");
  });
});
