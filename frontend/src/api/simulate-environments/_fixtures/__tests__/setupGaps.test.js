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

  it("raises exactly one blocking Grading gap when no evals are added", () => {
    const gaps = setupGaps(env, { evals: [] });
    const blocking = gaps.filter((g) => g.status === "blocking");
    expect(blocking).toHaveLength(1);
    expect(blocking[0].area).toBe("Grading");
    expect(blocking[0].id).toBe("no-evals");
    expect(gapCounts(gaps).blocking).toBe(1);
  });

  it("raises no blocking gap once evals are present", () => {
    const gaps = setupGaps(env, { evals: [{ id: "task_success" }] });
    expect(gapCounts(gaps).blocking).toBe(0);
  });

  it("flips a gap to resolved when gapsResolved answers it", () => {
    const gaps = setupGaps(env, {
      evals: [],
      gapsResolved: { "no-evals": "Added task success" },
    });
    const gap = gaps.find((g) => g.id === "no-evals");
    expect(gap.status).toBe("resolved");
    expect(gap.answered).toBe("Added task success");
    expect(gapCounts(gaps).blocking).toBe(0);
  });
});
