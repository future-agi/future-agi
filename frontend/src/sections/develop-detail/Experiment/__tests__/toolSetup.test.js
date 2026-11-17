import { describe, expect, it } from "vitest";
import {
  BLOCK_HELP,
  MENU_HELP,
  TOOL_MODES,
  blockHelpFor,
  menuHelpFor,
  recordedHelpFor,
  addedToolsOf,
  cardTools,
  effectiveMode,
  fixtureMatchText,
  fixtureResultText,
  fullSignature,
  modeSummary,
  paramSignature,
  promptToolsPayload,
  toolIssues,
  toolWarnings,
  versionToolsOf,
} from "../toolSetup";
import { configurationIssues, configurationWarnings } from "../stepIssues";
import { withPromptTools, startFromPayload } from "../experimentFormValues";

const fn = (name, props, required = []) => ({
  type: "function",
  function: {
    name,
    description: `${name} tool`,
    parameters: { type: "object", properties: props, required },
  },
});
const OPS = [
  fn("lookup_order", { order_id: { type: "string" } }, ["order_id"]),
  fn("lookup_customer", { email: { type: "string" } }, ["email"]),
  fn(
    "issue_refund",
    {
      order_id: { type: "string" },
      amount: { type: "number" },
      reason: { type: "string" },
    },
    ["order_id", "amount"],
  ),
  fn("schedule_callback", { slot: { type: "string" } }),
  fn("create_ticket", { summary: { type: "string" } }),
  fn("escalate_to_human", { queue: { type: "string" } }),
];
const version = {
  id: "v1",
  template_version: "V1",
  prompt_config_snapshot: { configuration: { tools: OPS } },
};
const setupOf = (extra = {}) => ({
  versionTools: versionToolsOf(version),
  versionLabel: "Support Ops Reply V1",
  mode: "mock",
  perTool: {},
  endpoints: {},
  recorded: { available: false, sessions: 0 },
  ...extra,
});
const card = (setup, added = []) => ({
  targetType: "prompt",
  name: "Support Ops Reply",
  promptVersion: "v1",
  model: [{ value: "gpt-4o-mini" }],
  configuration: { toolChoice: "auto", tools: added },
  toolSetup: setup,
});

describe("toolSetup", () => {
  it("reads the version's six tools with their signatures", () => {
    const tools = versionToolsOf(version);
    expect(tools.map((t) => t.name)).toEqual([
      "lookup_order",
      "lookup_customer",
      "issue_refund",
      "schedule_callback",
      "create_ticket",
      "escalate_to_human",
    ]);
    expect(paramSignature(tools[0])).toBe("{order_id}");
    expect(fullSignature(tools[2])).toBe(
      "issue_refund(order_id: string, amount: number, reason?: string)",
    );
  });

  it("adds ConfigTool picks after the version's tools, without duplicates", () => {
    const added = [
      { id: "x", tool: { tool: { name: "lookup_order", config: OPS[0] } } },
      {
        id: "y",
        tool: { tool: { name: "search_kb", config: fn("search_kb", {}) } },
      },
    ];
    expect(addedToolsOf(added).map((t) => t.name)).toEqual([
      "lookup_order",
      "search_kb",
    ]);
    const tools = cardTools(setupOf(), added);
    expect(tools).toHaveLength(7);
    expect(tools[6]).toMatchObject({ name: "search_kb", added: true });
  });

  it("summarises modes as one line", () => {
    const tools = versionToolsOf(version);
    expect(modeSummary(setupOf(), tools)).toBe("all Mock");
    expect(modeSummary(setupOf(), [])).toBe("none");
    const per = setupOf({
      mode: "per_tool",
      perTool: { lookup_order: "live", issue_refund: "recorded" },
      recorded: { available: true, sessions: 6 },
    });
    expect(modeSummary(per, tools)).toBe("4 Mock · 1 Recorded · 1 Live");
  });

  it("falls back to Mock when recordings are not available", () => {
    expect(effectiveMode(setupOf({ mode: "recorded" }), "lookup_order")).toBe(
      "mock",
    );
    expect(
      effectiveMode(
        setupOf({ mode: "recorded", recorded: { available: true } }),
        "lookup_order",
      ),
    ).toBe("recorded");
  });

  it("blocks a Live tool without an endpoint (stepIssues)", () => {
    const live = setupOf({
      mode: "per_tool",
      perTool: { lookup_order: "live" },
    });
    expect(toolIssues(card(live))).toEqual([
      "Live tool lookup_order has no endpoint",
    ]);
    const values = {
      name: "x",
      experimentType: "llm",
      promptConfig: [card(live)],
    };
    expect(configurationIssues(values)).toContain(
      "Live tool lookup_order has no endpoint",
    );
    const ok = setupOf({
      mode: "per_tool",
      perTool: { lookup_order: "live" },
      endpoints: { lookup_order: "https://sandbox.acme.ai/tools/lookup_order" },
    });
    expect(toolIssues(card(ok))).toEqual([]);
  });

  it("warns how many rows have no recording", () => {
    const rec = setupOf({
      mode: "recorded",
      recorded: { available: true, sessions: 6 },
    });
    // M9 (merge round 3, W6 integration open item tools:all-mock): only a call
    // to a Recorded tool on a row without a recording follows Unrecorded
    // calls, so the line names the tools; it was "they will use mocks" /
    // "they will fail", which claimed every such row fails.
    expect(toolWarnings(card(rec), { rowCount: 30 })).toEqual([
      "24 rows have no recording (the 6 recorded sessions answer the first rows in run order); a call to any of the 6 Recorded tools on them uses Mock",
    ]);
    expect(
      toolWarnings(card({ ...rec, onUnrecorded: "fail" }), { rowCount: 7 }),
    ).toEqual([
      "1 row has no recording (the 6 recorded sessions answer the first rows in run order); a call to any of the 6 Recorded tools on it fails the row",
    ]);
    expect(toolWarnings(card(setupOf()), { rowCount: 30 })).toEqual([]);
    expect(
      configurationWarnings({ promptConfig: [card(rec)] }, { rowCount: 30 }),
    ).toHaveLength(1);
  });

  it("names the Recorded tools and what a call on an unrecorded row does (M9)", () => {
    const one = setupOf({
      mode: "per_tool",
      perTool: { issue_refund: "recorded" },
      recorded: { available: true, sessions: 6 },
      onUnrecorded: "fail",
    });
    expect(toolWarnings(card(one), { rowCount: 248 })).toEqual([
      "242 rows have no recording (the 6 recorded sessions answer the first rows in run order); a call to issue_refund on them fails the row",
    ]);
    const two = {
      ...one,
      perTool: { issue_refund: "recorded", lookup_order: "recorded" },
      onUnrecorded: "live",
    };
    expect(toolWarnings(card(two), { rowCount: 20 })).toEqual([
      "14 rows have no recording (the 6 recorded sessions answer the first rows in run order); a call to lookup_order or issue_refund on them is made live",
    ]);
    expect(
      toolWarnings(card({ ...one, perTool: {} }), { rowCount: 20 }),
    ).toEqual([]);
    // The help lines follow the same choice; Use mock keeps the spec copy.
    expect(recordedHelpFor("mock")).toBe(TOOL_MODES.recorded.help);
    expect(recordedHelpFor(undefined)).toBe(TOOL_MODES.recorded.help);
    expect(recordedHelpFor("fail")).toBe(
      "Returns what this tool returned in production for the row's source session. A call on a row without a recording fails the row.",
    );
    expect(menuHelpFor("recorded", "mock")).toBe(MENU_HELP.recorded);
    expect(menuHelpFor("mock", "fail")).toBe(MENU_HELP.mock);
    expect(menuHelpFor("recorded", "fail")).toBe(
      "Each tool replays what it returned in production for the row. No recording: the call fails the row.",
    );
    expect(blockHelpFor("recorded")).toBe(
      "Each tool returns what it returned in production for the row; rows without a recording use Mock.",
    );
    expect(blockHelpFor("recorded", "live")).toBe(
      "Each tool returns what it returned in production for the row; on a row without a recording a call is made live.",
    );
    expect(blockHelpFor("mock", "fail")).toBe(BLOCK_HELP);
  });

  it("builds the create-body keys per prompt run", () => {
    const live = setupOf({
      mode: "per_tool",
      perTool: { lookup_order: "live" },
      endpoints: { lookup_order: " https://x.acme.ai/lookup " },
      maxToolTurns: "6",
    });
    const body = promptToolsPayload(card(live));
    expect(body.tools).toHaveLength(6);
    expect(body.tools[0]).toMatchObject({
      name: "lookup_order",
      mode: "live",
      endpoint: "https://x.acme.ai/lookup",
    });
    expect(body.tools[1]).toMatchObject({ mode: "mock", endpoint: null });
    expect(body.max_tool_turns).toBe(6);
    expect(body.on_unrecorded).toBe("mock");
    expect(promptToolsPayload(card({ versionTools: [] }))).toEqual({});

    const cards = [card(live)];
    const entries = [
      { target_type: "prompt", model: "gpt-4o-mini" },
      { target_type: "agent" },
    ];
    const runs = [{ configIndex: 0 }, { configIndex: 1 }];
    const out = withPromptTools(entries, cards, runs);
    expect(out[0].tools).toHaveLength(6);
    expect(out[1]).toEqual({ target_type: "agent" });
  });

  it("formats fixtures read-only", () => {
    expect(fixtureMatchText({ order_id: "A-7781" })).toBe(
      'order_id = "A-7781"',
    );
    expect(fixtureMatchText("*")).toBe("default");
    expect(
      fixtureResultText({ error: { code: 404, message: "Order not found" } }),
    ).toBe('404 {"error":{"code":404,"message":"Order not found"}}');
  });

  it("keeps the spec copy", () => {
    expect(BLOCK_HELP).toBe(
      "Each tool returns a test answer we generate from its schema and the row. Nothing real is called.",
    );
    expect(TOOL_MODES.mock.help).toBe(
      "Returns a test answer. Fast and repeatable. Nothing real is called.",
    );
    expect(TOOL_MODES.recorded.help).toBe(
      "Returns what this tool returned in production for the row's source session. Rows without a recording use Mock.",
    );
  });

  it("start-from keys only when the version moved and carry-over is on", () => {
    expect(startFromPayload({})).toEqual({});
    const sf = { experimentId: "e1", preset: "bakeoff-v2", versionMoved: true };
    expect(startFromPayload({ startFrom: sf })).toEqual({
      preset: "bakeoff-v2",
      carry_over_from_experiment_id: "e1",
    });
    expect(
      startFromPayload({ startFrom: { ...sf, carryOver: false } }),
    ).toEqual({ preset: "bakeoff-v2" });
  });
});
