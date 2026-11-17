import { describe, expect, it } from "vitest";
import {
  conversationSummary,
  dotText,
  formatDuration,
  metricLabel,
  outputText,
  parseConversation,
  replayPreview,
  replayToolChips,
  rowKindOf,
  sourceChip,
  toolCallText,
  toolOutcome,
  traceItemSummary,
  turnDots,
  turnStrip,
  verdictLine,
} from "../itemModel";

const err404 = {
  name: "lookup_order",
  args: { order_id: "8830" },
  result: { error: { code: 404, message: "Order 8830 not found" } },
  status: "error",
};
const C1052 = {
  turns: [
    {
      n: 1,
      role: "customer",
      content:
        "My blender from order A-8830 arrived with a cracked jug, photo attached.",
      attachments: [
        {
          name: "damaged-blender.jpg",
          kind: "image",
          url: "data:image/svg+xml;utf8,x",
        },
      ],
    },
    {
      n: 2,
      role: "agent",
      content: "I couldn't find order 8830.",
      tool_calls: [err404],
      evals: { helpfulness: "Fail" },
    },
    { n: 3, role: "customer", content: "It's A-8830." },
    {
      n: 4,
      role: "agent",
      content: "I still can't find order 8830.",
      tool_calls: [err404],
      evals: { helpfulness: "Fail" },
    },
    {
      n: 5,
      role: "customer",
      content: "This is ridiculous. I'll file a chargeback",
    },
    {
      n: 6,
      role: "agent",
      content: "I'm transferring you to a colleague.",
      tool_calls: [
        {
          name: "escalate_to_human",
          args: { queue: "support", priority: "P3" },
          result: { ticket: "T-47710" },
          status: "ok",
        },
      ],
      evals: { helpfulness: "Pass" },
    },
  ],
  resolution: "Fail",
  resolution_reason:
    "The agent looked up order 8830 instead of A-8830 twice and never offered the refund.",
  failing_turn: 2,
  channel: "chat",
};

describe("conversations", () => {
  const conv = parseConversation(JSON.stringify(C1052));
  it("parses and summarises", () => {
    expect(conv.turns).toHaveLength(6);
    expect(conv.failingTurn).toBe(2);
    expect(conversationSummary(conv)).toBe(
      '6 turns · 3 tool calls · ends: "I\'m transferring you to a colleague."',
    );
    expect(conversationSummary(parseConversation({ turns: [] }))).toBe(
      "No turns",
    );
    expect(parseConversation("nope")).toBeNull();
  });
  it("accepts chat arrays with a system prompt", () => {
    const c = parseConversation([
      { role: "system", content: "Be brief." },
      { role: "user", content: "Hi" },
      { role: "assistant", content: "Hello" },
    ]);
    expect(c.systemPrompt).toBe("Be brief.");
    expect(c.turns.map((t) => t.role)).toEqual(["customer", "agent"]);
    expect(c.failingTurn).toBeNull();
    expect(verdictLine(c)).toBeNull();
  });
  it("dots, strip and verdict", () => {
    const dots = turnDots(conv);
    expect(dotText(dots)).toBe("○●○●○●");
    expect(dots[3].tooltip).toBe("Turn 4 · Agent · Helpfulness Fail");
    expect(dots[0].tooltip).toBe("Turn 1 · Customer · Not scored");
    const strip = turnStrip(conv);
    expect(
      strip.map((s) => `${s.label}${s.mark ? ` ${s.mark}` : ""}`).join(" · "),
    ).toBe(
      "1 Customer · 2 Agent ✗ · 3 Customer · 4 Agent ✗ · 5 Customer · 6 Agent ✓",
    );
    expect(strip.find((s) => s.failing).n).toBe(2);
    expect(verdictLine(conv)).toBe(
      'Conversation: Fail · Resolution · "The agent looked up order 8830 instead of A-8830 twice and never offered the refund." · First failing turn: 2',
    );
  });
  it("tool chips", () => {
    expect(toolCallText(err404)).toBe('lookup_order(order_id="8830") → 404');
    expect(toolOutcome(err404).tone).toBe("error");
    expect(toolOutcome(C1052.turns[5].tool_calls[0])).toMatchObject({
      label: "200",
      tone: "ok",
    });
  });
  it("replay preview and chips", () => {
    expect(replayPreview(conv)).toBe(
      "Replays turn 2 of 6 with the recorded history and 3 recorded tool results (lookup_order, escalate_to_human). No live side effects.",
    );
    expect(replayPreview(conv, { mode: "score_only", tools: "recorded" })).toBe(
      "Scores the 6 recorded turns as they are. No new calls.",
    );
    expect(replayPreview(conv, { mode: "rollout", tools: "live" })).toMatch(
      /Tools call the live sandbox\.$/,
    );
    expect(replayPreview(null)).toMatch(/^Pick a sample row/);
    const chips = replayToolChips(
      [{ name: "lookup_order", args: { order_id: "8830" } }],
      [
        { name: "lookup_order", args: { order_id: "8830" } },
        { name: "issue_refund", args: { order_id: "A-8830" } },
      ],
    );
    expect(chips.map((c) => c.label)).toEqual([
      "lookup_order · same call as production",
      "issue_refund · new call",
    ]);
    expect(
      replayToolChips(
        [{ name: "lookup_order", args: { order_id: "8830" } }],
        [{ name: "lookup_order", args: { order_id: "A-8830" } }],
      )[0].kind,
    ).toBe("changed");
    expect(
      replayToolChips([{ name: "cancel_item", args: {} }], [])[0].label,
    ).toBe("cancel_item · not called");
  });
});

describe("rows and sources", () => {
  it("row kind falls back to the dataset kind", () => {
    expect(rowKindOf({ kind: "task" }, "rows")).toBe("task");
    expect(rowKindOf({}, "task_suite")).toBe("task");
    expect(rowKindOf({ row_meta: {} }, "conversations")).toBe("conversation");
    expect(rowKindOf(null, "rows")).toBe("row");
  });
  it("source chips", () => {
    expect(
      sourceChip({
        kind: "session",
        session_name: "chat-1052",
        session_id: "5e00…",
      }).label,
    ).toBe("Session chat-1052 ↗");
    const tr = sourceChip({
      type: "trace",
      trace_id: "7e100000-0000-4000-8000-000000000201",
      expired: true,
    });
    expect(tr.label).toBe("Trace 7e1… ↗");
    expect(tr.copy).toBe("7e100000-0000-4000-8000-000000000201");
    expect(tr.tooltip).toMatch(/expired/);
    expect(
      sourceChip(
        { type: "upload", file_name: "support-golden-sep.csv" },
        { rowNumber: 37 },
      ).label,
    ).toBe("File support-golden-sep.csv · line 37");
    expect(
      sourceChip({ type: "experiment", run_label: "#2D", experiment_id: "e" })
        .label,
    ).toBe("#2D result");
    expect(sourceChip(null).label).toBe("—");
  });
  it("trace cells", () => {
    expect(
      traceItemSummary(
        JSON.stringify({
          trace_id: "tr_1",
          name: "support-agent",
          spans: 9,
          duration_ms: 2100,
          status: "OK",
        }),
      ).text,
    ).toBe("support-agent · 9 spans · 2.1 s · OK");
    expect(traceItemSummary("x")).toBeNull();
    expect(formatDuration(74000)).toBe("74 s");
    expect(formatDuration(450)).toBe("450 ms");
    expect(formatDuration(120000)).toBe("2 min");
  });
  it("span outputs and metric labels", () => {
    expect(outputText({ content: "Hi" })).toBe("Hi");
    expect(outputText('{"reply":"Done"}')).toBe("Done");
    expect(outputText("plain")).toBe("plain");
    expect(outputText({ a: 1 })).toBe('{"a":1}');
    expect(outputText(null)).toBe("");
    expect(metricLabel("eval:resolution")).toBe("Resolution");
    expect(metricLabel("eval:draft_reply")).toBe("Draft reply");
  });
});

// IT-R3 G5 P2: imported conversations read as conversations.
import {
  failingTurnNote as r3FailingTurnNote,
  findConversationCell as r3FindConversationCell,
  isConversationValue as r3IsConversationValue,
  parseConversation as r3ParseConversation,
} from "../itemModel";

describe("IT-R3 G5 P2 conversations from imports", () => {
  const imported = {
    turns: [
      { n: 1, role: "customer", text: "Where is A-9014?" },
      {
        n: 2,
        role: "agent",
        text: "I've asked the team to call you back.",
        tool_calls: [{ name: "lookup_order", args: { order_id: "A-9014" } }],
      },
    ],
  };

  it("reads `text` turns", () => {
    const conv = r3ParseConversation(imported);
    expect(conv.turns.map((t) => t.content)).toEqual([
      "Where is A-9014?",
      "I've asked the team to call you back.",
    ]);
    expect(conv.turns[1].toolCalls).toHaveLength(1);
  });

  it("knows a conversation-shaped value from other JSON", () => {
    expect(r3IsConversationValue(imported)).toBe(true);
    expect(r3IsConversationValue(JSON.stringify(imported))).toBe(true);
    expect(r3IsConversationValue({ tier: "enterprise" })).toBe(false);
    expect(r3IsConversationValue([1, 2, 3])).toBe(false);
    expect(r3IsConversationValue("hello")).toBe(false);
  });

  it("finds the conversation in a json column named like one", () => {
    const cells = {
      byType: {},
      columns: [
        { name: "customer_message", data_type: "text" },
        { name: "conversation", data_type: "json" },
      ],
      byName: { customer_message: "hi", conversation: imported },
    };
    expect(r3FindConversationCell(cells)?.column.name).toBe("conversation");
    expect(
      r3FindConversationCell({ byType: {}, columns: [], byName: {} }),
    ).toBe(null);
  });

  it("names the failing turn with its eval and reason", () => {
    const conv = r3ParseConversation(imported);
    expect(
      r3FailingTurnNote(conv, {
        outcome: "fail",
        criterion: "Resolution",
        reason: "promised a callback, no schedule_callback call",
        failing_turn: 2,
      }),
    ).toEqual({
      turn: 2,
      text: "Turn 2 · Resolution failed: promised a callback, no schedule_callback call",
    });
    expect(r3FailingTurnNote(conv, null)).toBe(null);
  });
});
