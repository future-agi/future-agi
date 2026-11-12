import { describe, expect, it } from "vitest";
import { normalizeToolCall, toolCallText, toolOutcome } from "../toolCallModel";

describe("toolCallModel", () => {
  it("reads an OpenAI call and its matching tool message", () => {
    const messages = [
      {
        role: "assistant",
        tool_calls: [
          {
            id: "call-1",
            type: "function",
            function: {
              name: "lookup_order",
              arguments: '{"order_id":"A-7701"}',
            },
          },
        ],
      },
      {
        role: "tool",
        tool_call_id: "call-1",
        content: '{"status":"found","order_id":"A-7701"}',
      },
    ];
    const call = normalizeToolCall(messages[0].tool_calls[0], { messages });
    expect(toolCallText(call)).toBe(
      'lookup_order(order_id: "A-7701") → 200 OK',
    );
    expect(call.result).toContain("A-7701");
    expect(toolOutcome(call)).toMatchObject({ code: 200, tone: "ok" });
  });

  it("keeps the compact fixture shape", () => {
    const call = normalizeToolCall({
      name: "lookup_order",
      args: { order_id: "A-7701" },
      result: { status: "found" },
    });
    expect(toolCallText(call)).toBe(
      'lookup_order(order_id: "A-7701") → 200 OK',
    );
    expect(call.args).toEqual({ order_id: "A-7701" });
  });
});

describe("toolCallModel result lookup", () => {
  it("falls back to the next tool message after the calling message", () => {
    const first = { function: { name: "search", arguments: '{"q":"a"}' } };
    const second = { function: { name: "lookup_order", arguments: "{}" } };
    const messages = [
      { role: "assistant", tool_calls: [first] },
      { role: "tool", content: '{"hits":1}' },
      { role: "assistant", tool_calls: [second] },
      { role: "tool", content: '{"status":"found"}' },
    ];
    expect(normalizeToolCall(second, { messages }).result).toBe(
      '{"status":"found"}',
    );
    expect(normalizeToolCall(first, { messages }).result).toBe('{"hits":1}');
  });

  it("reads a 404 error result as a red chip with its message", () => {
    const call = normalizeToolCall({
      name: "lookup_order",
      args: { order_id: "8830" },
      result: { error: { code: 404, message: "Order 8830 not found" } },
    });
    expect(toolOutcome(call)).toMatchObject({
      code: 404,
      tone: "error",
      message: "Order 8830 not found",
    });
  });
});
