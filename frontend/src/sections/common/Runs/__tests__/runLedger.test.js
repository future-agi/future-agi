// W1-6 · the one run ledger (pure).
import { describe, expect, it } from "vitest";
import {
  ledgerLine,
  ledgerOf,
  ledgerParts,
  plannedLine,
  sampleLine,
  sampledCount,
  spendLine,
  stopKindOf,
} from "../runLedger";

const K = "eval:correctness";
const rec = (row, trial, patch = {}) => ({
  example_id: `r${row}`,
  trial,
  visibility: "completed",
  status: "completed",
  scores: { [K]: { value: 1, passed: true } },
  ...patch,
});
const skippedCell = {
  value: null,
  status: "skipped",
  skip_reason: "no expected value",
};

describe("W1-6 · runLedger", () => {
  it("counts each row once: scored, skipped by reason, errored by cause, stopped by the cap", () => {
    const records = [
      // 3 scored rows (one with a failing second try)
      rec(1, 1),
      rec(2, 1),
      rec(3, 1, { scores: { [K]: { value: 0, passed: false } } }),
      // 2 rows skipped: no expected value
      rec(4, 1, { scores: { [K]: skippedCell } }),
      rec(5, 1, { scores: { [K]: skippedCell } }),
      // 1 N/A row
      rec(6, 1, { scores: { [K]: { value: null, status: "na" } } }),
      // 2 errored rows: a rate limit and a tool timeout
      rec(7, 1, {
        visibility: "error",
        status: "error",
        error: "RateLimitError: 429 Too Many Requests",
        scores: {},
      }),
      rec(8, 1, {
        visibility: "error",
        status: "error",
        error: "lookup_invoice timed out after 30s",
        scores: {},
      }),
      // 2 rows the cost cap stopped before they ran
      rec(9, 1, { status: "infra", attempt_status: "stopped", scores: {} }),
      rec(10, 1, { status: "infra", attempt_status: "stopped", scores: {} }),
    ];
    const l = ledgerOf({
      records,
      primaryKey: K,
      stopReason: "Stopped at cost cap",
    });
    expect(l.rows).toMatchObject({
      total: 10,
      done: 8,
      scored: 3,
      na: 1,
      skipped: { "no expected value": 2 },
      errored: { RateLimitError: 1, "tool timeout · lookup_invoice": 1 },
      stopped: 2,
      running: 0,
      queued: 0,
    });
    expect(l.tries).toMatchObject({
      total: 10,
      done: 8,
      scored: 3,
      skipped: 2,
      na: 1,
      errored: 2,
      stopped: 2,
    });
    expect(l.stopped_by).toBe("cost_cap");
    expect(ledgerLine(l)).toBe(
      "3 rows scored · 2 skipped (no expected value) · 1 N/A · 2 errored (RateLimitError 1, tool timeout · lookup_invoice 1) · 2 not run · stopped at cost cap",
    );
    // every number names the filter it opens
    const parts = ledgerParts(l);
    expect(parts.find((p) => p.key === "errored").filter).toEqual({
      type: "error",
    });
    expect(parts.find((p) => p.key === "skipped").filter).toEqual({
      type: "skipped",
    });
  });

  it("a playing run: queued and running rows, tasks noun, carried rows", () => {
    const records = [
      rec(1, 1, { carried_from: "#2A" }),
      rec(1, 2, { carried_from: "#2A" }),
      rec(2, 1),
      rec(2, 2, { visibility: "running" }),
      rec(3, 1, { visibility: "queued" }),
      rec(3, 2, { visibility: "queued" }),
    ];
    const l = ledgerOf({ records, primaryKey: K });
    expect(l.rows).toMatchObject({
      total: 3,
      scored: 1,
      running: 1,
      queued: 1,
      carried: 1,
    });
    expect(l.stopped_by).toBeNull();
    expect(ledgerLine(l, { noun: "task" })).toBe(
      "1 task scored · 1 running · 1 queued · 1 carried",
    );
  });

  it("a row with no primary eval cell yet, and a run with no eval", () => {
    expect(
      ledgerLine(
        ledgerOf({ records: [rec(1, 1, { scores: {} })], primaryKey: K }),
      ),
    ).toBe("1 row not scored yet");
    expect(ledgerLine(ledgerOf({ records: [rec(1, 1), rec(2, 1)] }))).toBe(
      "2 rows ran",
    );
    expect(ledgerLine(ledgerOf({ records: [] }))).toBe("");
  });

  it("stop kinds read from the stop reason", () => {
    expect(stopKindOf("Stopped at cost cap")).toBe("cost_cap");
    expect(stopKindOf("Stopped at the time limit")).toBe("time_limit");
    expect(stopKindOf("Hit max turns")).toBe("max_turns");
    expect(stopKindOf("")).toBeNull();
  });

  it("sample, planned and spend lines", () => {
    expect(sampleLine({ n: 25, seed: 7, of: 248 })).toBe(
      "25 random rows (seed 7) of 248",
    );
    expect(sampleLine({ n: 300, seed: 7, of: 248 })).toBe(
      "248 random rows (seed 7) of 248",
    );
    expect(sampleLine(null)).toBeNull();
    expect(sampledCount({ n: 25, seed: 7 }, 248)).toBe(25);
    expect(sampledCount({ n: 25, seed: 7 }, 10)).toBe(10);
    expect(sampledCount(null, 248)).toBe(248);
    expect(plannedLine({ rows: 20, tries: 3, scope: "smoke" })).toBe(
      "smoke 20 rows × 3 tries = 60 tries",
    );
    expect(plannedLine({ rows: 1, tries: 1 })).toBe("1 row × 1 try = 1 try");
    expect(
      spendLine({
        estimate: { usd: 0.065, tries: 40 },
        actual: { usd: 0.066, tries: 40, done: true },
      }),
    ).toBe("Estimated $0.065 · spent $0.066 (+2%) · 40 tries");
    expect(
      spendLine({
        estimate: { usd: 0.5, tries: 40 },
        actual: { usd: 0.45, tries: 40, done: true },
      }),
    ).toBe("Estimated $0.50 · spent $0.45 (−10%) · 40 tries");
    expect(
      spendLine({
        estimate: { usd: 0.065, tries: 40 },
        actual: { usd: 0.03, tries: 18, done: false },
      }),
    ).toBe("Estimated $0.065 · spent $0.030 so far · 18 of 40 tries");
    expect(spendLine({ estimate: null, actual: { usd: 1 } })).toBeNull();
  });
});

describe("W1-8 · the results status names the run its counts are about", () => {
  it("names the lead run when several runs are in view; nothing on a one-run page", async () => {
    const { statusScopeOf } = await import("../runLedger");
    const summary = {
      runs: [
        { run_id: "a", label: "#3A" },
        { run_id: "e", label: "#3E" },
      ],
    };
    expect(statusScopeOf(summary, "e", true)).toBe("#3E");
    expect(statusScopeOf(summary, "e", false)).toBeNull();
    expect(statusScopeOf(summary, null, true)).toBeNull();
  });
});
