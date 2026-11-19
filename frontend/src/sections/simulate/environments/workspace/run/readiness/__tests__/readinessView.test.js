import { describe, it, expect } from "vitest";
import { fixEndCard, readinessView } from "../readinessView";

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
        rowId: "tool_routing",
        reason: "issue_refund still reaches production",
        affected: 4,
        checkedAt: null,
        fix,
        group: "needs_you",
        progress: null,
        fixAction: null,
        fixResult: null,
        waitingOnArea: null,
      },
    ]);
  });

  it("groups lines by who fixes them, from the server or by fix type", () => {
    const areas = [
      { id: "start_and_message", status: "blocked", rows: ["start"] },
      { id: "tool_binding", status: "blocked", rows: ["tool_routing"] },
      { id: "reset", status: "stale", rows: ["reset"] },
    ];
    const rows = [
      row("start", "blocked", { fix: { type: "preflight" } }),
      row("tool_routing", "blocked", { fix: { type: "tool_routing" } }),
      row("reset", "stale", {
        fix: { type: "recheck", recheck: { scope: "pair" } },
      }),
    ];
    const areasOf = (view) =>
      Object.fromEntries(
        Object.entries(view.groups).map(([group, lines]) => [
          group,
          lines.map((line) => line.area),
        ]),
      );

    const grouped = readinessView({
      ...report({ areas }),
      rows: rows.map((one, at) => ({
        ...one,
        fix_group: ["needs_you", "builder", "recheck"][at],
      })),
      fix_groups: { builder: { rows: ["tool_routing"] } },
    });
    expect(grouped.grouped).toBe(true);
    expect(areasOf(grouped)).toEqual({
      needs_you: ["start_and_message"],
      builder: ["tool_binding"],
      recheck: ["reset"],
    });

    const flat = readinessView(report({ areas, rows }));
    expect(flat.grouped).toBe(false);
    expect(areasOf(flat)).toEqual({
      needs_you: ["start_and_message", "tool_binding"],
      builder: [],
      recheck: ["reset"],
    });
  });

  it("keeps a fixed area as a Fixed builder line, and names what a line waits on", () => {
    const fixed = { state: "fixed", reason: null, waiting_on: null };
    const view = readinessView({
      ...report({
        areas: [
          { id: "start_and_message", status: "blocked", rows: ["start"] },
          { id: "tool_binding", status: "supported", rows: ["tool_routing"] },
          { id: "reset", status: "blocked", rows: ["reset"] },
        ],
        rows: [
          row("start", "blocked", { area: "start_and_message" }),
          // Fixed rows have nothing left to fix, so the server groups them nowhere.
          row("tool_routing", "supported", {
            fix_group: null,
            fix_progress: fixed,
          }),
          row("reset", "blocked", {
            fix_group: "builder",
            fix_progress: { state: "waiting_on_other", waiting_on: "start" },
          }),
        ],
      }),
      fix_groups: {},
    });
    const [waiting, done] = view.groups.builder;
    expect(done).toMatchObject({ area: "tool_binding", progress: fixed });
    expect(view.ready).not.toContain("tool_binding");
    expect(waiting.waitingOnArea).toBe("start_and_message");
  });
});

it("keeps a grouped line nobody can fix out of Needs you", () => {
  const view = readinessView({
    ...report({
      areas: [{ id: "cancellation", status: "untested", rows: ["cancel"] }],
      rows: [row("cancel", "untested", { fix_group: null })],
    }),
    fix_groups: {},
  });
  expect(view.groups.needs_you).toEqual([]);
  expect(view.open).toEqual([]);
  expect(view.limits).toEqual([
    { area: "cancellation", status: "untested", reason: "cancel is untested" },
  ]);
});

it("counts the cases that can't be graded past the 200 it lists", () => {
  const view = readinessView(
    report({
      selection: {
        requested: 300,
        grading: { gradable: 50, not_gradable: Array(200).fill("k") },
      },
    }),
  );
  expect(view.grading).toEqual({ gradable: 50, notGradable: 250 });
});

describe("fixEndCard", () => {
  const ended = (outcome, extra = {}) => ({
    state: "done",
    outcome,
    ...extra,
  });
  it("says how the fix came out, in the server's words", () => {
    expect(fixEndCard({ state: "fixing", outcome: null })).toBeNull();
    expect(
      fixEndCard(
        ended({ kind: "fixed", fixed: 1, total: 1, message: "Fixed" }),
      ),
    ).toEqual({ text: "Fixed", action: "Review and start run" });
    expect(
      fixEndCard(
        ended({
          kind: "partly_fixed",
          fixed: 1,
          total: 2,
          message: "Fixed 1 of 2",
        }),
      ),
    ).toEqual({ text: "Fixed 1 of 2", action: "Open run plan" });
    expect(
      fixEndCard(
        ended({
          kind: "could_not_fix",
          fixed: 0,
          total: 1,
          message: "The tool has no test route",
        }),
      ).text,
    ).toBe("Couldn't fix. The tool has no test route");
  });

  it("asks to save a fix left in unsaved changes, unless its draft is stale", () => {
    const unsaved = ended({ kind: "unsaved", fixed: 1, total: 1, message: "" });
    expect(fixEndCard(unsaved).text).toBe(
      "The fixes are in unsaved changes. Save them to run them.",
    );
    expect(
      fixEndCard({ ...unsaved, error: { code: "draft_stale", message: "x" } })
        .text,
    ).toBe(
      "These unsaved changes started from an older version. Discard them on the page, or ask chat to make them again.",
    );
  });
});
