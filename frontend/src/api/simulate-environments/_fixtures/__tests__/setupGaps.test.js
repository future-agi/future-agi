import { describe, it, expect } from "vitest";
import { setupGaps, gapCounts, GAP_STATUS } from "../setupGaps";

const env = {
  surface: "voice",
  tools: [
    { name: "lookup_account", desc: "Reads the caller's account." },
    { name: "issue_refund", desc: "Refunds a charge." },
  ],
  rules: ["Never disclose another caller's data.", "Only refund verified callers."],
};

describe("setupGaps", () => {
  it("has a GAP_STATUS entry for each status with a colour", () => {
    ["blocking", "assumed", "resolved"].forEach((s) => {
      expect(GAP_STATUS[s].color).toBeTruthy();
    });
  });

  it("does not flag missing evaluations as a blocking gap — a run is allowed without them", () => {
    const gaps = setupGaps(env, { evals: [] });
    expect(gaps.find((g) => g.id === "no-evals")).toBeUndefined();
    // No gap is blocking: a run only needs an agent + scenarios, so nothing here
    // claims "a run cannot start". Missing evals mean an unscored run, not a block.
    expect(gapCounts(gaps).blocking).toBe(0);
  });

  it("stays free of blocking gaps whether or not evals are present", () => {
    expect(gapCounts(setupGaps(env, { evals: [] })).blocking).toBe(0);
    expect(gapCounts(setupGaps(env, { evals: [{ id: "task_success" }] })).blocking).toBe(0);
  });

  it("flips a gap to resolved when gapsResolved answers it", () => {
    const gaps = setupGaps(env, {
      evals: [],
      gapsResolved: { manifest: "Reviewed the contract" },
    });
    const gap = gaps.find((g) => g.id === "manifest");
    expect(gap.status).toBe("resolved");
    expect(gap.answered).toBe("Reviewed the contract");
  });
});
