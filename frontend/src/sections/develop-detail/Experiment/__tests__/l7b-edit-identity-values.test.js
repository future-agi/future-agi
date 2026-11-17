// options-truth L7b G2 (RC2): Edit and Duplicate rebuild each card from its
// run's config, and the confirm names exactly what an edit changes.
import { describe, expect, it } from "vitest";
import {
  editChangeSummary,
  entryOfCard,
  valuesFromExperiment,
  withoutCard,
} from "../experimentFormValues";
import { flattenRuns } from "../runPlan";

const SERVER = {
  name: "acme-support-sandbox",
  transport: "stdio",
  command: "npx acme-support-mcp",
  url: "",
  env_secret: "ACME_KEY",
};
const CARD = {
  name: "Globex Help",
  version: "1.2",
  url: "https://help.globex.test/a2a",
  skills: [{ id: "refund_request" }],
};

const detail = {
  name: "#9",
  trials: 2,
  runs: [
    {
      run_id: "R9A",
      label: "#9A",
      letter: "A",
      name: "HTTP endpoint · POST agent.acme.test/v2/reply",
      target_type: "http",
      target: { url: "https://agent.acme.test/v2/reply", method: "POST" },
      config: {
        kind: "http",
        endpoint: {
          url: "https://agent.acme.test/v2/reply",
          method: "POST",
          headers: [{ name: "X-Tier", value: "gold" }],
          body_template: '{"q": "{{customer_message}}"}',
          output_path: "$.output.text",
        },
        limits: { tries: 4, max_cost_usd: 0.5 },
        variable_mapping: { customer_message: "c-intent" },
      },
    },
    {
      run_id: "R9B",
      label: "#9B",
      letter: "B",
      name: "A2A · Globex Help 1.2 · skill refund_request",
      target_type: "http",
      target: { source: "a2a", url: CARD.url, skill: "refund_request" },
      config: {
        kind: "a2a",
        a2a: {
          card_url: "https://help.globex.test",
          card: CARD,
          skill_id: "refund_request",
          text_column: "c1",
          data_columns: ["c2"],
          context_column: "c3",
          auth: { name: "Authorization", secret: "GLOBEX" },
          agent: "Globex Help",
          version: "1.2",
        },
        limits: { tries: 2 },
      },
    },
    {
      run_id: "R9C",
      label: "#9C",
      letter: "C",
      name: "MCP · acme-support-sandbox · lookup_order",
      target_type: "http",
      target: { source: "mcp_tool", server: SERVER.name, tool: "lookup_order" },
      config: {
        kind: "mcp_tool",
        mcp_tool: {
          server: SERVER,
          tool: "lookup_order",
          arguments: { order_id: { column: "c4" } },
          side_effects: "dry_run",
        },
        safety: { side_effects: "dry_run" },
      },
    },
    {
      run_id: "R9D",
      label: "#9D",
      letter: "D",
      target_type: "prompt",
      target: {
        prompt_id: "p1",
        prompt_name: "Support",
        prompt_version_id: "pv4",
        prompt_version_name: "V4",
        model: "gpt-4o-mini",
        model_params: { temperature: 0.7, max_tokens: 400 },
      },
      config: {
        kind: "prompt",
        variable_mapping: {
          customer_message: "c-intent",
          customer_tier: { kind: "constant", value: "enterprise" },
        },
      },
    },
  ],
};

describe("valuesFromExperiment · each run comes back as its own card", () => {
  const v = valuesFromExperiment(detail, { mode: "edit" });
  const [http, a2a, mcp, prompt] = v.promptConfig;

  it("HTTP: stored body, output path, headers, tries, cost cap, mapping", () => {
    expect(http.targetType).toBe("http");
    expect(http._itemId).toBe("R9A");
    expect(http.http).toMatchObject({
      url: "https://agent.acme.test/v2/reply",
      method: "POST",
      bodyTemplate: '{"q": "{{customer_message}}"}',
      outputPath: "$.output.text",
      headers: [{ name: "X-Tier", value: "gold" }],
    });
    expect(http.limits).toEqual({ tries: 4, maxCostUsd: 0.5 });
    expect(http.variableMapping).toEqual({ customer_message: "c-intent" });
  });

  it("A2A: an A2A card with its card, skill, parts and auth (never HTTP)", () => {
    expect(a2a.targetType).toBe("a2a");
    expect(a2a.a2a).toMatchObject({
      card_url: "https://help.globex.test",
      card: CARD,
      skill_id: "refund_request",
      text_column: "c1",
      data_columns: ["c2"],
      context_column: "c3",
      auth: { name: "Authorization", secret: "GLOBEX" },
    });
    expect(a2a.a2a.agent).toBeUndefined();
    // tries 2 = the experiment's Trials: not its own
    expect(a2a.limits).toBeUndefined();
  });

  it("MCP: server incl. env, tool, arguments, side effects", () => {
    expect(mcp.targetType).toBe("mcp_tool");
    expect(mcp.mcp_tool.server).toMatchObject(SERVER);
    expect(mcp.mcp_tool.tool).toBe("lookup_order");
    expect(mcp.mcp_tool.arguments).toEqual({ order_id: { column: "c4" } });
    expect(mcp.mcp_tool.side_effects).toBe("dry_run");
  });

  it("prompt: params and variable mapping of its run", () => {
    expect(prompt.modelParams["gpt-4o-mini"]).toEqual({
      temperature: 0.7,
      max_tokens: 400,
    });
    expect(prompt.variableMapping).toEqual({
      customer_message: "c-intent",
      customer_tier: { kind: "constant", value: "enterprise" },
    });
    expect(prompt.model[0].runId).toBe("R9D");
  });

  it("an unchanged form re-runs nothing", () => {
    const s = editChangeSummary(v, v, {
      runs: detail.runs,
      flatten: flattenRuns,
    });
    expect(s.rerun).toEqual([]);
    expect(s.kept).toEqual(["#9A", "#9B", "#9C", "#9D"]);
  });

  it("a changed kept card is listed with what changed", () => {
    const next = JSON.parse(JSON.stringify(v));
    next.promptConfig[0].limits = { tries: 3 };
    next.promptConfig[0].http.outputPath = "$.reply";
    next.promptConfig[0].http.headers = [];
    next.promptConfig[2].mcp_tool.arguments = { order_id: { column: "c9" } };
    next.promptConfig[3].variableMapping = { customer_message: "c-question" };
    const s = editChangeSummary(v, next, {
      runs: detail.runs,
      flatten: flattenRuns,
    });
    expect(s.rerun).toEqual(["#9A", "#9C", "#9D"]);
    expect(s.kept).toEqual(["#9B"]);
    expect(s.changes["#9A"]).toEqual([
      "output path $.output.text → $.reply",
      "− header X-Tier",
      "tries 4 → 3",
      "cost cap $0.50 → –",
    ]);
    expect(s.changes["#9C"]).toEqual(["arguments changed"]);
    expect(s.changes["#9D"][0]).toMatch(/^mapping customer_message ← c-intent/);
    expect(s.text).toMatch(
      /^Re-run: #9A \(output path \$\.output\.text → \$\.reply; − header X-Tier; tries 4 → 3; cost cap \$0\.50 → –\)/,
    );
  });

  it("the body entry of a card carries its limits and mapping", () => {
    const e = entryOfCard(http, flattenRuns(v.promptConfig)[0]);
    expect(e).toMatchObject({
      target_type: "http",
      limits: { tries: 4, max_cost_usd: 0.5 },
      variable_mapping: { customer_message: "c-intent" },
    });
  });

  it("Duplicate & edit copies without run ids", () => {
    const d = valuesFromExperiment(detail, { mode: "duplicate" });
    expect(d.promptConfig.map((c) => c.targetType)).toEqual([
      "http",
      "a2a",
      "mcp_tool",
      "prompt",
    ]);
    expect(JSON.stringify(d.promptConfig)).not.toContain("R9A");
    expect(d.promptConfig[3].modelParams["gpt-4o-mini"].temperature).toBe(0.7);
  });
});

describe("withoutCard · Remove takes exactly one card", () => {
  it("removes the card at the index, even when ids repeat", () => {
    const cards = [{ _itemId: "x" }, { _itemId: "x", n: 2 }, { _itemId: "y" }];
    expect(withoutCard(cards, 1)).toEqual([{ _itemId: "x" }, { _itemId: "y" }]);
    expect(withoutCard(cards, 2)).toHaveLength(2);
    expect(withoutCard([{}, {}], 0)).toHaveLength(1);
  });
});
