/* global process */
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

let DAY;
let buildTimeline;
let rangeText;
let selectColumns;

// A zone with a DST change inside the window (US clocks go back on 1 Nov 2026).
const ZONE = "America/New_York";
let previous;
beforeAll(async () => {
  previous = process.env.TZ;
  process.env.TZ = ZONE;
  // The date formatters capture the zone when the module loads.
  vi.resetModules();
  ({ DAY, buildTimeline, rangeText, selectColumns } = await import(
    "../timelineBins"
  ));
});
afterAll(() => {
  // Assigning undefined would set the zone to the string "undefined".
  if (previous === undefined) delete process.env.TZ;
  else process.env.TZ = previous;
});

const QUESTION = {
  configId: "q1",
  question: "Resolved?",
  answerType: "yes_no",
  labels: ["Yes", "No"],
};

describe("day columns across a DST change", () => {
  it("labels the last included calendar day across spring-forward", () => {
    const start = new Date(2026, 2, 7).getTime();
    const end = new Date(2026, 2, 9).getTime();
    expect(end - start).toBe(47 * 60 * 60 * 1000);
    expect(rangeText(start, end)).toBe("Mar 7 – Mar 8");
    expect(rangeText(new Date(2026, 2, 8).getTime(), end)).toBe("Mar 8");
    expect(selectColumns([{ start, end, n: 3 }], 0, 0)).toEqual({
      count: 3,
      filter: {
        time_from: "2026-03-07T05:00:00.000Z",
        time_to: "2026-03-09T04:00:00.000Z",
      },
      label: "Mar 7 – Mar 8",
    });
  });

  it("runs in a zone that changes its clocks in the window", () => {
    // Without the zone the test below passes whatever the code does.
    expect(new Date("2026-10-20T12:00:00Z").getTimezoneOffset()).toBe(240);
    expect(new Date("2026-11-03T12:00:00Z").getTimezoneOffset()).toBe(300);
  });

  it("start every column at local midnight", () => {
    const records = ["2026-10-20T15:00:00Z", "2026-11-03T15:00:00Z"].map(
      (time, i) => ({
        id: `r${i}`,
        time,
        answers: { q1: { answer: "Yes", status: "completed" } },
      }),
    );
    const timeline = buildTimeline({
      records,
      configId: "q1",
      question: QUESTION,
      window: ["2026-10-15T04:00:00Z", "2026-11-15T05:00:00Z"],
      now: Date.parse("2026-11-15T05:00:00Z"),
      maxColumns: 40,
    });
    expect(timeline.binMs % DAY).toBe(0);
    for (const column of timeline.columns) {
      const start = new Date(column.start);
      expect([start.getHours(), start.getMinutes()]).toEqual([0, 0]);
    }
    // The row of 3 Nov sits in the column of 3 Nov, local time.
    const late = timeline.columns.find((c) =>
      c.items.some((item) => item.id === "r1"),
    );
    expect(new Date(late.start).getDate()).toBe(3);
  });
});
