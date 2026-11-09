import { describe, expect, it } from "vitest";
import { revisionChanges } from "./revisionDiff";
import { HIDDEN_MCP_TABS, isMcpTabHidden } from "../../shell/page-status";

describe("revision diff and default release gate", () => {
  it("keeps the existing trust release switch OFF", () => {
    expect(HIDDEN_MCP_TABS).toHaveProperty("trust");
    expect(isMcpTabHidden("trust")).toBe(true);
  });
  it("expands schema changes by field while preserving additions, removals, and null", () => {
    expect(
      revisionChanges({
        diff: [
          { field: "description", before: "old", after: "new" },
          {
            field: "inputSchema",
            before: { properties: { gone: { type: "string" }, value: null } },
            after: { properties: { value: false, added: {} } },
          },
        ],
      }),
    ).toEqual([
      { path: "description", before: "old", after: "new" },
      { path: "inputSchema.properties.added", before: undefined, after: {} },
      {
        path: "inputSchema.properties.gone",
        before: { type: "string" },
        after: undefined,
      },
      { path: "inputSchema.properties.value", before: null, after: false },
    ]);
  });
  it("shows a new tool's entire definition when no accepted definition exists", () => {
    expect(
      revisionChanges({
        diff: [],
        previous_definition: null,
        definition: { description: "new", inputSchema: { type: "object" } },
      }),
    ).toEqual([
      { path: "description", before: undefined, after: "new" },
      { path: "inputSchema", before: undefined, after: { type: "object" } },
    ]);
  });
  it("keeps array order significant and ignores object-key ordering", () => {
    expect(
      revisionChanges({
        diff: [
          {
            field: "annotations",
            before: { b: false, a: true },
            after: { a: true, b: false },
          },
          {
            field: "inputSchema",
            before: { enum: ["a", "b"] },
            after: { enum: ["b", "a"] },
          },
        ],
      }),
    ).toEqual([
      { path: "inputSchema.enum", before: ["a", "b"], after: ["b", "a"] },
    ]);
  });
});
