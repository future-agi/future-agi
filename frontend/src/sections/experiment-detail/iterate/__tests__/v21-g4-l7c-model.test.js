// v2.1 g4 (L7c skeptic must-fix): the dock's price follows the engine's
// cost model (model and max tokens), the scope sends its rows, an unsaved
// agent edit offers no Publish, the node prompt is read-only text with a
// way to change it, and the copy names only what the dock changes.
import { describe, expect, it } from "vitest";
import { buildIterateBody, childCostRatio, previewOf } from "../iterateModel";
import { publishGateOf } from "../publishGate";
import { nodePromptHowTo } from "../changes/AgentNodeChange";
import { ITERATE_TIP } from "../IterateButton";
import {
  MAX_RERUN_TRIES,
  childPriceRatio,
  clampRerunTries,
} from "src/sections/common/RunSources/RerunWithChangesDialog";
import {
  connectionToolsOf,
  environmentMcpWithout,
} from "src/sections/develop-detail/TaskSuite/EnvironmentDrawer";
import { copyableRun } from "src/sections/develop-detail/Experiment/StartFromExperiment";

describe("v2.1 g4 · prices from the engine's cost model", () => {
  it("max tokens moves the dock preview (gpt-4o-mini 400 → 120)", () => {
    const base = {
      rows: 56,
      trials: 1,
      kind: "prompt",
      costPerAttemptUsd: 0.004,
      key: "A",
      fromModel: "gpt-4o-mini",
      toModel: "gpt-4o-mini",
      fromMaxTokens: 400,
    };
    const same = previewOf({ ...base, toMaxTokens: 400 });
    const cut = previewOf({ ...base, toMaxTokens: 120 });
    expect(cut.usd).toBeLessThan(same.usd);
    expect(cut.modelNote).toMatch(/max 120 tokens/);
    expect(
      childCostRatio({
        kind: "prompt",
        fromModel: "gpt-4o-mini",
        toModel: "claude-sonnet-4-5",
        fromMaxTokens: 400,
      }),
    ).toBeGreaterThan(5);
  });

  it("the rerun dialog prices with the same ratio and allows at most 3 tries", () => {
    expect(MAX_RERUN_TRIES).toBe(3);
    expect(clampRerunTries(10)).toBe(3);
    expect(clampRerunTries(0)).toBe(1);
    expect(clampRerunTries("2")).toBe(2);
    const dialog = childPriceRatio({
      kind: "prompt",
      parentModel: "gpt-4o-mini",
      childModel: "claude-sonnet-4-5",
      parentMaxTokens: 400,
    });
    const dock = childCostRatio({
      kind: "prompt",
      fromModel: "gpt-4o-mini",
      toModel: "claude-sonnet-4-5",
      fromMaxTokens: 400,
    });
    expect(dialog).toBeCloseTo(dock, 9);
    expect(
      childPriceRatio({
        kind: "prompt",
        parentModel: "gpt-4o-mini",
        childModel: "gpt-4o-mini",
      }),
    ).toBe(1);
  });
});

describe("v2.1 g4 · dock body and publish gate", () => {
  it("a failed scope sends the rows the dock counted", () => {
    const body = buildIterateBody({
      parentRunId: "A",
      scopeType: "regressed",
      rowIds: ["r1", "r2"],
      trials: 1,
    });
    expect(body.scope).toEqual({ type: "regressed", row_ids: ["r1", "r2"] });
    const all = buildIterateBody({
      parentRunId: "A",
      scopeType: "all",
      rowIds: ["r1"],
      trials: 1,
    });
    expect(all.scope.row_ids).toBeUndefined();
  });

  it("an unsaved agent edit (`v3 (edited)`) offers no Publish", () => {
    const cand = {
      run_id: "c",
      label: "#2H",
      listed: true,
      config: { agent: { version: "v3 (edited)", unsaved: true } },
    };
    const gate = publishGateOf({
      draftVersion: { version: "v3 (edited)", draft: true },
      cand,
      canEdit: true,
    });
    expect(gate.show).toBe(false);
    const saved = publishGateOf({
      draftVersion: { version: "v3.2", draft: true },
      cand: { ...cand, config: { agent: { version: "v3.2" } } },
      canEdit: true,
    });
    expect(saved.show).toBe(true);
  });
});

describe("v2.1 g4 · copy and small rules", () => {
  it("the node prompt says how it changes; the tip names what the dock changes", () => {
    expect(nodePromptHowTo({ hasFixes: true })).toMatch(/Read-only.*fixes/);
    expect(nodePromptHowTo({ hasFixes: false })).toMatch(/Agent Playground/);
    expect(ITERATE_TIP).not.toMatch(/Change the prompt/);
    expect(ITERATE_TIP).toMatch(/model, settings or tools/);
  });

  it("Apply on an address no server answers saves no MCP tools", () => {
    const mcp = {
      name: "acme",
      url: "http://mcp.acme:8080",
      tool_names: ["lookup_order"],
    };
    expect(connectionToolsOf(mcp, "url", "http://mcp.acme:8080")).toEqual([
      "lookup_order",
    ]);
    expect(connectionToolsOf(mcp, "url", "http://other:9")).toEqual([]);
    expect(environmentMcpWithout()).toEqual({ tool_names: [], tools: 0 });
  });

  it("Start from copies prompt, agent and endpoint runs only", () => {
    expect(copyableRun({ target_type: "prompt" })).toBe(true);
    expect(copyableRun({ config: { kind: "http" } })).toBe(true);
    expect(copyableRun({ config: { kind: "harness" } })).toBe(false);
    expect(copyableRun({ config: { kind: "code" } })).toBe(false);
  });
});
