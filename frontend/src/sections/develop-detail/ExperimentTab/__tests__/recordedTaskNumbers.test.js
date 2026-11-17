// The Experiments list's per-task and time columns, from each try the
// results matrix records, for every kind of row a run can hold.
import { describe, expect, it } from "vitest";
import {
  RECORDED_REASON,
  RECORDED_SHORT,
  median,
  recordedTaskNumbers,
} from "../recordedTaskNumbers";

const R = "run-1";
// One row's cell: tries as [status, cost_usd, wall_ms].
const row = (id, tries) => ({
  row_id: id,
  cells: {
    [R]: {
      trials: tries.map(([s]) => (s === "completed" ? true : s)),
      trace_summary: tries.map(([status], i) => ({
        status,
        trial_number: i + 1,
      })),
      cost_usd: tries.map(([, usd]) => usd),
      wall_ms: tries.map(([, , ms]) => ms),
    },
  },
});
const matrixOf = (...rows) => ({ rows, columns: { [R]: {} } });

describe("recordedTaskNumbers", () => {
  it("a prompt run with every try priced and timed", () => {
    const n = recordedTaskNumbers(
      matrixOf(
        row("a", [["completed", 0.01, 1000]]),
        row("b", [["completed", 0.02, 3000]]),
        row("c", [["completed", 0.03, 2000]]),
      ),
      R,
    );
    expect(n.tasks).toBe(3);
    expect(n.tries).toBe(3);
    expect(n.perTask.usd).toBeCloseTo(0.02, 10);
    expect(n.perTask.total).toBeCloseTo(0.06, 10);
    expect(n.perTask.reason).toBeNull();
    expect(n.time.ms).toBe(2000);
    expect(n.time.reason).toBeNull();
  });

  it("two tries per row: a task sums its tries, the time is the median try", () => {
    const n = recordedTaskNumbers(
      matrixOf(
        row("a", [
          ["completed", 0.01, 100],
          ["completed", 0.03, 300],
        ]),
        row("b", [
          ["error", 0.02, 200],
          ["completed", 0.02, 200],
        ]),
      ),
      R,
    );
    expect(n.tasks).toBe(2);
    expect(n.tries).toBe(4);
    expect(n.perTask.usd).toBeCloseTo(0.04, 10);
    expect(n.time.ms).toBe(200);
  });

  it("errored and infrastructure tries ran, so they count", () => {
    const n = recordedTaskNumbers(
      matrixOf(
        row("a", [["error", 0.01, 500]]),
        row("b", [["infra", 0.01, 700]]),
      ),
      R,
    );
    expect(n.tries).toBe(2);
    expect(n.perTask.usd).toBeCloseTo(0.01, 10);
    expect(n.time.ms).toBe(600);
  });

  it("stopped, queued and never-reached tries did not run and count for neither", () => {
    const n = recordedTaskNumbers(
      matrixOf(
        row("a", [
          ["completed", 0.04, 400],
          ["stopped", null, null],
        ]),
        row("b", [["not_reached", null, null]]),
        row("c", [["queued", null, null]]),
      ),
      R,
    );
    expect(n.tasks).toBe(1);
    expect(n.tries).toBe(1);
    expect(n.perTask.usd).toBeCloseTo(0.04, 10);
    expect(n.time.ms).toBe(400);
  });

  it("agent rows record neither value: both columns say so", () => {
    const n = recordedTaskNumbers(
      matrixOf(
        row("a", [["completed", null, null]]),
        row("b", [["error", null, null]]),
      ),
      R,
    );
    expect(n.perTask.usd).toBeNull();
    expect(n.perTask.reason).toBe(RECORDED_REASON.perTaskNone);
    expect(n.time.ms).toBeNull();
    expect(n.time.reason).toBe(RECORDED_REASON.timeNone);
  });

  it("evaluation-only rows record a time and no per-task value", () => {
    const n = recordedTaskNumbers(
      matrixOf(
        row("a", [["completed", null, 300]]),
        row("b", [["completed", null, 500]]),
      ),
      R,
    );
    expect(n.perTask.reason).toBe(RECORDED_REASON.perTaskNone);
    expect(n.time.ms).toBe(400);
  });

  it("a value missing on some tries that ran gives no number, and says why", () => {
    const n = recordedTaskNumbers(
      matrixOf(
        row("a", [["completed", 0.01, 100]]),
        row("b", [["completed", null, null]]),
      ),
      R,
    );
    expect(n.perTask.usd).toBeNull();
    expect(n.perTask.reason).toBe(RECORDED_REASON.perTaskSome);
    expect(n.time.ms).toBeNull();
    expect(n.time.reason).toBe(RECORDED_REASON.timeSome);
  });

  it("every try recorded zero: no number, and it says so", () => {
    const n = recordedTaskNumbers(
      matrixOf(row("a", [["completed", 0, 100]])),
      R,
    );
    expect(n.perTask.usd).toBeNull();
    expect(n.perTask.reason).toBe(RECORDED_REASON.perTaskZero);
    expect(n.time.ms).toBe(100);
  });

  it("no try ran yet", () => {
    const n = recordedTaskNumbers(
      matrixOf(row("a", [["queued", null, null]])),
      R,
    );
    expect(n.tries).toBe(0);
    expect(n.perTask.reason).toBe(RECORDED_REASON.noTries);
    expect(n.time.reason).toBe(RECORDED_REASON.noTries);
  });

  it("a read that holds only some of the run's rows totals nothing", () => {
    const n = recordedTaskNumbers(
      matrixOf(row("a", [["completed", 0.01, 100]])),
      R,
      { partial: true },
    );
    expect(n.perTask.usd).toBeNull();
    expect(n.perTask.reason).toBe(RECORDED_REASON.partial);
    expect(n.time.reason).toBe(RECORDED_REASON.partial);
  });

  it("rows of other runs are not read", () => {
    const n = recordedTaskNumbers(
      { rows: [{ row_id: "x", cells: { other: {} } }] },
      R,
    );
    expect(n.tries).toBe(0);
  });

  it("every reason has a few visible words except a run still starting", () => {
    Object.values(RECORDED_REASON)
      .filter((r) => r !== RECORDED_REASON.noTries)
      .forEach((r) => {
        expect(RECORDED_SHORT[r]).toBeTruthy();
        expect(RECORDED_SHORT[r].length).toBeLessThanOrEqual(14);
      });
  });

  it("median", () => {
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 3, 2])).toBe(2.5);
    expect(median([])).toBeNull();
  });
});
