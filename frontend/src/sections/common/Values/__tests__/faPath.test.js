import { describe, expect, it } from "vitest";
import {
  formatPath,
  fromLegacyMapping,
  listPaths,
  parsePath,
  resolvePath,
  toLegacyMapping,
} from "../faPath";

const row = {
  conversation: [
    { role: "system", content: "Be brief." },
    {
      role: "user",
      content: "Order A-7781 arrived broken",
      tool_calls: [{ name: "lookup_order" }, { name: "refund" }],
    },
    { role: "assistant", content: "Refunded.", amount: 129 },
  ],
  metadata: { customer: { tier: "enterprise" }, "key.with.dots": 7 },
  expected: { citations: [{ page: 4 }, { page: 9 }] },
};

describe("parsePath grammar", () => {
  it.each([
    ["col", [{ type: "key", key: "col" }]],
    [
      "col.key",
      [
        { type: "key", key: "col" },
        { type: "key", key: "key" },
      ],
    ],
    [
      'col["key.with.dots"]',
      [
        { type: "key", key: "col" },
        { type: "key", key: "key.with.dots" },
      ],
    ],
    [
      "col['k']",
      [
        { type: "key", key: "col" },
        { type: "key", key: "k" },
      ],
    ],
    [
      "col[0]",
      [
        { type: "key", key: "col" },
        { type: "index", index: 0 },
      ],
    ],
    [
      "col[-1]",
      [
        { type: "key", key: "col" },
        { type: "index", index: -1 },
      ],
    ],
    ["col[*]", [{ type: "key", key: "col" }, { type: "wildcard" }]],
    [
      "col[1:3]",
      [
        { type: "key", key: "col" },
        { type: "slice", start: 1, end: 3 },
      ],
    ],
    [
      'col[?(@.role=="user")]',
      [
        { type: "key", key: "col" },
        { type: "filter", path: ["role"], value: "user" },
      ],
    ],
    [
      "col[?(@.amount==129)]",
      [
        { type: "key", key: "col" },
        { type: "filter", path: ["amount"], value: 129 },
      ],
    ],
  ])("%s", (text, steps) => {
    const p = parsePath(text);
    expect(p.ok).toBe(true);
    expect(p.steps).toEqual(steps);
  });

  it("splits off a known column", () => {
    const p = parsePath("metadata.customer.tier", { columns: ["metadata"] });
    expect(p.column).toBe("metadata");
    expect(p.steps.map((s) => s.key)).toEqual(["customer", "tier"]);
    expect(parsePath("other.x", { columns: ["metadata"] }).column).toBeNull();
  });

  it("accepts $ / $. / {{ }} and whitespace", () => {
    expect(parsePath("$.a.b").steps).toEqual(parsePath("a.b").steps);
    expect(parsePath("$[0]").steps).toEqual([{ type: "index", index: 0 }]);
    expect(parsePath("{{ col.a }}").steps).toEqual(parsePath("col.a").steps);
    expect(parsePath("  col [ 0 ] . name ").steps).toEqual(
      parsePath("col[0].name").steps,
    );
  });

  it("reports the first unexpected character with its position", () => {
    expect(parsePath("metadata%x")).toMatchObject({
      ok: false,
      error: 'Unexpected "%" at 8',
      pos: 8,
    });
    expect(parsePath("col[0").ok).toBe(false);
    expect(parsePath("").error).toBe("Empty path");
    expect(parsePath('col["open').ok).toBe(false);
  });
});

describe("resolvePath", () => {
  it("resolves keys, indexes, negatives and dotted keys", () => {
    expect(resolvePath(row, "metadata.customer.tier")).toEqual({
      found: true,
      value: "enterprise",
      multi: false,
    });
    expect(resolvePath(row, "conversation[-1].amount").value).toBe(129);
    expect(resolvePath(row, 'metadata["key.with.dots"]').value).toBe(7);
    expect(resolvePath(row, "expected.citations[0].page").value).toBe(4);
  });

  it("returns multi values for wildcards, slices and filters", () => {
    expect(resolvePath(row, "expected.citations[*].page")).toMatchObject({
      found: true,
      multi: true,
      values: [4, 9],
    });
    expect(
      resolvePath(row, "conversation[*].tool_calls[*].name").values,
    ).toEqual(["lookup_order", "refund"]);
    expect(resolvePath(row, "conversation[1:3]").values).toHaveLength(2);
    expect(
      resolvePath(row, 'conversation[?(@.role=="user")].content').values,
    ).toEqual(["Order A-7781 arrived broken"]);
    expect(
      resolvePath(row, "conversation[?(@.amount==129)].role").values,
    ).toEqual(["assistant"]);
  });

  it("parses JSON stored as text", () => {
    const cell = JSON.stringify({ customer: { tier: "pro" } });
    expect(resolvePath(cell, "customer.tier").value).toBe("pro");
    expect(
      resolvePath({ metadata: cell }, "metadata.customer.tier").value,
    ).toBe("pro");
  });

  it("is typed and never throws on a missing path", () => {
    expect(resolvePath(row, "metadata.nope.deeper")).toEqual({
      found: false,
      multi: false,
    });
    expect(resolvePath(null, "a.b").found).toBe(false);
    expect(resolvePath(row, "metadata%").found).toBe(false);
    expect(typeof resolvePath(row, "conversation[2].amount").value).toBe(
      "number",
    );
  });

  it("handles a 500-item array", () => {
    const big = { items: Array.from({ length: 500 }, (_, i) => ({ n: i })) };
    expect(resolvePath(big, "items[-1].n").value).toBe(499);
    expect(resolvePath(big, "items[*].n").values).toHaveLength(500);
    expect(resolvePath(big, "items[10:20].n").values).toEqual(
      Array.from({ length: 10 }, (_, i) => i + 10),
    );
  });

  it("resolves today's mapping forms identically", () => {
    const rowCells = { col: { a: { b: "x" }, items: [{ name: "first" }] } };
    const viaVariable = (text) => {
      const p = parsePath(text, { columns: ["col"] });
      return resolvePath(rowCells[p.column], p.steps).value;
    };
    expect(viaVariable("{{col.a.b}}")).toBe("x");
    expect(viaVariable("{{col.items[0]}}")).toEqual({ name: "first" });
    expect(viaVariable("{{col.items[0].name}}")).toBe("first");
    const { colId, path } = fromLegacyMapping("col::$.a.b");
    expect(resolvePath(rowCells[colId], path).value).toBe("x");
  });
});

describe("formatPath", () => {
  it("writes the canonical text", () => {
    expect(formatPath("expected", parsePath("citations[0].page").steps)).toBe(
      "expected.citations[0].page",
    );
    expect(
      formatPath("metadata", [{ type: "key", key: "key.with.dots" }]),
    ).toBe('metadata["key.with.dots"]');
    expect(
      formatPath(null, parsePath('c[?(@.role=="user")][1:3][*]').steps),
    ).toBe('c[?(@.role=="user")][1:3][*]');
  });

  it("accepts formatPath(steps) and resolvePath(…, {all})", () => {
    expect(formatPath(parsePath("a.b[0]").steps)).toBe("a.b[0]");
    expect(resolvePath({ a: 1 }, "a", { all: true }).values).toEqual([1]);
  });

  it("round-trips through parsePath", () => {
    [
      "a.b[0]",
      'a["x y"].z',
      "a[*].b",
      "a[-2]",
      "a[1:]",
      "a[?(@.n==3)]",
    ].forEach((text) => {
      const steps = parsePath(text).steps;
      expect(parsePath(formatPath(null, steps)).steps).toEqual(steps);
    });
  });
});

describe("listPaths", () => {
  it("lists paths with kinds and an example", () => {
    const paths = listPaths([
      { a: { b: "x" }, list: [{ n: 1 }, { n: null }] },
      { a: { b: null } },
    ]);
    const byPath = Object.fromEntries(paths.map((p) => [p.path, p]));
    expect(byPath["a.b"].kinds).toEqual({ string: 1, null: 1 });
    expect(byPath["a.b"].example).toBe("x");
    expect(byPath["list[*].n"].kinds).toEqual({ number: 1, null: 1 });
    expect(byPath.list.kinds).toEqual({ array: 1 });
  });

  it("respects maxDepth and maxPaths", () => {
    const deep = { a: { b: { c: { d: 1 } } } };
    expect(listPaths([deep], { maxDepth: 2 }).map((p) => p.path)).toEqual([
      "a",
      "a.b",
    ]);
    const wide = Object.fromEntries(
      Array.from({ length: 50 }, (_, i) => [`k${i}`, i]),
    );
    expect(listPaths([wide], { maxPaths: 10 })).toHaveLength(10);
  });
});

describe("legacy mapping", () => {
  it("converts both ways", () => {
    expect(toLegacyMapping("C1", "a.b")).toBe("C1::$.a.b");
    expect(toLegacyMapping("C1", "$.a.b")).toBe("C1::$.a.b");
    expect(toLegacyMapping("C1", "[0].x")).toBe("C1::$[0].x");
    expect(toLegacyMapping("C1", "")).toBe("C1");
    expect(fromLegacyMapping("C1::$.a.b")).toEqual({
      colId: "C1",
      path: "a.b",
    });
    expect(fromLegacyMapping("C1")).toEqual({ colId: "C1", path: "" });
  });
});
