// options-truth L7b G3 (info:rerun-dialog:no-params): Rerun with changes of
// an endpoint / A2A / MCP run edits the run's own fields and posts them as
// changes the rerun applies.
import { describe, expect, it } from "vitest";
import {
  a2aSkillOptions,
  sourceChangeLine,
  sourceChangesOf,
  sourceFieldsOf,
  sourceKindOfConfig,
} from "../sourceChangesModel";

const http = {
  kind: "http",
  endpoint: {
    url: "https://agent.acme.test/v2/reply",
    method: "POST",
    body_template: '{"q": "{{instruction}}"}',
    output_path: "$.reply",
  },
};
const a2a = {
  kind: "a2a",
  a2a: {
    skill_id: "lookup_order",
    card: {
      skills: [
        { id: "lookup_order", name: "Look up an order" },
        { id: "issue_refund", name: "Issue a refund" },
      ],
    },
  },
};

describe("L7b G3 source fields in Rerun with changes", () => {
  it("knows the kinds that call an endpoint, agent or tool", () => {
    expect(sourceKindOfConfig(http)).toBe("http");
    expect(sourceKindOfConfig(a2a)).toBe("a2a");
    expect(sourceKindOfConfig({ kind: "mcp_tool", mcp_tool: {} })).toBe(
      "mcp_tool",
    );
    expect(sourceKindOfConfig({ kind: "http" })).toBeNull();
    expect(sourceKindOfConfig({ kind: "prompt" })).toBeNull();
    expect(
      sourceKindOfConfig({
        kind: "code",
        execution: { mode: "remote" },
        endpoint: http.endpoint,
      }),
    ).toBe("http");
  });

  it("posts only the edited fields, on the run's own axis", () => {
    expect(sourceFieldsOf(http).method).toBe("POST");
    expect(sourceChangesOf(http, { method: "POST" })).toEqual([]);
    expect(
      sourceChangesOf(http, { method: "PUT", output_path: "$.data.reply" }),
    ).toEqual([
      { axis: "endpoint", field: "method", value: "PUT" },
      { axis: "endpoint", field: "output_path", value: "$.data.reply" },
    ]);
    expect(sourceChangesOf(http, { url: "  " })).toEqual([]);
    expect(sourceChangesOf(a2a, { skill_id: "issue_refund" })).toEqual([
      { axis: "a2a", field: "skill_id", value: "issue_refund" },
    ]);
    expect(
      sourceChangesOf(
        { kind: "mcp_tool", mcp_tool: { side_effects: "stubs" } },
        { side_effects: "dry_run" },
      ),
    ).toEqual([{ axis: "mcp_tool", field: "side_effects", value: "dry_run" }]);
  });

  it("lists the agent card's skills and words the change", () => {
    expect(a2aSkillOptions(a2a).map((s) => s.value)).toEqual([
      "lookup_order",
      "issue_refund",
    ]);
    expect(
      sourceChangeLine([
        { axis: "a2a", field: "skill_id", value: "issue_refund" },
      ]),
    ).toBe("skill → issue_refund");
  });
});
