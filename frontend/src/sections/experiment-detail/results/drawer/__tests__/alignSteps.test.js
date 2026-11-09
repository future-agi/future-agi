import { describe, expect, it } from "vitest";
import {
  alignSteps,
  divergence,
  flattenTrace,
  inputChangeText,
  lcsPairs,
  rowBadges,
  stepTotals,
  totalsText,
  traceIdFor,
  traceRoot,
  trialCount,
} from "../alignSteps";

// A GET /tracer/trace/{id}/ body: root span + child steps (type, name, ms,
// input, output, error).
function trace(name, steps, start = Date.parse("2026-10-12T09:00:00Z")) {
  let cursor = start;
  const children = steps.map((s, i) => {
    const begin = cursor;
    cursor += s.ms;
    return {
      observation_span: {
        id: `${name}-${i + 1}`,
        name: s.name,
        observation_type: s.type,
        status: s.error ? "ERROR" : "OK",
        status_message: s.error
          ? `ERROR ${s.error.code} ${s.error.message}`
          : null,
        start_time: new Date(begin).toISOString(),
        end_time: new Date(cursor).toISOString(),
        latency_ms: s.ms,
        input: s.input,
        output: s.error ? { error: s.error } : s.output ?? null,
        total_tokens: s.type === "llm" ? 400 : 0,
        cost: s.type === "llm" ? 0.0018 : 0,
      },
      children: [],
    };
  });
  return {
    trace: { id: name },
    observation_spans: [
      {
        observation_span: {
          id: `${name}-root`,
          name: "Support Agent",
          observation_type: "agent",
          start_time: new Date(start).toISOString(),
          end_time: new Date(cursor).toISOString(),
          latency_ms: cursor - start,
        },
        children,
      },
    ],
  };
}

// Row 57 (STORY.md): #3A v2 vs #4A v3.
const V2 = [
  {
    type: "llm",
    name: "classify_intent",
    ms: 410,
    input: "Order A-7781 arrived cracked.",
    output: { intent: "refund_request", order_id: "A-7781" },
  },
  {
    type: "tool",
    name: "lookup_order",
    ms: 180,
    input: { order_id: "A-7781" },
    output: { status: "delivered", total: 64.5 },
  },
  {
    type: "retriever",
    name: "search_kb",
    ms: 240,
    input: "damaged item refund policy",
    output: [{ id: "kb-4.2" }],
  },
  {
    type: "llm",
    name: "decide_action",
    ms: 690,
    input: "Decide.",
    output: { action: "refund" },
  },
  {
    type: "tool",
    name: "issue_refund",
    ms: 320,
    input: { order_id: "A-7781", amount: 64.5 },
    output: { refund_id: "RF-30981" },
  },
  {
    type: "llm",
    name: "respond",
    ms: 1060,
    input: "Write the reply.",
    output: "I'm sorry it arrived damaged.",
  },
];
const V3 = [
  {
    type: "llm",
    name: "classify_intent",
    ms: 380,
    input: "Order A-7781 arrived cracked.",
    output: { intent: "refund_request", order_ref: "7781" },
  },
  {
    type: "tool",
    name: "lookup_order",
    ms: 150,
    input: { order_id: "7781" },
    error: { code: 404, message: "Order 7781 not found" },
  },
  {
    type: "llm",
    name: "reformulate_query",
    ms: 520,
    input: "Fix the arguments.",
    output: { order_number: "A-7781" },
  },
  {
    type: "tool",
    name: "lookup_order",
    ms: 90,
    input: { order_number: "A-7781" },
    error: { code: 400, message: "Unknown argument: order_number" },
  },
  {
    type: "retriever",
    name: "search_kb",
    ms: 260,
    input: "refund damaged item",
    output: [{ id: "kb-4.2" }],
  },
  {
    type: "llm",
    name: "plan",
    ms: 610,
    input: "Plan.",
    output: { action: "respond" },
  },
  {
    type: "retriever",
    name: "search_kb",
    ms: 230,
    input: "refund timeline",
    output: [{ id: "kb-2.1" }],
  },
  {
    type: "llm",
    name: "draft_reply",
    ms: 1320,
    input: "Draft.",
    output: "I've processed a refund.",
  },
  {
    type: "llm",
    name: "respond",
    ms: 1840,
    input: "Polish the draft and reply.",
    output: "I've processed a refund of $64.50.",
  },
];
const labels = { labelA: "#3A", labelB: "#4A" };

describe("flattenTrace / stepTotals", () => {
  it("drops the root and orders steps by start time", () => {
    const steps = flattenTrace(trace("a", V2));
    expect(steps.map((s) => s.name)).toEqual(V2.map((s) => s.name));
    expect(steps[1]).toMatchObject({
      type: "tool",
      key: "tool:lookup_order",
      ms: 180,
    });
  });

  it("keeps a single-span trace as one step (prompt runs)", () => {
    const body = trace("p", []);
    body.observation_spans[0].observation_span.observation_type = "llm";
    expect(flattenTrace(body)).toHaveLength(1);
  });

  it("counts a prompt run as LLM calls", () => {
    const body = trace("p", [V2[5]]);
    expect(totalsText(stepTotals(flattenTrace(body), traceRoot(body)))).toBe(
      "1 LLM call · 1.1 s",
    );
  });

  it("totals row 57 as the story says", () => {
    const a = trace("a", V2);
    const b = trace("b", V3);
    expect(totalsText(stepTotals(flattenTrace(a), traceRoot(a)))).toBe(
      "6 steps · 2 tool calls · 2.9 s",
    );
    expect(totalsText(stepTotals(flattenTrace(b), traceRoot(b)))).toBe(
      "9 steps · 2 tool calls · 2 tool errors · 5.4 s",
    );
  });

  it("marks an errored span with its error and no output", () => {
    const [, lookup] = flattenTrace(trace("b", V3));
    expect(lookup.error).toBe("404 Order 7781 not found");
    expect(lookup.output).toBeNull();
  });
});

describe("alignSteps", () => {
  const rows = alignSteps(
    flattenTrace(trace("a", V2)),
    flattenTrace(trace("b", V3)),
  );

  it("aligns on type:name with the earliest matches", () => {
    expect(lcsPairs(["x", "y", "z"], ["x", "q", "y", "y", "z"])).toEqual([
      [0, 0],
      [1, 2],
      [2, 4],
    ]);
    expect(
      rows.map((r) => [r.a?.name ?? "—", r.b?.name ?? "—", r.kind]),
    ).toEqual([
      ["classify_intent", "classify_intent", "changed"],
      ["lookup_order", "lookup_order", "changed"],
      ["—", "reformulate_query", "only_b"],
      ["—", "lookup_order", "only_b"],
      ["search_kb", "search_kb", "changed"],
      ["decide_action", "—", "only_a"],
      ["issue_refund", "—", "only_a"],
      ["—", "plan", "only_b"],
      ["—", "search_kb", "only_b"],
      ["—", "draft_reply", "only_b"],
      ["respond", "respond", "changed"],
    ]);
    expect(rows.map((r) => r.index)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11,
    ]);
  });

  it("badges each row", () => {
    expect(rowBadges(rows[0], labels).map((b) => b.text)).toEqual([
      "Output changed",
    ]);
    expect(rowBadges(rows[1], labels).map((b) => b.text)).toEqual([
      "Input changed",
      "Error",
    ]);
    expect(rowBadges(rows[2], labels).map((b) => b.text)).toEqual([
      "Only in #4A",
    ]);
    expect(rowBadges(rows[3], labels).map((b) => b.text)).toEqual([
      "Only in #4A",
      "Error",
    ]);
    expect(rowBadges(rows[5], labels).map((b) => b.text)).toEqual([
      "Only in #3A",
    ]);
  });

  it("diverges at step 2, not at the output-only change of step 1", () => {
    expect(divergence(rows, labels)).toMatchObject({
      index: 2,
      text: 'lookup_order input changed (order_id "A-7781" → "7781")',
    });
  });

  it("reports no divergence for identical trajectories", () => {
    const same = alignSteps(
      flattenTrace(trace("a", V2)),
      flattenTrace(trace("c", V2)),
    );
    expect(same.every((r) => r.kind === "same")).toBe(true);
    expect(rowBadges(same[0], labels)[0].text).toBe("Same");
    expect(divergence(same, labels)).toBeNull();
  });

  it("names a step only one run took, and an error on one side", () => {
    const extra = alignSteps(
      flattenTrace(trace("a", V2.slice(0, 2))),
      flattenTrace(trace("b", [V2[0], V2[1], V2[2]])),
    );
    expect(divergence(extra, labels).text).toBe("search_kb only in #4A");
    const failed = alignSteps(
      flattenTrace(trace("a", [V2[1]])),
      flattenTrace(
        trace("b", [{ ...V2[1], error: { code: 500, message: "boom" } }]),
      ),
    );
    expect(divergence(failed, labels).text).toBe(
      "lookup_order failed in #4A (500 boom)",
    );
  });
});

describe("inputChangeText", () => {
  it("shows a renamed key, a changed value and plain strings", () => {
    expect(
      inputChangeText({ order_id: "7781" }, { order_number: "A-7781" }),
    ).toBe('order_id "7781" → order_number "A-7781"');
    expect(inputChangeText({ a: 1, b: 2 }, { a: 1, b: 3 })).toBe("b 2 → 3");
    expect(inputChangeText("damaged item", "refund item")).toBe(
      '"damaged item" → "refund item"',
    );
  });
});

describe("traceIdFor / trialCount", () => {
  const result = {
    trials: 3,
    trace_ids: ["t1", "t2", "t3"],
    trials_detail: [{ trial: 2, trace_id: "t2-detail" }],
  };
  it("prefers trials_detail, then trace_ids, then trial 1", () => {
    expect(traceIdFor(result, 2)).toBe("t2-detail");
    expect(traceIdFor(result, 3)).toBe("t3");
    expect(traceIdFor({ trace_ids: ["only"] }, 3)).toBe("only");
    expect(traceIdFor(null)).toBeNull();
    expect(trialCount(result)).toBe(3);
    expect(trialCount({})).toBe(1);
  });
});
