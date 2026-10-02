import { describe, it, expect } from "vitest";

import { buildTree } from "./columnTree";

// buildTree parses flat column-path strings ("col", "col.a.b", "col[0].x")
// into the nested node tree that ColumnTreeSelect renders. Each node is
// { id, label, path, children }.

describe("buildTree", () => {
  it("returns flat scalar columns as leaf roots with no children", () => {
    const tree = buildTree(["prompt_tokens", "cost", "status"]);
    expect(tree).toHaveLength(3);
    expect(tree.map((n) => n.label)).toEqual([
      "prompt_tokens",
      "cost",
      "status",
    ]);
    expect(tree.every((n) => n.children.length === 0)).toBe(true);
    expect(tree[0]).toMatchObject({
      id: "prompt_tokens",
      label: "prompt_tokens",
      path: "prompt_tokens",
    });
  });

  it("nests dot paths and merges shared parents into one node", () => {
    const tree = buildTree([
      "status_message.name",
      "status_message.operation_name.metadata.response_time_ms",
    ]);
    // One shared root: status_message
    expect(tree).toHaveLength(1);
    const root = tree[0];
    expect(root.label).toBe("status_message");
    expect(root.children.map((c) => c.label)).toEqual([
      "name",
      "operation_name",
    ]);

    // Deep leaf keeps the segment as label and the full dotted path as path
    const operationName = root.children.find(
      (c) => c.label === "operation_name",
    );
    const metadata = operationName.children[0];
    expect(metadata.label).toBe("metadata");
    const leaf = metadata.children[0];
    expect(leaf).toMatchObject({
      label: "response_time_ms",
      path: "status_message.operation_name.metadata.response_time_ms",
    });
  });

  it("parses array bracket segments without inserting a dot before '['", () => {
    const tree = buildTree(["child_spans[0].name"]);
    const root = tree[0];
    expect(root.label).toBe("child_spans");
    const index = root.children[0];
    expect(index).toMatchObject({ label: "[0]", path: "child_spans[0]" });
    expect(index.children[0]).toMatchObject({
      label: "name",
      path: "child_spans[0].name",
    });
  });

  it("does not duplicate a parent shared across sibling array indices", () => {
    const tree = buildTree(["tags[0]", "tags[1]"]);
    expect(tree).toHaveLength(1);
    expect(tree[0].label).toBe("tags");
    expect(tree[0].children.map((c) => c.path)).toEqual(["tags[0]", "tags[1]"]);
  });

  it("marks isColumn so a scalar that is also a prefix stays selectable", () => {
    const tree = buildTree(["status", "status.code"]);
    expect(tree).toHaveLength(1);
    const status = tree[0];
    // "status" is both a real column and a parent of "status.code".
    expect(status.isColumn).toBe(true);
    expect(status.children.map((c) => c.path)).toEqual(["status.code"]);
    expect(status.children[0].isColumn).toBe(true);
  });

  it("does not mark pure prefix nodes as columns", () => {
    const tree = buildTree(["a.b.c"]);
    const a = tree[0];
    expect(a.isColumn).toBe(false); // "a" is only a prefix, never its own column
    expect(a.children[0].isColumn).toBe(false); // "a.b" likewise
    expect(a.children[0].children[0].isColumn).toBe(true); // "a.b.c" is the column
  });
});
