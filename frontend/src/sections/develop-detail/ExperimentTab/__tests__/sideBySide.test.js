import { describe, expect, it } from "vitest";
import {
  sideBySideHref,
  sideBySidePartner,
  sideBySideTitle,
} from "../sideBySide";

// IT-R4 G2 P1: a run pairs with the dataset's pinned baseline (same output
// kind); else its previous sibling, which is a plain lane (no baseline=).
const run = (label, exp, extra = {}) => ({
  run_id: label,
  label,
  experiment_id: exp,
  ...extra,
});
const A1 = run("#1A", "e1");
const B1 = run("#1B", "e1");
const A2 = run("#2A", "e2");
const B2 = run("#2B", "e2");
const C3 = run("#3C", "e3");
const IMG = run("#4A", "e4", { config: { output_modality: "image" } });
const IMG2 = run("#4B", "e4", { config: { output_modality: "image" } });
const all = [A1, B1, A2, B2, C3, IMG, IMG2];
const ctx = (baseline, pinned = true) => ({
  datasetId: "d",
  baselineId: baseline.run_id,
  baselineRun: baseline,
  pinnedRunId: pinned ? baseline.run_id : null,
  allRuns: all,
});

describe("sideBySidePartner", () => {
  it("prefers the pinned baseline over the previous sibling", () => {
    expect(sideBySidePartner(B2, ctx(A1))).toBe(A1);
    expect(sideBySidePartner(B1, ctx(A1))).toBe(A1);
    expect(sideBySideTitle(B2, ctx(A1))).toBe("Side by side with baseline #1A");
  });
  it("falls back to the previous sibling for another output kind", () => {
    expect(sideBySidePartner(IMG2, ctx(A1))).toBe(IMG);
    expect(sideBySideTitle(IMG2, ctx(A1))).toBe(
      "Side by side with #4A (previous run)",
    );
    expect(sideBySidePartner(IMG, ctx(A1))).toBe(IMG2);
  });
  it("uses the sibling when nothing is pinned", () => {
    expect(sideBySidePartner(B2, ctx(A1, false))).toBe(A2);
    expect(sideBySidePartner(A1, { ...ctx(A1), allRuns: [A1] })).toBeNull();
  });
  it("passes baseline= only for the pinned baseline", () => {
    const toBase = sideBySideHref(B2, ctx(A1));
    expect(toBase).toContain("runs=%232B%2C%231A");
    expect(toBase).toContain("baseline=%231A");
    const toSib = sideBySideHref(IMG2, ctx(A1));
    expect(toSib).toContain("runs=%234B%2C%234A");
    expect(toSib).not.toContain("baseline=");
    expect(sideBySideHref(B2, ctx(A1, false))).not.toContain("baseline=");
  });
});
