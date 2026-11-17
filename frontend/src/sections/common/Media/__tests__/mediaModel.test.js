import { describe, expect, it } from "vitest";
import {
  callChipText,
  callChipTooltip,
  clockOf,
  failingTurnOf,
  fittedSize,
  mediaCellKind,
  personaOf,
} from "../mediaModel";
import { conversationOf } from "../mediaRef";

const call = (over = {}) => ({
  kind: "call",
  call_id: "c-1",
  depth: "full_voice",
  status: "ended",
  ended_reason: "resolved",
  duration_ms: 108000,
  latency_source: "recording",
  metrics: {
    latency_ms: { p50: 900, p90: 1800 },
    interruptions: { agent_kept_talking: 1, caller_barge_ins: 3 },
  },
  recording: { agent: { url: "/a.wav" }, caller: { url: "/c.wav" } },
  turns: [],
  ...over,
});

describe("personaOf", () => {
  it("reads a persona object or JSON text", () => {
    const p = personaOf(
      '{"name":"Ana","label":"Ana · calm","traits":{"pace":"slow","x":{"a":1}}}',
    );
    expect(p).toMatchObject({ name: "Ana", label: "Ana · calm", initial: "A" });
    expect(p.traits).toEqual([["pace", "slow"]]);
  });
  it("rejects rows that only carry a name", () => {
    expect(personaOf({ name: "Ana" })).toBeNull();
    expect(personaOf({ name: "Ana", label: "x", order_id: "A-1" })).toBeNull();
    expect(personaOf("Ana")).toBeNull();
    expect(personaOf([{ name: "Ana", label: "x" }])).toBeNull();
  });
});

describe("callChipText", () => {
  it("full voice: duration · p50 · interruptions", () => {
    expect(callChipText(call())).toBe("1 m 48 s · p50 900 ms · 1 interruption");
    expect(
      callChipText(
        call({ metrics: { latency_ms: { p50: 1420 }, interruptions: {} } }),
      ),
    ).toBe("1 m 48 s · p50 1.4 s · 0 interruptions");
  });
  it("text only never shows a latency", () => {
    const t = call({ depth: "text_only", turns: new Array(9).fill({}) });
    expect(callChipText(t)).toBe("Text only · 9 turns");
    expect(callChipText(t, { compact: true })).toBe("Text only");
  });
  it("compact for 72 px Matrix cells", () => {
    expect(callChipText(call(), { compact: true })).toBe("900 ms · ⚡1");
    expect(
      callChipText(call({ metrics: { latency_ms: {}, interruptions: {} } }), {
        compact: true,
      }),
    ).toBe("—");
  });
  it("tooltip names where the latency comes from", () => {
    expect(callChipTooltip(call())).toContain("estimated from the recording");
    expect(
      callChipTooltip(call({ latency_source: "provider", recording: null })),
    ).toMatch(/reported by the provider[\s\S]*Recording missing/);
  });
});

describe("fittedSize", () => {
  it("fits inside the box", () => {
    expect(fittedSize(640, 480, 480, 240)).toEqual({
      w: 320,
      h: 240,
      scale: 0.5,
    });
  });
  it("never upscales past 2×", () => {
    expect(fittedSize(100, 50, 1000, 1000)).toEqual({
      w: 200,
      h: 100,
      scale: 2,
    });
  });
  it("unknown size → the box", () => {
    expect(fittedSize(0, 0, 300, 200)).toEqual({ w: 300, h: 200, scale: 1 });
  });
});

describe("clockOf", () => {
  it("formats m:ss and guards bad input", () => {
    expect(clockOf(4000)).toBe("0:04");
    expect(clockOf(108000)).toBe("1:48");
    expect(clockOf(NaN)).toBe("0:00");
  });
});

describe("failingTurnOf", () => {
  it("is 1-based from a flagged message, or the stated failing_turn", () => {
    const msgs = [
      { role: "user", content: "a" },
      { role: "assistant", content: "b", failing: true },
    ];
    expect(failingTurnOf(msgs, conversationOf(msgs))).toBe(2);
    const v = { turns: [{ role: "user", text: "a" }], failing_turn: 6 };
    expect(failingTurnOf(v, conversationOf(v))).toBe(6);
    const none = [{ role: "user", content: "a" }];
    expect(failingTurnOf(none, conversationOf(none))).toBeNull();
  });
});

describe("mediaCellKind", () => {
  it("calls, personas, media; else null", () => {
    expect(mediaCellKind(call())).toBe("call");
    expect(mediaCellKind({ name: "Ana", label: "Ana · calm" }, "json")).toBe(
      "persona",
    );
    expect(mediaCellKind("https://x.test/a.png")).toBe("media");
    expect(mediaCellKind("a.png", "text")).toBeNull();
    expect(mediaCellKind("https://x.test/photo", "image")).toBe("media");
    expect(mediaCellKind("hello")).toBeNull();
    expect(mediaCellKind("")).toBeNull();
    expect(mediaCellKind("[]", "images")).toBeNull();
  });
  it("a conversation with an image part stays a chat", () => {
    const conv = [
      {
        role: "user",
        content: [
          { type: "text", text: "Screenshot attached." },
          {
            type: "image",
            source: { type: "base64", media_type: "image/png", data: "AAAA" },
          },
        ],
      },
    ];
    expect(mediaCellKind(conv)).toBeNull();
    expect(mediaCellKind(JSON.stringify(conv), "json")).toBeNull();
  });
});
