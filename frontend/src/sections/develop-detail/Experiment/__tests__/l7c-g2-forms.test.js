// L7c g2 · card logic: tries above the engine's 10 are refused
// (knob:limits:tries), number arguments refuse text (knob:mcp:arguments),
// A2A / MCP run names come from their config (source:running:a2a,
// source:running:mcp_tool), Test offers the response's text paths
// (knob:http:output-path).
import { describe, expect, it } from "vitest";
import {
  a2aRunName,
  clampTries,
  isAutoA2aName,
  isAutoMcpName,
  MAX_TRIES_PER_ROW,
  mcpRunName,
} from "../endpointCards-g2";
import { argumentIssues } from "../McpToolRunForm";
import { textPathsOf } from "src/sections/common/RunSources/endpoint/previewModel";

describe("knob:limits:tries", () => {
  it("refuses more than 10 tries a row", () => {
    expect(MAX_TRIES_PER_ROW).toBe(10);
    expect(clampTries("25")).toEqual({ value: 10, refused: 25 });
    expect(clampTries("3")).toEqual({ value: 3, refused: null });
    expect(clampTries("")).toEqual({ value: null, refused: null });
  });
});

describe("knob:mcp:arguments", () => {
  const tool = {
    name: "issue_refund",
    inputSchema: {
      properties: { order_id: { type: "string" }, amount: { type: "number" } },
      required: ["order_id", "amount"],
    },
  };
  const columns = [
    { id: "c1", name: "category", data_type: "text" },
    { id: "c2", name: "refund_amount", data_type: "float" },
    { id: "c3", name: "order_id" },
  ];
  it("a text constant or text column for a number is an issue", () => {
    expect(
      argumentIssues(
        tool,
        {
          order_id: { kind: "column", column: "c3" },
          amount: { kind: "constant", value: "lots" },
        },
        columns,
      ),
    ).toEqual(['amount is a number: "lots" is text']);
    expect(
      argumentIssues(
        tool,
        {
          order_id: { kind: "column", column: "c3" },
          amount: { kind: "column", column: "c1" },
        },
        columns,
      ),
    ).toEqual(["amount is a number: column category holds text"]);
    expect(
      argumentIssues(
        tool,
        {
          order_id: { kind: "column", column: "c3" },
          amount: { kind: "column", column: "c2" },
        },
        columns,
      ),
    ).toEqual([]);
    expect(
      argumentIssues(tool, { order_id: { kind: "empty" } }, columns),
    ).toEqual(["order_id is required", "amount is required"]);
  });
});

describe("run names from the config", () => {
  it("A2A and MCP names follow the card", () => {
    const card = { name: "Globex Support Agent", version: "1.4" };
    expect(a2aRunName(card, "refund_request")).toBe(
      "A2A · Globex Support Agent 1.4 · skill refund_request",
    );
    expect(a2aRunName(null)).toBe("A2A agent card");
    expect(isAutoA2aName("A2A agent card")).toBe(true);
    expect(isAutoA2aName("My agent")).toBe(false);
    expect(mcpRunName("acme-support-sandbox", "lookup_order")).toBe(
      "MCP · acme-support-sandbox · lookup_order",
    );
    expect(mcpRunName(null, null)).toBe("MCP server tool");
    expect(isAutoMcpName("MCP · github · get_file")).toBe(true);
  });
});

describe("knob:http:output-path", () => {
  it("lists the text paths of a response", () => {
    expect(
      textPathsOf({
        data: { answer: "Hi" },
        usage: { n: 3 },
        request_id: "req_1",
        actions: [{ name: "lookup_order" }],
      }),
    ).toEqual(["$.data.answer", "$.actions[0].name"]);
  });
});

describe("legacy:r4-run-card-kept · a card reads its own values", () => {
  it("valueAt reads a nested path of the card at its index", async () => {
    const { valueAt } = await import("../cardWatch-g2");
    const list = [{ name: "A" }, { name: "C", r4: { entryId: "x" } }];
    expect(valueAt(list[1], "r4.entryId")).toBe("x");
    expect(valueAt(list[1], "name")).toBe("C");
    expect(valueAt(undefined, "name")).toBeUndefined();
  });
});
