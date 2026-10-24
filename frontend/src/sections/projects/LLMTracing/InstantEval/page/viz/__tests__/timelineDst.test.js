/* global process */
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { DAY, buildTimeline } from "../timelineBins";

// A zone with a DST change inside the window (US clocks go back on 1 Nov 2026).
const ZONE = "America/New_York";
let previous;
beforeAll(() => {
  previous = process.env.TZ;
  process.env.TZ = ZONE;
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
