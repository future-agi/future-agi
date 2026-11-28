import { describe, expect, it } from "vitest";
import { fixEndCard, isAgentFix, readinessView } from "../readinessView";

const report = (rows, validation = { state: "ready" }) => ({
  validation,
  answer: { kind: "blocked" },
  pairing: { evidence: "pair_certificate" },
  areas: [{ id: "tool_binding", rows: rows.map((row) => row.id) }],
  rows,
});

describe("readinessView", () => {
  it("reads an area as its worst counted row, leaving out rows nothing measures or touches", () => {
    const view = readinessView(
      report([
        { id: "browser", status: "untested", measured: false },
        {
          id: "tool_routing",
          status: "blocked",
          measured: true,
          affected: { count: 3 },
        },
      ]),
    );
    expect(view.open).toEqual([
      expect.objectContaining({
        area: "tool_binding",
        status: "blocked",
        rowId: "tool_routing",
        affected: 3,
      }),
    ]);
    expect(view.holdsRun).toBe(false);
  });

  it("leaves out a blocked row that blocks none of the picked scenarios", () => {
    const row = { id: "tool_routing", status: "blocked", measured: true };
    const view = (affected) => readinessView(report([{ ...row, affected }]));
    expect(view({ count: 0 }).open).toEqual([]);
    expect(view({ count: 1 }).open).toHaveLength(1);
  });

  it("holds the run only while the pair is checked or after its check failed", () => {
    const held = (state) => readinessView(report([], { state })).holdsRun;
    expect(held("validating")).toBe(true);
    expect(held("failed")).toBe(true);
    expect(held("check_unavailable")).toBe(false);
    expect(held("ready")).toBe(false);
  });

  it("sends fixes made on the agent to its versions", () => {
    expect(isAgentFix({ category: "agent_version" })).toBe(true);
    expect(isAgentFix({ type: "preflight" })).toBe(true);
    expect(isAgentFix({ type: "recheck", category: "recheck" })).toBe(false);
  });

  it("counts every scenario nothing can grade, past the ones the report lists", () => {
    const { grading } = readinessView({
      selection: {
        requested: 1103,
        grading: { gradable: 3, not_gradable: [{ key: "a" }], truncated: true },
      },
    });
    expect(grading).toEqual({ gradable: 3, notGradable: 1100 });
  });

  describe("while the server offers Fix", () => {
    const row = (id, status, extra = {}) => ({
      id,
      area: id,
      status,
      measured: true,
      reason: `${id} is ${status}`,
      affected: { count: 1 },
      ...extra,
    });
    const offered = (...rows) =>
      readinessView({
        fix_groups: {},
        areas: rows.map((one) => ({ id: one.id, rows: [one.id] })),
        rows,
      });
    const areas = (lines) => lines.map((line) => line.area);

    it("groups lines by who fixes them, and not while it doesn't", () => {
      const rows = [
        row("start", "blocked", { fix_group: "needs_you" }),
        row("state", "blocked", { fix_group: "builder" }),
        row("reset", "stale", { fix_group: "recheck" }),
      ];
      const { groups } = offered(...rows);
      expect(areas(groups.needs_you)).toEqual(["start"]);
      expect(areas(groups.builder)).toEqual(["state"]);
      expect(areas(groups.recheck)).toEqual(["reset"]);
      expect(readinessView(report(rows)).groups).toBeNull();
    });

    it("keeps a fixed area as a Fixed builder line, and names what a line waits on", () => {
      const view = offered(
        row("start", "blocked"),
        row("state", "supported", { fix_progress: { state: "fixed" } }),
        row("reset", "blocked", {
          fix_group: "builder",
          fix_progress: { state: "waiting_on_other", waiting_on: "start" },
        }),
      );
      const [waiting, fixed] = view.groups.builder;
      expect(waiting.waitingOnArea).toBe("start");
      expect(fixed).toMatchObject({ area: "state", status: "supported" });
      expect(view.ready).toEqual([]);
    });

    it("keeps a line a fix is working on once it blocks none of the picked scenarios", () => {
      const view = offered(
        row("state", "blocked", {
          affected: { count: 0 },
          fix_group: "builder",
          fix_progress: { state: "fixing" },
        }),
      );
      expect(areas(view.groups.builder)).toEqual(["state"]);
    });

    it("notes a line nobody can fix beside the limits, unless it blocks", () => {
      const view = offered(row("cancel", "untested"), row("tool", "blocked"));
      expect(areas(view.limits)).toEqual(["cancel"]);
      expect(areas(view.groups.needs_you)).toEqual(["tool"]);
    });
  });
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
