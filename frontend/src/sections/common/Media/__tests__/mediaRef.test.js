import { describe, expect, it } from "vitest";
import { CALL_VALUE_EXAMPLE } from "src/test-fixtures/call-value";
import {
  MEDIA_KINDS,
  callOf,
  conversationOf,
  fmtDuration,
  isCall,
  lastCustomerTurn,
  mediaLabel,
  mediaRefOf,
  outputKindOf,
} from "../mediaRef";

describe("mediaRefOf (D9)", () => {
  it("sniffs URLs by extension", () => {
    expect(mediaRefOf("/mock/multimodal/screen-1.png")).toEqual({
      kind: "image",
      mime: "image/png",
      url: "/mock/multimodal/screen-1.png",
      name: "screen-1.png",
    });
    expect(mediaRefOf("https://x.ai/a/call.wav?sig=1").kind).toBe("audio");
    expect(mediaRefOf("/mock/multimodal/clip.mp4").kind).toBe("video");
    expect(mediaRefOf("/mock/multimodal/contract.pdf")).toMatchObject({
      kind: "document",
      mime: "application/pdf",
    });
    expect(mediaRefOf("https://example.com/page")).toBeNull();
  });

  it("reads data: URIs", () => {
    expect(mediaRefOf("data:image/png;base64,AAAA")).toMatchObject({
      kind: "image",
      mime: "image/png",
    });
    expect(mediaRefOf("data:audio/wav;base64,AAAA").kind).toBe("audio");
    expect(mediaRefOf("data:application/pdf;base64,AAAA").kind).toBe(
      "document",
    );
  });

  it("reads OpenAI and Anthropic parts and {kind, url} dicts", () => {
    expect(
      mediaRefOf({
        type: "image_url",
        image_url: { url: "https://x.ai/p.jpg" },
      }).kind,
    ).toBe("image");
    expect(
      mediaRefOf({
        type: "input_audio",
        input_audio: { data: "AAAA", format: "mp3" },
      }),
    ).toMatchObject({
      kind: "audio",
      mime: "audio/mp3",
    });
    expect(
      mediaRefOf({
        type: "image",
        source: { type: "base64", media_type: "image/jpeg", data: "AAAA" },
      }),
    ).toMatchObject({ kind: "image", mime: "image/jpeg" });
    expect(
      mediaRefOf({
        type: "document",
        source: { type: "base64", media_type: "application/pdf", data: "AA" },
      }).kind,
    ).toBe("document");
    expect(
      mediaRefOf({ kind: "audio", url: "/a/x.bin", duration_ms: 42000 }),
    ).toMatchObject({
      kind: "audio",
      duration_ms: 42000,
    });
  });

  it("uses the column type for bare URLs and arrays", () => {
    expect(mediaRefOf("/files/blob-123", { columnType: "image" }).kind).toBe(
      "image",
    );
    const three = mediaRefOf('["/m/p-1.png","/m/p-2.png","/m/p-3.png"]', {
      columnType: "images",
    });
    expect(three).toHaveLength(3);
    expect(mediaLabel(three)).toBe("3 images");
    expect(mediaRefOf("/m/call.wav", { columnType: "audio" }).kind).toBe(
      "audio",
    );
  });

  it("knows a CallValue", () => {
    expect(isCall(CALL_VALUE_EXAMPLE)).toBe(true);
    expect(callOf(JSON.stringify(CALL_VALUE_EXAMPLE)).call_id).toBe(
      "call_1b_000003_1",
    );
    expect(mediaRefOf(CALL_VALUE_EXAMPLE)).toMatchObject({
      kind: "call",
      duration_ms: 108000,
    });
    expect(mediaLabel(mediaRefOf(CALL_VALUE_EXAMPLE))).toBe("Call · 1 m 48 s");
  });

  it("leaves text, numbers, JSON and conversations alone", () => {
    expect(mediaRefOf("Thanks Ana, refund started.")).toBeNull();
    expect(mediaRefOf(0.68)).toBeNull();
    expect(mediaRefOf('{"answer": "Yes"}')).toBeNull();
    expect(mediaRefOf([{ role: "user", content: "hi" }])).toBeNull();
    expect(MEDIA_KINDS).toEqual([
      "image",
      "audio",
      "video",
      "document",
      "call",
      "file",
    ]);
  });

  it("labels media", () => {
    expect(mediaLabel(mediaRefOf("/m/screen-1.png"))).toBe(
      "Image · screen-1.png",
    );
    expect(
      mediaLabel({
        kind: "document",
        mime: "application/pdf",
        url: "x",
        pages: 3,
      }),
    ).toBe("PDF · 3 pages");
    expect(
      mediaLabel({
        kind: "audio",
        mime: "audio/wav",
        url: "x",
        duration_ms: 42000,
      }),
    ).toBe("Audio · 42 s");
    expect(mediaLabel(null)).toBe("");
    expect(fmtDuration(842)).toBe("842 ms");
    expect(fmtDuration(108000)).toBe("1 m 48 s");
  });

  it("names output kinds", () => {
    expect(outputKindOf("hello")).toBe("text");
    expect(outputKindOf('{"a":1}')).toBe("json");
    expect(outputKindOf(CALL_VALUE_EXAMPLE)).toBe("call");
    expect(outputKindOf("data:image/png;base64,AA")).toBe("image");
    expect(outputKindOf([{ role: "user", content: "hi" }])).toBe("messages");
    expect(outputKindOf("x", { outputKind: "audio" })).toBe("audio");
  });
});

describe("conversationOf (D24)", () => {
  const openai = JSON.stringify([
    { role: "system", content: "Be brief." },
    { role: "user", content: "My blender arrived cracked. Order A-7781." },
    {
      role: "assistant",
      content: null,
      tool_calls: [
        {
          id: "c1",
          type: "function",
          function: {
            name: "lookup_order",
            arguments: '{"order_id":"A-7781"}',
          },
        },
      ],
    },
    { role: "tool", content: '{"status":"delivered"}' },
    { role: "assistant", content: "Sorry about that, Ana." },
    { role: "user", content: [{ type: "text", text: "Can I get a refund?" }] },
  ]);

  it("reads OpenAI messages (JSON string, tool calls, parts)", () => {
    const c = conversationOf(openai);
    expect(c.n).toBe(6);
    expect(c.firstCustomer).toBe("My blender arrived cracked. Order A-7781.");
    expect(c.turns[2]).toEqual({
      role: "assistant",
      text: 'lookup_order({"order_id":"A-7781"})',
    });
    expect(lastCustomerTurn(openai)).toBe("Can I get a refund?");
  });

  it("reads {turns} and Anthropic content blocks, with a failing turn", () => {
    const c = conversationOf({
      turns: [
        { role: "customer", text: "Where is my order?" },
        { role: "agent", text: "Checking.", failing: true },
      ],
    });
    expect(c).toEqual({
      turns: [
        { role: "customer", text: "Where is my order?" },
        { role: "agent", text: "Checking." },
      ],
      n: 2,
      firstCustomer: "Where is my order?",
      failingTurn: 1,
    });
    expect(
      conversationOf([
        { role: "user", content: [{ type: "text", text: "hello" }] },
      ]).firstCustomer,
    ).toBe("hello");
  });

  it("is null for things that are not conversations", () => {
    expect(conversationOf("plain text")).toBeNull();
    expect(conversationOf([{ a: 1 }])).toBeNull();
    expect(conversationOf([])).toBeNull();
    expect(conversationOf({ answer: "yes" })).toBeNull();
  });
});
