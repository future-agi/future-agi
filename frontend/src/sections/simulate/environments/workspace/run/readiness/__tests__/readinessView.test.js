import { describe, it, expect } from "vitest";
import { readinessView } from "../readinessView";

const row = (id, status, extra = {}) => ({
  id,
  status,
  measured: true,
  reason: `${id} is ${status}`,
  affected: { count: 0, of: 10, keys: [] },
  fix: null,
  ...extra,
});

const report = ({
  validation = "not_run",
  error = null,
  answer = "ready",
  evidence = "pair_certificate",
  areas = [],
  rows = [],
  selection = {},
} = {}) => ({
  pairing: { evidence },
  validation: { state: validation, error },
  answer: { kind: answer },
  areas,
  rows,
  selection: {
    requested: 10,
    eligible: 10,
    blocked: 0,
    stale: 0,
    untested: 0,
    ...selection,
  },
});

describe("readinessView", () => {
  it("reads a pair that can't be checked as unavailable, Run left on", () => {
    const view = readinessView(report({ validation: "unavailable" }));
    expect(view.state).toBe("unavailable");
    expect(view.canRun).toBe(true);
  });

  it("reads a pairing with no evidence as not checked yet", () => {
    const view = readinessView(
      report({ evidence: "none", answer: "unchecked" }),
    );
    expect(view.state).toBe("unchecked");
    expect(view.canRun).toBe(true);
  });

  it("turns Run off while the pair is being checked", () => {
    const view = readinessView(
      report({ validation: "validating", answer: "validating" }),
    );
    expect(view.state).toBe("checking");
    expect(view.canRun).toBe(false);
  });

  it("turns Run off after the agent failed its check, with the server's reason", () => {
    const view = readinessView(
      report({
        validation: "failed",
        error: {
          code: "x",
          reason: "the agent did not pass this environment's checks",
        },
        answer: "blocked",
      }),
    );
    expect(view.state).toBe("failed");
    expect(view.stateReason).toBe(
      "the agent did not pass this environment's checks",
    );
    expect(view.canRun).toBe(false);
  });

  it("keeps Run on when the check could not run on our side", () => {
    const view = readinessView(
      report({
        validation: "check_unavailable",
        error: { reason: "our side" },
      }),
    );
    expect(view.state).toBe("ours");
    expect(view.stateReason).toBe("our side");
    expect(view.canRun).toBe(true);
  });

  it("does not open an area for rows nothing measures yet, unless they affect a scenario", () => {
    const view = readinessView(
      report({
        areas: [
          { id: "evidence", status: "untested", rows: ["browser", "state"] },
          { id: "reset", status: "untested", rows: ["resume", "reset"] },
        ],
        rows: [
          row("browser", "untested", { measured: false }),
          row("state", "supported"),
          row("resume", "untested", {
            measured: false,
            affected: { count: 2, of: 10, keys: ["a", "b"] },
          }),
          row("reset", "supported"),
        ],
      }),
    );
    expect(view.open.map((line) => line.area)).toEqual(["reset"]);
    expect(view.ready).toContain("evidence");
  });

  it("gives an open area the worst row's reason, affected count and fix", () => {
    const fix = {
      action: "Point issue_refund at the test world",
      would: "lands",
      recheck: null,
    };
    const view = readinessView(
      report({
        areas: [
          {
            id: "tool_binding",
            status: "blocked",
            rows: ["tool_routing", "tool_discovery"],
          },
        ],
        rows: [
          row("tool_routing", "blocked", {
            reason: "issue_refund still reaches production",
            affected: { count: 4, of: 10, keys: [] },
            fix,
          }),
          row("tool_discovery", "stale"),
        ],
      }),
    );
    expect(view.open).toEqual([
      {
        area: "tool_binding",
        status: "blocked",
        reason: "issue_refund still reaches production",
        affected: 4,
        fix,
      },
    ]);
  });
});
