// IT-R1 G1 P10: Try on one row tells the truth about tool calls.
import { describe, expect, it } from "vitest";
import {
  claimsAction,
  filterRows,
  scoreToolEvals,
  unbackedClaim,
  unmappedWarning,
} from "../tryOnOneRowModel";

describe("Try on one row truth (IT-R1 G1 P10)", () => {
  it("flags a reply that claims an action with zero tool calls", () => {
    const a = {
      output: "Cancelled the kettle on A-6118; the rest ships as planned.",
      trace_summary: { tool_calls: 0 },
    };
    expect(unbackedClaim(a)?.label).toBe(
      "Says it acted, but made no tool call",
    );
    expect(unbackedClaim({ ...a, trace_summary: { tool_calls: 1 } })).toBe(
      null,
    );
    expect(unbackedClaim(a, { hasTools: false })).toBe(null);
    expect(claimsAction("I've taken care of A-1598 for you.")).toBe(true);
    expect(claimsAction("A-8216 is in transit and arrives Tuesday")).toBe(true);
    expect(claimsAction("Could you share your order number?")).toBe(false);
  });

  it("tool evals fail on zero calls; other evals pass through", () => {
    const evals = [
      { key: "eval:task_completion", passed: true },
      { key: "eval:tool_call_accuracy", passed: true },
      { key: "eval:argument_match", passed: true },
    ];
    const out = scoreToolEvals(evals, { toolCalls: 0 });
    expect(out.map((e) => e.passed)).toEqual([true, false, false]);
    expect(out[1].reason).toMatch(/No tool call/);
    expect(scoreToolEvals(evals, { toolCalls: 2 })).toBe(evals);
  });

  it("warns before a try with unmapped variables", () => {
    expect(unmappedWarning(["instruction", "customer_email"], {}).text).toBe(
      "2 variables are not mapped — the model will see empty text",
    );
    expect(unmappedWarning(["a"], { a: "col" })).toBe(null);
  });

  it("a number picks that row first", () => {
    const rows = [
      { row_id: "r4", order: 3, c: "A-1598" },
      { row_id: "r5", order: 4, c: "Where is my order" },
    ];
    const cols = [{ id: "c", name: "title" }];
    expect(filterRows(rows, "5", { columns: cols })[0].row_id).toBe("r5");
  });
});
