// W6 A1: run details name the limits per row and each Live tool's host.
import { describe, expect, it } from "vitest";
import { liveToolHosts, runSettingsLines } from "../runSettingsLines";

describe("runSettingsLines", () => {
  const config = {
    kind: "prompt",
    row_limits: { tokens: 600, cost_usd: 0.05 },
    tools: [
      { name: "lookup_order", mode: "recorded" },
      {
        name: "issue_refund",
        mode: "live",
        endpoint: "https://sandbox.acme.ai/tools/issue_refund",
      },
      { name: "search_kb", mode: "live", endpoint: "not a url" },
    ],
  };
  it("lists the live hosts and the limits", () => {
    expect(liveToolHosts(config)).toEqual(["issue_refund → sandbox.acme.ai"]);
    expect(runSettingsLines(config)).toEqual([
      ["Limits per row", "600 tokens · $0.050"],
      ["Live tools", "issue_refund → sandbox.acme.ai"],
    ]);
  });
  it("is empty for a run without them", () => {
    expect(runSettingsLines({ kind: "prompt" })).toEqual([]);
    expect(runSettingsLines(null)).toEqual([]);
  });
});
