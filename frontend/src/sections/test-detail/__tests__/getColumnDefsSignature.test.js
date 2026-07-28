import { describe, it, expect } from "vitest";
import { getColumnDefsSignature } from "../common";

// Mirrors the real shape from getTestRunDetailGridColumnDefs: an "Evaluation"
// group whose children are per-eval column defs (createColumnDef -> id: c.id,
// createReasonColumnDef -> id: `${c.id}_reason`).
const evalGroup = (childIds) => ({
  id: "evaluation",
  children: childIds.map((id) => ({ id, field: id })),
});

describe("getColumnDefsSignature (TH-6415 stale-column fix)", () => {
  it("returns '' for empty/undefined input", () => {
    expect(getColumnDefsSignature()).toBe("");
    expect(getColumnDefsSignature([])).toBe("");
  });

  it("changes when one eval child is removed from a group (the exact bug)", () => {
    // The old top-level length check saw [1 group] before and after and treated
    // them as equal; the recursive signature descends into children and differs.
    const twoEvals = [evalGroup(["eval_a", "eval_b"])];
    const oneEval = [evalGroup(["eval_a"])];

    expect(getColumnDefsSignature(twoEvals)).not.toBe(
      getColumnDefsSignature(oneEval),
    );
  });

  it("is stable across re-derivation: structurally equal defs share a signature", () => {
    // Freshly-built defs (new object refs, same ids) must match stored ones so
    // applyReasonColumnVisibility does not trigger a spurious column reset.
    const stored = [evalGroup(["eval_a", "eval_b"])];
    const freshlyDerived = [evalGroup(["eval_a", "eval_b"])];

    expect(getColumnDefsSignature(freshlyDerived)).toBe(
      getColumnDefsSignature(stored),
    );
  });

  it("ignores `hide` so toggling reason-column visibility does not reset columns", () => {
    const visible = [
      { id: "eval_a", field: "eval_a", hide: false },
      { id: "eval_a_reason", field: "eval_a_reason", hide: false },
    ];
    const reasonHidden = [
      { id: "eval_a", field: "eval_a", hide: false },
      { id: "eval_a_reason", field: "eval_a_reason", hide: true },
    ];

    expect(getColumnDefsSignature(reasonHidden)).toBe(
      getColumnDefsSignature(visible),
    );
  });

  it("is order-sensitive so a reordered group is detected as a change", () => {
    const original = [evalGroup(["eval_a", "eval_b"])];
    const reordered = [evalGroup(["eval_b", "eval_a"])];

    expect(getColumnDefsSignature(reordered)).not.toBe(
      getColumnDefsSignature(original),
    );
  });

  it("falls back to `field` when a leaf has no id", () => {
    expect(getColumnDefsSignature([{ field: "call_execution_id" }])).toBe(
      "call_execution_id",
    );
  });
});
