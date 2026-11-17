// The public dataset page's cells read like the grid (W4-6, A3).
import { describe, expect, it } from "vitest";
import { cellDetail, cellSummary } from "../sharedDatasetModel";

describe("sharedDatasetModel cells", () => {
  it("empty values read as a dash, never [] or {}", () => {
    [null, undefined, "", [], {}, "[]", "{}"].forEach((v) =>
      expect(cellSummary(v)).toBe("—"),
    );
    expect(cellDetail([])).toBe("—");
  });

  it("tool calls read as name(arg: value)", () => {
    const calls = [
      { name: "lookup_order", args: { order_id: "A-3920" } },
      { name: "apply_credit", args: { amount: 5 } },
    ];
    expect(cellSummary(calls)).toBe(
      'lookup_order(order_id: "A-3920"), apply_credit(amount: 5)',
    );
    expect(cellSummary(JSON.stringify(calls))).toBe(
      'lookup_order(order_id: "A-3920"), apply_credit(amount: 5)',
    );
    expect(cellDetail(calls)).toBe(JSON.stringify(calls, null, 2));
  });

  it("a chat reads as its turns; other JSON stays JSON; text stays text", () => {
    const chat = [
      { role: "user", content: "Where is my order?" },
      { role: "assistant", content: "On its way." },
    ];
    expect(cellSummary(chat)).toBe(
      "user: Where is my order? · assistant: On its way.",
    );
    expect(cellDetail(chat)).toBe(
      "user: Where is my order?\nassistant: On its way.",
    );
    expect(cellSummary({ a: 1 })).toBe('{"a":1}');
    expect(cellSummary("Hi there")).toBe("Hi there");
    expect(cellSummary(3)).toBe("3");
  });
});
