import { describe, expect, it } from "vitest";
import { buildTimeGrid } from "../askPivot";
import { findPatterns } from "../askStats";
import { matchesFilter, summarizeRecords } from "../askSummary";
import {
  buildTimeline,
  clampWindow,
  columnBudget,
  squareLayout,
  stackCap,
} from "../timelineBins";
import run from "./fixtures/askRunSessions.json";

// A real answers sample from the harbor-conversations project: "Did the agent
// resolve the customer's issue?" on Sessions · Past 7 days, with an outage
// two days before the run where 8 sessions hit an error. The view's window
// runs to the next UTC midnight, past the moment the run started.
const question = run.questions[0];
const now = new Date(run.startedAt).getTime();
const errorIds = run.records
  .filter((record) => record.facets.error)
  .map((record) => record.id);

describe("a real run", () => {
  it("stops the window at the run, not at a midnight still to come", () => {
    expect(new Date(run.window[1]).getTime()).toBeGreaterThan(now);
    expect(clampWindow(run.window, run.records, now)).toEqual([
      run.window[0],
      new Date(now).toISOString(),
    ]);
    const timeline = buildTimeline({
      records: run.records,
      configId: question.configId,
      question,
      window: run.window,
      now,
    });
    const last = timeline.columns[timeline.columns.length - 1];
    expect(last.start).toBeLessThan(now);
    expect(last.end).toBeGreaterThanOrEqual(now);
    expect(
      timeline.columns.reduce((sum, column) => sum + column.items.length, 0),
    ).toBe(run.records.length);
    expect(
      timeline.columns.reduce((sum, column) => sum + column.errors, 0),
    ).toBe(errorIds.length);
    const grid = buildTimeGrid({
      summary: summarizeRecords({
        records: run.records,
        questions: run.questions,
        window: run.window,
        now,
      }),
      question,
      window: run.window,
      now,
    });
    const lastDay = grid.rows[grid.rows.length - 1].date.getTime();
    expect(lastDay).toBeLessThanOrEqual(now);
  });

  it("cuts the one burst column so the others get 12px squares", () => {
    const timeline = buildTimeline({
      records: run.records,
      configId: question.configId,
      question,
      window: run.window,
      now,
      maxColumns: columnBudget(run.records.length, 840),
    });
    const cap = stackCap(timeline.columns);
    expect(cap).toBe(12);
    const layout = squareLayout(
      840,
      timeline.columns.length,
      timeline.maxStack,
      cap,
    );
    expect(timeline.maxStack).toBeGreaterThan(cap);
    expect(layout.capped).toBe(true);
    expect(layout.w).toBe(12);
    expect(layout.h).toBe(12);
  });

  it("calls out the outage's errors and when they happened", () => {
    const result = findPatterns({
      records: run.records,
      questions: run.questions,
      focus: { configId: question.configId, answer: null },
    });
    expect(result.answer).toBe("No");
    expect(result.answered).toBe(run.records.length);
    expect(result.counted).toBeLessThan(result.tested);
    const error = result.patterns.find((p) => p.dimension === "error");
    expect(error).toBeTruthy();
    expect(error.k).toBe(errorIds.length);
    expect(error.n).toBe(errorIds.length);
    expect(error.filter).toEqual({ group: "error:error" });
    expect(
      new Set(
        run.records
          .filter((r) => matchesFilter(r, error.filter))
          .map((r) => r.id),
      ),
    ).toEqual(new Set(errorIds));
    expect(error.q).toBeLessThan(0.1);
    // The errors fall within two hours; whether that is one local day
    // depends on the time zone the test runs in.
    const days = new Set(
      run.records
        .filter((record) => record.facets.error)
        .map((record) => new Date(record.time).toDateString()),
    );
    if (days.size === 1)
      expect(error.when).toMatch(new RegExp(`^all ${errorIds.length} on `));
    else expect(error.when).toBe("");
    // Every callout's numbers are its own counts, and at most five.
    expect(result.patterns.length).toBeLessThanOrEqual(5);
    result.patterns.forEach((p) => {
      expect(
        run.records.filter((r) => matchesFilter(r, p.filter)).length,
      ).toBeGreaterThanOrEqual(p.n);
      expect(p.text).toContain(`${p.k} of ${p.n}`);
    });
  });

  it("compares only the rows answered so far while the rest are read", () => {
    // The first 8 answers of the run, the rest still being read.
    const answered = new Set(run.records.slice(0, 8).map((r) => r.id));
    const records = run.records.map((record) =>
      answered.has(record.id)
        ? record
        : {
            ...record,
            answers: {
              [question.configId]: {
                answer: null,
                confidence: null,
                explanation: "",
                status: "running",
              },
            },
          },
    );
    const result = findPatterns({
      records,
      questions: run.questions,
      focus: { configId: question.configId, answer: null },
    });
    expect(result.answered).toBe(8);
    result.patterns.forEach((p) => {
      expect(p.n).toBeLessThanOrEqual(8);
      // Only answered rows are counted.
      const counted = records.filter(
        (r) => matchesFilter(r, p.filter) && answered.has(r.id),
      );
      expect(counted.length).toBeGreaterThanOrEqual(p.n);
    });
  });
});
