import { describe, expect, it } from "vitest";
import {
  diffCountsText,
  isIgnored,
  isJsonPair,
  jsonDiff,
  parseIgnore,
} from "../jsonDiff";
import { splitEvalMapping } from "../PathPreviewStrip";

const before = {
  answer: "Damaged items are refunded within 5 business days.",
  citations: [{ doc: "refund-policy.pdf", page: 3 }],
  tool_calls: [{ name: "search", args: { q: "refund" } }],
};
const after = {
  ...before,
  citations: [{ doc: "refund-policy.pdf", page: 4 }],
  usage: { total_tokens: 812 },
};

describe("jsonDiff", () => {
  it("lists changed and added paths with numeric deltas", () => {
    const r = jsonDiff(before, JSON.stringify(after));
    expect(r.changes).toEqual([
      { path: "citations[0].page", op: "changed", from: 3, to: 4, delta: 1 },
      { path: "usage", op: "added", to: { total_tokens: 812 } },
    ]);
    expect(diffCountsText(r.counts)).toBe("1 changed · 1 added · 0 removed");
  });

  it("ignores paths by glob and counts them", () => {
    const r = jsonDiff(before, after, { ignorePaths: parseIgnore("usage.*") });
    expect(r.changes.map((c) => c.path)).toEqual(["citations[0].page"]);
    expect(r.ignored).toBe(1);
    expect(isIgnored("a.id", ["*.id"])).toBe(true);
    expect(isIgnored("usage", ["usage.*"])).toBe(true);
    const none = jsonDiff(before, after, {
      ignorePaths: ["usage.*", "citations*"],
    });
    expect(none.changes).toEqual([]);
    expect(none.ignored).toBe(2);
  });

  it("ignores key order, optional whitespace and case", () => {
    expect(jsonDiff({ a: 1, b: 2 }, { b: 2, a: 1 }).changes).toEqual([]);
    expect(
      jsonDiff(
        { s: "Hi  there" },
        { s: "hi there" },
        { ignoreWhitespace: true, ignoreCase: true },
      ).changes,
    ).toEqual([]);
  });

  it("diffs step inputs (trajectory) and matches arrays by key", () => {
    expect(
      jsonDiff({ order_id: "A-7781" }, { order_id: "7781" }).changes,
    ).toEqual([
      { path: "order_id", op: "changed", from: "A-7781", to: "7781" },
    ]);
    const r = jsonDiff(
      [
        { id: "a", v: 1 },
        { id: "b", v: 2 },
      ],
      [
        { id: "b", v: 3 },
        { id: "a", v: 1 },
      ],
      { arrayMatch: "key" },
    );
    expect(r.changes).toEqual([
      { path: '[?(@.id=="b")].v', op: "changed", from: 2, to: 3, delta: 1 },
    ]);
  });

  it("aligns chat messages and diffs tool-call arguments as JSON", () => {
    const a = [
      {
        role: "assistant",
        content: null,
        tool_calls: [
          {
            id: "c1",
            type: "function",
            function: { name: "search", arguments: '{"q":"refund"}' },
          },
        ],
      },
    ];
    const b = [
      {
        role: "assistant",
        content: null,
        tool_calls: [
          {
            id: "c1",
            type: "function",
            function: { name: "search", arguments: '{"q":"refunds"}' },
          },
        ],
      },
    ];
    expect(jsonDiff(a, b).changes.map((c) => c.path)).toEqual([
      "[0].tool_calls[0].function.arguments.q",
    ]);
  });

  it("detects JSON pairs", () => {
    expect(isJsonPair('{"a":1}', { a: 2 })).toBe(true);
    expect(isJsonPair("plain", { a: 2 })).toBe(false);
  });
});

describe("splitEvalMapping", () => {
  it("splits an eval mapping value into column and path", () => {
    const cols = [{ field: "c-1" }, { field: "c-10" }];
    expect(splitEvalMapping("c-1.customer.tier", cols)).toEqual({
      colId: "c-1",
      rel: "customer.tier",
    });
    expect(splitEvalMapping("c-10[0].page", cols)).toEqual({
      colId: "c-10",
      rel: "[0].page",
    });
    expect(splitEvalMapping("c-1", cols)).toBeNull();
  });
});
