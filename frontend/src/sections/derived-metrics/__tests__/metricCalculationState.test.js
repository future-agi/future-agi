import { describe, expect, it } from "vitest";
import {
  attemptState,
  awaitsFirstCalculation,
  firstCalculationBadge,
  firstCalculationWord,
  groupHistoryRows,
  summarizeAttempts,
} from "../metricCalculationState";

const attempt = (created_at, status) => ({
  kind: "recalculation",
  created_at,
  status,
  definition: null,
  snapshot: null,
});

describe("calculation state copy", () => {
  it("labels every refresh attempt state and keeps an unknown one's words", () => {
    expect(attemptState("prepared")).toMatchObject({ label: "Preparing" });
    expect(attemptState("staging")).toMatchObject({ label: "Calculating" });
    expect(attemptState("verified")).toMatchObject({ label: "Finishing" });
    expect(attemptState("committed")).toMatchObject({ label: "Committed" });
    expect(attemptState("aborted")).toEqual({
      status: "stopped",
      role: "warning",
      label: "Stopped",
    });
    // No invented label: the status map humanizes the server's own word.
    expect(attemptState("rolled_back")).toEqual({ status: "rolled_back" });
    expect(attemptState(undefined)).toEqual({ status: "unavailable" });
  });

  it("calls a saved, uncalculated version pending, never a draft", () => {
    expect(firstCalculationBadge({ version: 1 })).toEqual({
      status: "pending",
      label: "v1 · First calculation pending",
    });
    expect(
      firstCalculationBadge(
        { version: 2, backfill_status: "running" },
        { short: true },
      ),
    ).toEqual({ status: "running", label: "v2 · Running" });
    expect(firstCalculationBadge({ backfill_status: "failed" })).toEqual({
      status: "failed",
      label: "First calculation failed",
    });
    // The Overview panel names the version elsewhere; same words, no prefix.
    expect(
      firstCalculationBadge(
        { version: 3, backfill_status: "pending" },
        { short: true, version: false },
      ),
    ).toEqual({ status: "pending", label: "Pending" });
    expect(
      firstCalculationBadge({ version: 3 }, { short: true, version: false }),
    ).toEqual({ status: "pending", label: "Pending" });
    expect(firstCalculationWord({ backfill_status: "running" })).toBe(
      "running",
    );
    expect(firstCalculationWord({})).toBe("pending");
  });

  it("states how many recent attempts stopped, and whether most did", () => {
    const page = [
      attempt("2026-11-11T05:22:00Z", "prepared"),
      attempt("2026-11-11T04:58:00Z", "aborted"),
      attempt("2026-11-11T04:34:00Z", "aborted"),
    ];
    expect(summarizeAttempts(page)).toMatchObject({
      total: 3,
      stopped: 2,
      mostStopped: true,
    });
    expect(summarizeAttempts(page.slice(0, 1)).mostStopped).toBe(false);
    expect(summarizeAttempts([]).mostStopped).toBe(false);
  });

  it("checks again only while a first calculation can still finish", () => {
    const draft = (backfill_status) => ({
      definition: null,
      draft_definition: { backfill_status },
    });
    expect(awaitsFirstCalculation(draft("pending"))).toBe(true);
    expect(awaitsFirstCalculation(draft("running"))).toBe(true);
    expect(awaitsFirstCalculation(draft(undefined))).toBe(true);
    expect(awaitsFirstCalculation(draft("failed"))).toBe(false);
    expect(awaitsFirstCalculation(draft("complete"))).toBe(false);
    expect(
      awaitsFirstCalculation({ definition: {}, draft_definition: {} }),
    ).toBe(false);
    expect(awaitsFirstCalculation(undefined)).toBe(false);
  });
});

describe("history rows", () => {
  const version = {
    kind: "version",
    created_at: "2026-11-11T00:00:00Z",
    status: "pending",
  };
  const results = [
    attempt("2026-11-11T05:22:00Z", "prepared"),
    attempt("2026-11-11T04:58:00Z", "aborted"),
    attempt("2026-11-11T04:34:00Z", "aborted"),
    version,
    attempt("2026-11-10T23:00:00Z", "aborted"),
  ];

  it("summarizes only the attempts on the page", () => {
    expect(summarizeAttempts(results)).toEqual({
      latest: results[0],
      total: 4,
      stopped: 3,
      mostStopped: true,
    });
    expect(summarizeAttempts([version])).toEqual({
      latest: null,
      total: 0,
      stopped: 0,
      mostStopped: false,
    });
  });

  it("collapses consecutive attempts in one state and keeps order", () => {
    const rows = groupHistoryRows(results);
    expect(rows.map((row) => row.kind)).toEqual([
      "attempts",
      "attempts",
      "entry",
      "attempts",
    ]);
    expect(rows[1].entries).toHaveLength(2);
    expect(rows[2].entry).toBe(version);
    // An attempt after another entry starts its own group.
    expect(rows[3].entries).toEqual([results[4]]);
  });
});
