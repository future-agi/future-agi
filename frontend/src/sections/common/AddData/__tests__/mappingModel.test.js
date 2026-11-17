import { describe, expect, it } from "vitest";
import {
  apiCallSnippets,
  autoMapLine,
  clipText,
  conversationSummary,
  filterSummary,
  importBody,
  importDescription,
  includeEstimate,
  loadMapping,
  mappingBody,
  mappingRows,
  normaliseTarget,
  openingMessage,
  saveMapping,
  startStateSummary,
  summaryCounts,
  summaryText,
  targetProblem,
  toChangeSetOps,
  toPython,
  traceItemLabel,
} from "../mappingModel";
import { defaultIncludes, defaultShape, im1Shape } from "../shapes";

const TS = "d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e06";
const OBS = "8e000000-0000-4000-8000-000000000001";
const TASK_COLUMNS = [
  "title",
  "instruction",
  "customer_goal",
  "order_id",
  "start_state",
  "checks",
  "category",
  "difficulty",
  "allowed_tools",
  "source_conversation",
];

const conv = {
  turns: [
    { n: 1, role: "customer", content: "My blender arrived cracked." },
    {
      n: 2,
      role: "agent",
      content: "Let me check.",
      tool_calls: [{ name: "lookup_order" }],
      evals: { helpfulness: "Fail" },
    },
    { n: 3, role: "customer", content: "It's A-8830." },
    {
      n: 4,
      role: "agent",
      content: "Found it.",
      tool_calls: [{ name: "lookup_order" }, { name: "issue_refund" }],
      evals: { helpfulness: "Pass" },
    },
    { n: 5, role: "customer", content: "I'll file a chargeback" },
    { n: 6, role: "agent", content: "Sorry.", evals: { helpfulness: "Fail" } },
  ],
};

describe("conversationSummary", () => {
  it("reads turns, tool calls, the last customer message and dots", () => {
    const s = conversationSummary(conv);
    expect(s.text).toBe(
      '6 turns · 3 tool calls · ends: "I\'ll file a chargeback"',
    );
    expect(s.dots).toEqual(["none", "fail", "none", "pass", "none", "fail"]);
  });
  it("handles a missing conversation without NaN or undefined", () => {
    expect(conversationSummary(null).text).toBe("0 turns · 0 tool calls");
  });
});

describe("mapping", () => {
  const items = [
    {
      cells: Object.fromEntries(TASK_COLUMNS.map((c) => [c, `v-${c}`])),
    },
  ];
  it("auto-maps the six failing sessions as tasks with 0 unmapped", () => {
    const rows = mappingRows({
      columns: TASK_COLUMNS,
      items,
      sourceKind: "sessions",
      shapeId: "task",
      expected: "empty",
    });
    expect(autoMapLine(rows, { shapeId: "task" })).toBe(
      "Fills title, instruction, customer_goal, start_state, checks · every column filled",
    );
    expect(rows.find((r) => r.target === "customer_goal").auto.draft).toBe(
      true,
    );
  });
  it("counts empty columns as unmapped unless left empty on purpose", () => {
    const cols = ["title", "conversation", "success_criteria"];
    const its = [{ cells: { title: "x", conversation: conv } }];
    const base = { columns: cols, items: its, sourceKind: "sessions" };
    expect(autoMapLine(mappingRows(base))).toBe(
      "Fills title, conversation · 1 column stays empty",
    );
    const rows = mappingRows({
      ...base,
      overrides: { success_criteria: { mode: "empty" } },
    });
    expect(autoMapLine(rows)).toBe(
      "Fills title, conversation · every column filled",
    );
  });
  it("leaves expected empty by default (never the model's answer)", () => {
    const rows = mappingRows({
      columns: ["input", "expected_output"],
      items: [{ cells: { input: "hi", expected_output: null } }],
      sourceKind: "traces",
      expected: "empty",
    });
    expect(rows[1].auto.label).toBe("Leave empty (needs review)");
    expect(rows[1].unmapped).toBe(false);
    const prod = mappingRows({
      columns: ["expected_output"],
      items: [],
      sourceKind: "traces",
      expected: "production_output",
    });
    expect(prod[0].auto.warn).toBe(true);
  });
  it("sends only overrides, and never an invalid path", () => {
    expect(
      mappingBody({
        instruction: { mode: "field", field: "last_customer_message" },
        title: { mode: "path", path: "$.turns[0].content" },
        bad: { mode: "path", path: "$.[", error: "Invalid path" },
        expected: { mode: "empty" },
      }),
    ).toEqual([
      { target: "instruction", from: { field: "last_customer_message" } },
      { target: "title", from: { path: "$.turns[0].content" } },
      { target: "expected", from: { empty: true } },
    ]);
  });
});

describe("importBody", () => {
  it("pins Observe's selected sessions", () => {
    const body = importBody({
      sourceKind: "sessions",
      projectId: OBS,
      sessionIds: ["chat-1052"],
      shapeId: "task",
      target: { dataset_id: TS },
      sample: 6,
    });
    expect(body).toMatchObject({
      source: { kind: "sessions", project_id: OBS, session_ids: ["chat-1052"] },
      shape: "task",
      expected: "empty",
      target: { dataset_id: TS },
      sample: 6,
    });
  });
  it("filters by Resolution when nothing is pinned", () => {
    const body = importBody({
      sourceKind: "sessions",
      projectId: OBS,
      resolution: "Fail",
      shapeId: "conversation",
    });
    expect(JSON.stringify(body.source.filters)).toContain("Fail");
  });
  it("adds include flags and dedupe for traces", () => {
    const body = importBody({
      sourceKind: "traces",
      projectId: OBS,
      traceIds: ["t1", "t2"],
      shapeId: "per_span",
      includes: defaultIncludes(),
    });
    expect(body.shape).toBe("trace");
    expect(body.span_filter).toEqual({ name: "respond" });
    expect(body.include.child_spans).toBe(true);
    expect(body.dedupe).toBe("source_trace_id");
  });
  it("maps queue items to reviewer corrections", () => {
    const body = importBody({ sourceKind: "queue", queueId: "q1" });
    expect(body.source).toEqual({
      kind: "queue",
      queue_id: "q1",
      completed_only: true,
    });
    expect(body.expected).toBe("human_correction");
  });
  it("normalises both spellings of a new target", () => {
    expect(normaliseTarget({ new: { name: "A" } }, "task")).toEqual({
      new: { name: "A", kind: "task_suite" },
    });
    expect(
      normaliseTarget({ new_dataset: { name: "B", kind: "rows" } }),
    ).toEqual({ new: { name: "B", kind: "rows" } });
    expect(normaliseTarget(null)).toBeNull();
  });
  it("picks shapes by source", () => {
    expect(defaultShape("sessions", "task_suite")).toBe("task");
    expect(defaultShape("sessions", "rows")).toBe("conversation");
    expect(im1Shape("traces", "trace_item")).toBe("trace");
  });
});

describe("copy", () => {
  it("summarises the source in one line", () => {
    expect(
      filterSummary({
        sourceKind: "sessions",
        projectName: "support-prod",
        resolution: "Fail",
        count: 6,
      }),
    ).toBe("support-prod · last 7 days · Resolution = Fail · 6 sessions");
    expect(
      filterSummary({
        sourceKind: "sessions",
        projectName: "support-prod",
        pinnedCount: 6,
      }),
    ).toBe("support-prod · 6 sessions");
  });
  it("writes the summary line and subtracts unticked items", () => {
    const summary = { add: 6, skip_duplicates: 0, needs_review: 6 };
    expect(summaryText(summaryCounts(summary))).toBe(
      "Add 6 rows · skip 0 duplicates (already imported) · 6 rows need a quick look (fields we guessed)",
    );
    const items = [{ temp_id: "imp-1", status: "new", needs_review: true }];
    expect(summaryText(summaryCounts(summary, items, new Set(["imp-1"])))).toBe(
      "Add 5 rows · skip 0 duplicates (already imported) · 5 rows need a quick look (fields we guessed)",
    );
  });
  it("prefills the version description", () => {
    expect(
      importDescription({
        count: 6,
        projectName: "support-prod",
        sourceKind: "sessions",
        shapeId: "task",
        resolution: "Fail",
      }),
    ).toBe(
      "Added 6 rows from support-prod sessions as tasks (Resolution = Fail, last 7 days)",
    );
  });
  it("estimates the import size", () => {
    expect(includeEstimate(defaultIncludes(), 2)).toMatch(
      /^≈ \d+(\.\d)? KB per trace · \d+(\.\d)? KB total$/,
    );
  });
  it("clips to 80 chars and labels traces", () => {
    expect(clipText("x".repeat(100))).toHaveLength(80);
    expect(
      traceItemLabel({
        trace: { name: "support-agent" },
        summary: { total_spans: 4, total_latency_ms: 2142, error_count: 0 },
      }),
    ).toBe("support-agent · 4 spans · 2.1 s · OK");
    expect(startStateSummary({ orders: [{ id: "A-8830" }] })).toBe(
      "1 order · A-8830",
    );
  });
  it("blocks As tasks on a dataset that is not a task suite", () => {
    expect(
      targetProblem({
        shapeId: "task",
        sourceKind: "sessions",
        targetKind: "rows",
      }),
    ).toMatch(/task suite/);
    expect(
      targetProblem({
        shapeId: "task",
        sourceKind: "sessions",
        targetKind: "task_suite",
      }),
    ).toBeNull();
  });
});

describe("commit and API call", () => {
  it("turns IM1 operations into change-set ops with edits applied", () => {
    const ops = toChangeSetOps(
      [
        {
          op: "add_row",
          temp_id: "imp-2",
          kind: "task",
          source: { kind: "session" },
          expected_source: "empty",
          cells: { title: "Old" },
          cells_by_column_id: { c1: "Old" },
        },
      ],
      {
        edits: { "imp-2": { title: "New" } },
        columnIdByName: { title: "c1" },
        hint: "Added 1 row",
        stamp: "x-",
      },
    );
    expect(ops[0]).toMatchObject({
      op: "add_row",
      temp_id: "imp-x-1",
      kind: "task",
      expected_source: "empty",
      cells: { c1: "New" },
      description_hint: "Added 1 row",
    });
  });
  it("writes cURL and Python with real ids and Python literals", () => {
    const { curl, python } = apiCallSnippets({
      source: { kind: "sessions", session_ids: ["chat-1052"] },
      target: { dataset_id: TS },
      mapping: [],
      flag: true,
      none: null,
      sample: 6,
    });
    expect(curl).toContain(TS);
    expect(curl).toContain('"op": "commit"');
    expect(curl).not.toContain("sample");
    expect(python).toContain('"flag": True');
    expect(python).toContain('"none": None');
    expect(toPython({ s: "null true" })).toContain('"null true"');
  });
  it("remembers a mapping with a storage that may throw", () => {
    const mem = new Map();
    const storage = {
      getItem: (k) => mem.get(k) ?? null,
      setItem: (k, v) => mem.set(k, v),
      removeItem: (k) => mem.delete(k),
    };
    const o = { title: { mode: "empty" } };
    expect(saveMapping(OBS, TS, o, storage)).toBe(true);
    expect(loadMapping(OBS, TS, storage)).toEqual(o);
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(loadMapping(OBS, TS, broken)).toBeNull();
    expect(saveMapping(OBS, TS, o, broken)).toBe(false);
  });
});

describe("openingMessage", () => {
  it("returns the first customer message, else empty", () => {
    const conv = {
      turns: [
        { role: "assistant", content: "Hi" },
        { role: "customer", content: "My blender arrived cracked." },
        { role: "customer", content: "Hello?" },
      ],
    };
    expect(openingMessage(conv)).toBe("My blender arrived cracked.");
    expect(openingMessage({ turns: [] })).toBe("");
    expect(openingMessage(null)).toBe("");
  });
});
