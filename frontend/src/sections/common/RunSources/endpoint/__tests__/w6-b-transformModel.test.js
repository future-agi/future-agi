// W6 B2: the declarative response transform (paths only, no code).
import { describe, expect, it } from "vitest";
import {
  USAGE_FIELDS,
  applyTransform,
  transformIssues,
  transformOf,
  transformSummary,
} from "../transformModel";

const body = {
  reply: "Refund issued",
  ticket: { id: "T-48004" },
  actions: [
    { name: "lookup_order", arguments: { order_id: "A-7781" } },
    { function: { name: "issue_refund", arguments: '{"order_id":"A-7781"}' } },
  ],
  usage: { input_tokens: 41, output_tokens: 12, cost_usd: 0.0000134 },
};

describe("transformModel", () => {
  it("reads the output from a path, or a template over paths", () => {
    expect(applyTransform(body, { output: "$.reply" }).output).toBe(
      "Refund issued",
    );
    const t = applyTransform(body, {
      output: "{{ $.reply }} (ref {{ $.ticket.id }})",
    });
    expect(t.output).toBe("Refund issued (ref T-48004)");
    expect(t.errors).toEqual([]);
  });

  it("a missing output path is an error; a missing tool-call list is no calls", () => {
    const t = applyTransform(
      { reply: "x" },
      { output: "$.data.text", tool_calls: "$.actions" },
    );
    expect(t.output).toBeNull();
    expect(t.errors).toEqual([
      "Output path $.data.text not found in the response",
    ]);
    expect(t.tool_calls).toEqual([]);
    expect(t.notes[0]).toMatch(/No tool calls/);
  });

  it("extracts tool calls and token usage; the endpoint's own cost is not read", () => {
    const transform = transformOf({
      output_path: "$.reply",
      transform: {
        tool_calls: "$.actions",
        // A card saved before the cost field left the form keeps no cost.
        usage: {
          prompt_tokens: "$.usage.input_tokens",
          completion_tokens: "$.usage.output_tokens",
          cost_usd: "$.usage.cost_usd",
        },
      },
    });
    expect(transform.usage).toEqual({
      prompt_tokens: "$.usage.input_tokens",
      completion_tokens: "$.usage.output_tokens",
    });
    const t = applyTransform(body, transform);
    expect(t.tool_calls).toEqual([
      { name: "lookup_order", arguments: { order_id: "A-7781" } },
      { name: "issue_refund", arguments: { order_id: "A-7781" } },
    ]);
    expect(t.usage).toEqual({ prompt_tokens: 41, completion_tokens: 12 });
    expect(USAGE_FIELDS.map((f) => f.id)).toEqual([
      "prompt_tokens",
      "completion_tokens",
    ]);
  });

  it("builds a transform from a card and checks its paths", () => {
    expect(transformOf({ output_path: "$.data.answer" })).toEqual({
      output: "$.data.answer",
    });
    expect(
      transformOf({
        output_path: "$.r",
        transform: { tool_calls: "$.actions", usage: { prompt_tokens: " " } },
      }),
    ).toEqual({ output: "$.r", tool_calls: "$.actions" });
    expect(
      transformIssues({ output: "reply", tool_calls: "actions" }),
    ).toHaveLength(2);
    expect(transformIssues({ output: "{{ $.a }} {{ b }}" })).toHaveLength(1);
    expect(
      transformSummary({
        output: "$.reply",
        tool_calls: "$.actions",
        usage: { prompt_tokens: "$.u.in" },
      }),
    ).toBe("Output $.reply · tool calls $.actions · usage 1 path");
  });
});
