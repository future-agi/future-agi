import { describe, expect, it } from "vitest";

import { volumeTitle, volumeWindow } from "../requestVolume";

// The strip's window reads like the From / To chips beside it: to the
// second, no milliseconds (seen live: "Oct 19 11:30:00.000").
describe("request volume title", () => {
  it("names a set window to the second, as the filter chips do", () => {
    const title = volumeTitle({
      start: "2026-10-19T18:30:00.000Z",
      end: "2026-10-21T00:00:00.000Z",
      granularity: "hour",
      lastDay: false,
    });
    expect(title).toMatch(
      /^Requests per hour, \w{3} \d\d \d\d:\d\d:\d\d to \w{3} \d\d \d\d:\d\d:\d\d$/,
    );
    expect(title).not.toMatch(/\.\d{3}/);
  });
});

// The strip covered the last 24 hours while the table
// under it listed every request (617 rows back to Oct 20). With no time
// filter the strip covers the table's window: from the oldest request that
// matches the table's filters to now.
describe("request volume window", () => {
  const now = new Date("2026-10-22T08:00:00.000Z");

  it("with no time filter, runs from the oldest matching request to now", () => {
    const window = volumeWindow({}, now, "2026-10-20T09:38:06.404Z");
    expect(window).toMatchObject({
      start: "2026-10-20T09:38:06.404Z",
      end: "2026-10-22T08:00:00.000Z",
      granularity: "hour",
      since: true,
    });
    expect(volumeTitle(window)).toMatch(
      /^Requests per hour, since \w{3} \d\d \d\d:\d\d:\d\d$/,
    );
  });

  it("buckets by day past 7 days", () => {
    const window = volumeWindow({}, now, "2026-09-01T00:00:00.000Z");
    expect(window.granularity).toBe("day");
  });

  it("follows a set start as before, the oldest request aside", () => {
    const window = volumeWindow(
      { startedAfter: "2026-10-22T06:00:00.000Z" },
      now,
      "2026-10-20T09:38:06.404Z",
    );
    expect(window.start).toBe("2026-10-22T06:00:00.000Z");
    expect(window.since).toBe(false);
  });
});
