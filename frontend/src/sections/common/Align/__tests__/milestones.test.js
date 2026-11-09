// R3 G0 §B9 on literal TS T26 trial-1 fixtures (copied from
// _mock/prototype/fixtures/world/taskTrajectories.js handAgent / handPrompt /
// handClaudeT26; outputs shortened where they do not matter).
import { describe, expect, it } from "vitest";
import {
  alignLanes,
  firstDifferenceText,
  firstWrongStepText,
  gapSummary,
  howAligned,
  howAlignedHelp,
  markers,
  stepNumbersHelp,
  toLaneSteps,
} from "../milestones";
import { normalizeSteps as copyNormalize, stableJson } from "../normalize";
import { normalizeSteps as drawerNormalize } from "src/sections/experiment-detail/results/drawer/alignSteps";

const notFound = (id) => ({
  error: { code: 404, message: `Order ${id} not found` },
});
const order = { id: "A-8830", status: "delivered", items: [{ sku: "BL-700" }] };
const instruction =
  "Hi, my blender from order A-8830 arrived with a cracked jug (photo attached). Can I get a refund?";

// #2B Support Agent v3 (agent graph), trial 1
const B_STEPS = [
  {
    n: 1,
    type: "node",
    node_type: "llm",
    name: "classify_intent",
    input: instruction,
    output: { intent: "refund_request", order_ref: "8830" },
    status: "ok",
    ms: 900,
  },
  {
    n: 2,
    type: "node",
    node_type: "tool",
    name: "lookup_order",
    input: { order_id: "8830" },
    output: notFound("8830"),
    mode: "live",
    status: "error",
    ms: 300,
  },
  {
    n: 3,
    type: "node",
    node_type: "llm",
    name: "reformulate_query",
    input: "lookup_order failed: Order 8830 not found.",
    output: { order_ref: "8830", hint: "digits only" },
    status: "ok",
    ms: 700,
  },
  {
    n: 4,
    type: "node",
    node_type: "tool",
    name: "lookup_order",
    input: { order_id: "8830" },
    output: notFound("8830"),
    mode: "live",
    status: "error",
    ms: 300,
  },
  {
    n: 5,
    type: "node",
    node_type: "retriever",
    name: "search_kb",
    input: "damaged on arrival refund",
    output: [{ id: "kb-12" }],
    status: "ok",
    ms: 400,
  },
  {
    n: 6,
    type: "node",
    node_type: "llm",
    name: "plan",
    input: "Decide the next action from the order and policy.",
    output: { action: "ask_customer", note: "order 8830 not found" },
    status: "ok",
    ms: 1500,
  },
  {
    n: 7,
    type: "node",
    node_type: "llm",
    name: "respond",
    input: "Write the reply.",
    output:
      "Thanks for the photo, Ana, and sorry about the jug. I couldn't find order 8830 on your account.",
    role: "assistant",
    status: "ok",
    ms: 2100,
  },
  {
    n: 8,
    type: "end_state",
    name: "End state",
    input: null,
    output: { changes: [], failed: "order_found" },
    status: "error",
    ms: 0,
  },
];
// #2A Support Ops Reply V1 + tools (prompt), trial 1
const A_STEPS = [
  {
    n: 1,
    type: "llm",
    name: "Support Ops Reply V1",
    input: [{ role: "system", content: "Support Ops Reply V1 (6 tools)" }],
    output: {
      tool_calls: [{ name: "lookup_order", arguments: { order_id: "A-8830" } }],
    },
    status: "ok",
    model: "gpt-4o-mini",
  },
  {
    n: 2,
    type: "tool_call",
    name: "lookup_order",
    input: { order_id: "A-8830" },
    output: order,
    mode: "mock",
    status: "ok",
  },
  {
    n: 3,
    type: "llm",
    name: "Support Ops Reply V1",
    input: null,
    output: { tool_calls: [{ name: "issue_refund" }] },
    status: "ok",
  },
  {
    n: 4,
    type: "tool_call",
    name: "issue_refund",
    input: { order_id: "A-8830", amount: 149, reason: "damaged" },
    output: { refund_id: "RF-41208", status: "issued" },
    mode: "mock",
    status: "ok",
  },
  {
    n: 5,
    type: "llm",
    name: "Support Ops Reply V1",
    input: null,
    output:
      "I'm sorry the blender arrived with a cracked jug, Ana. I've refunded the full $149 on order A-8830 (refund RF-41208).",
    role: "assistant",
    status: "ok",
  },
  {
    n: 6,
    type: "end_state",
    name: "End state",
    input: null,
    output: { changes: [{ text: "refunds +1" }] },
    status: "ok",
    ms: 0,
  },
];
// #2D the coding harness Code (harness), trial 1
const say = (n, text) => ({
  n,
  type: "llm",
  name: "assistant",
  input: null,
  output: text,
  role: "assistant",
  status: "ok",
});
const cust = (n, text) => ({
  n,
  type: "customer",
  name: "customer (simulated)",
  input: null,
  output: text,
  role: "customer",
  status: "ok",
});
const call = (n, name, input, output) => ({
  n,
  type: "tool_call",
  name,
  input,
  output,
  mode: "live",
  status: "ok",
});
const D_STEPS = [
  cust(1, instruction),
  say(2, "Sorry about the jug, Ana. Let me check A-8830."),
  call(3, "lookup_order", { order_id: "A-8830" }, order),
  call(4, "search_kb", { query: "damaged on arrival refund or replacement" }, [
    { id: "kb-12" },
  ]),
  call(
    5,
    "lookup_order",
    { order_id: "A-8830", include: "replacement_stock" },
    { sku: "BL-700", in_stock: 0 },
  ),
  say(6, "The jug is clearly cracked in your photo."),
  say(
    7,
    "You can have a full refund of $149, or a replacement once the BL-700 is back in stock in about 3 weeks. Which would you prefer?",
  ),
  cust(8, "A refund, please. I don't want to wait three weeks."),
  call(
    9,
    "issue_refund",
    { order_id: "A-8830", amount: 149, reason: "damaged" },
    { refund_id: "RF-47205" },
  ),
  call(
    10,
    "lookup_order",
    { order_id: "A-8830" },
    { id: "A-8830", status: "refunded" },
  ),
  say(
    11,
    "Done: $149 refunded to your Mastercard ending 0932 (refund RF-47205), usually within 5–7 business days.",
  ),
  cust(12, "Do I need to send the broken one back?"),
  say(13, "No, you can recycle it. The photo is enough."),
  cust(14, "Thank you, that was easy."),
  say(15, "Glad I could help, Ana."),
  {
    n: 16,
    type: "end_state",
    name: "End state",
    input: null,
    output: { changes: [{ text: "refunds +1" }] },
    status: "ok",
    ms: 0,
  },
];

const lane = (id, label, steps, runKind) => ({
  id,
  label,
  steps: toLaneSteps(steps, { runKind }),
});
const B = lane("B", "#2B", B_STEPS, "agent");
const A = lane("A", "#2A", A_STEPS, "prompt");
const D = lane("D", "#2D", D_STEPS, "harness");

const cellsOf = (rows) =>
  rows.filter((r) => r.kind === "milestone").map((r) => r.cells);

describe("toLaneSteps", () => {
  it("keeps each lane's own n and classifies kinds", () => {
    expect(B.steps.map((s) => [s.n, s.kind, s.name])).toEqual([
      [1, "think", "classify_intent"],
      [2, "tool", "lookup_order"],
      [3, "think", "reformulate_query"],
      [4, "tool", "lookup_order"],
      [5, "retrieval", "search_kb"],
      [6, "think", "plan"],
      [7, "reply", "respond"],
      [8, "end", "End state: 0 changes"],
    ]);
    expect(A.steps.map((s) => s.kind)).toEqual([
      "think",
      "tool",
      "think",
      "tool",
      "reply",
      "end",
    ]);
    expect(D.steps[1]).toMatchObject({
      n: 2,
      kind: "reply",
      name: "assistant · 9 words",
    });
    expect(D.steps[3]).toMatchObject({
      n: 4,
      kind: "retrieval",
      name: "search_kb",
    });
    expect(B.steps[1].error).toBe("404 Order 8830 not found");
    expect(B.steps.filter((s) => s.isMilestone).length).toBe(5);
  });

  it("normalises DS trace steps through the copied normalizeSteps", () => {
    const trace = [
      {
        order: 0,
        type: "llm",
        name: "classify_intent",
        input: "x",
        output: { order_ref: "7781" },
      },
      {
        order: 1,
        type: "tool",
        name: "lookup_order",
        input: { order_id: "7781" },
        output: null,
        error: "404 Order 7781 not found",
      },
      {
        order: 2,
        type: "llm",
        name: "respond",
        input: "Write the reply.",
        output: null,
      },
    ];
    expect(
      toLaneSteps(trace, { runKind: "agent" }).map((s) => [s.n, s.kind]),
    ).toEqual([
      [1, "think"],
      [2, "tool"],
      [3, "reply"],
    ]);
    const mcp = toLaneSteps(
      [
        {
          order: 0,
          type: "tool",
          name: "mcp__acme-support-sandbox__lookup_order",
          input: {},
        },
      ],
      { runKind: "harness" },
    );
    expect(mcp[0]).toMatchObject({
      name: "lookup_order",
      mappedFrom: "mcp__acme-support-sandbox__lookup_order",
    });
  });

  it("normalize.js copies behave exactly like alignSteps.js", () => {
    const fixture = [
      { type: "llm", name: "classify_intent", output: { a: 1 } },
      {
        type: "tool",
        name: "mcp__acme-support-sandbox__lookup_order",
        input: { order_id: "A-1" },
      },
      { type: "chain", name: "search_kb", output: [{ id: 1 }] },
      { type: "llm", name: "assistant", output: "one two three" },
      { type: "chain", name: "customer (simulated)" },
      { type: "chain", name: "End state", output: { changes: [1, 2] } },
    ];
    expect(copyNormalize(fixture, { harness: true })).toEqual(
      drawerNormalize(fixture, { harness: true }),
    );
    expect(copyNormalize(fixture)).toEqual(drawerNormalize(fixture));
    expect(stableJson({ b: 1, a: [2, { d: 1, c: 2 }] })).toBe(
      '{"a":[2,{"c":2,"d":1}],"b":1}',
    );
  });
});

describe("alignLanes · the mispair test (TS T26)", () => {
  it("#2B vs #2D never pairs a model step with a reply; lookup_order pairs with lookup_order", () => {
    const res = alignLanes(B, [D], { mode: "milestone" });
    cellsOf(res.rows).forEach(([b, d]) => {
      if (b && d)
        expect(
          b.step.kind === d.step.kind ||
            [b.step.kind, d.step.kind].every(
              (k) => k === "tool" || k === "retrieval",
            ),
        ).toBe(true);
      [b, d].forEach((c) => c && expect(c.step.kind).not.toBe("think"));
    });
    const pairs = cellsOf(res.rows)
      .filter(([b, d]) => b && d)
      .map(([b, d]) => [b.step.name, d.step.name]);
    expect(pairs).not.toContainEqual([
      "classify_intent",
      "assistant · 9 words",
    ]);
    expect(pairs).not.toContainEqual([
      "reformulate_query",
      "assistant · 8 words",
    ]);
    expect(pairs[0]).toEqual(["lookup_order", "lookup_order"]);
    // classify_intent rides along with #2B's first lookup_order
    const m1 = res.rows.find((r) => r.index === 1);
    expect(m1.cells[0].subSteps.map((s) => s.name)).toEqual([
      "classify_intent",
    ]);
    expect(m1.cells[1].inputChanged).toBe(true);
    // the final replies pair (not #2D's first chit-chat)
    expect(pairs).toContainEqual(["respond", "assistant · 5 words"]);
  });

  it("#2B vs #2A never pairs classify_intent with Support Ops Reply V1", () => {
    const res = alignLanes(B, [A]);
    const pairs = cellsOf(res.rows)
      .filter(([b, a]) => b && a)
      .map(([b, a]) => [b.step.name, a.step.name]);
    expect(pairs).not.toContainEqual([
      "classify_intent",
      "Support Ops Reply V1",
    ]);
    expect(pairs[0]).toEqual(["lookup_order", "lookup_order"]);
    expect(
      res.rows.find((r) => r.index === 1).cells[1].subSteps.map((s) => s.name),
    ).toEqual(["Support Ops Reply V1"]);
  });

  it("folds unmatched steps into counted gaps and says how it aligned", () => {
    const res = alignLanes(B, [D]);
    const gaps = res.rows.filter((r) => r.kind === "gap");
    expect(gaps.length).toBeGreaterThan(0);
    gaps.forEach((g) => expect(g.change).toBe("only"));
    const first = gaps[0].cells[1];
    expect(first).toMatchObject({
      type: "gap",
      count: 2,
      summary: "1 reply, 1 customer turn",
    });
    const { aligned, total, folded } = res.footer;
    expect(total).toBe(B_STEPS.length + D_STEPS.length);
    expect(aligned + folded).toBe(total);
    expect(howAligned(res)).toBe(
      `${aligned} of ${total} steps match · ${folded} only in one run (folded)`,
    );
    expect(howAlignedHelp()).toBe(
      "Steps are matched on tool name and arguments; internal model steps ride along with the next action. Unmatched steps are folded, never forced into a pair.",
    );
    expect(
      gapSummary([
        ...Array(9).fill({ kind: "tool", name: "Read", mappedFrom: "Read" }),
        ...Array(3).fill({ kind: "file_edit", name: "Edit" }),
        ...Array(2).fill({ kind: "test", name: "Bash" }),
      ]),
    ).toBe("9 file reads, 3 edits, 2 test runs");
  });

  it("other lanes align to the baseline, never to each other", () => {
    const res = alignLanes(B, [A, D]);
    expect(res.lanes.map((l) => l.label)).toEqual(["#2B", "#2A", "#2D"]);
    const m1 = res.rows.find((r) => r.index === 1);
    expect(m1.cells.map((c) => c.step.n)).toEqual([2, 2, 3]);
    expect(m1.change).toBe("changed");
  });

  it("index and none modes", () => {
    const idx = alignLanes(B, [A], { mode: "index" });
    expect(idx.rows).toHaveLength(8);
    expect(idx.rows[0].cells.map((c) => c.step.name)).toEqual([
      "classify_intent",
      "Support Ops Reply V1",
    ]);
    const none = alignLanes(B, [A], { mode: "none" });
    none.rows.forEach((r) => expect(r.change).toBe("none"));
  });
});

describe("markers + frozen copy", () => {
  const checks = {
    B: [
      {
        key: "order_found",
        passed: false,
        evidence: 'lookup_order got 404 for "8830"',
      },
    ],
    A: [{ key: "order_found", passed: true, evidence: null }],
  };

  it("pins the TS #2B T26 numbers", () => {
    const res = alignLanes(B, [A]);
    const m = markers({ rows: res.rows, lanes: [B, A], checks });
    expect(m.firstWrongStep).toMatchObject({
      laneId: "B",
      stepN: 1,
      stepName: "classify_intent",
      milestoneIndex: 1,
    });
    expect(firstWrongStepText(m.firstWrongStep)).toBe(
      "First wrong step: #2B step 1 · classify_intent",
    );
    expect(m.firstDifference).toMatchObject({
      milestoneIndex: 1,
      baseStepN: 2,
    });
    expect(firstDifferenceText(m.firstDifference)).toBe(
      'First difference: step 2 · lookup_order input changed (order_id "8830" → "A-8830")',
    );
    expect(stepNumbersHelp("#2B")).toBe("Step numbers are #2B's own steps.");
  });

  it("#2B vs #2D reads the same first difference (replies and customer turns are context)", () => {
    const res = alignLanes(B, [D]);
    const m = markers({
      rows: res.rows,
      lanes: [B, D],
      checks: { B: checks.B },
    });
    expect(firstDifferenceText(m.firstDifference)).toBe(
      'First difference: step 2 · lookup_order input changed (order_id "8830" → "A-8830")',
    );
    expect(m.firstWrongStep.stepN).toBe(1);
  });

  it("falls back: cause_step_n, then first error, then the end state", () => {
    const res = alignLanes(B, [A]);
    const lanes = [B, A];
    expect(
      markers({
        rows: res.rows,
        lanes,
        checks: { B: [{ key: "k", passed: false, cause_step_n: 4 }] },
      }).firstWrongStep.stepN,
    ).toBe(4);
    expect(
      markers({
        rows: res.rows,
        lanes,
        checks: { B: [{ key: "k", passed: false }] },
      }).firstWrongStep.stepN,
    ).toBe(2);
    const ok = toLaneSteps(A_STEPS, { runKind: "prompt" });
    expect(
      markers({
        rows: [],
        lanes: [{ id: "A", label: "#2A", steps: ok }],
        checks: { A: [{ key: "k", passed: false }] },
      }).firstWrongStep.stepN,
    ).toBe(6);
    expect(
      markers({ rows: res.rows, lanes, checks: {} }).firstWrongStep,
    ).toBeNull();
  });
});
