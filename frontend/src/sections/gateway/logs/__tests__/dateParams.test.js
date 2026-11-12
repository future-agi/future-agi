import { afterAll, describe, expect, it } from "vitest";

import { buildFilterParams } from "../hooks/useRequestLogs";
import { exportParams } from "../exportLogs";

// Pin a zone that is not UTC, before any case below builds a Date, so a UTC
// CI host can tell a local reading from a UTC one (Node applies a TZ change
// to later Date calls). Restored after the file.
const HOST_ZONE = globalThis.process.env.TZ;
globalThis.process.env.TZ = "Asia/Kolkata";

// A zone-less started_after / started_before in the URL (an old bookmark or a
// hand-built link) is the viewer's local time, as the Filters panel and the
// "From" chip show it. The API reads a zone-less time as UTC, so the page
// sends it as an instant; a zoned value goes as it is.
const local = (y, m, d, h = 0, min = 0) =>
  new Date(y, m - 1, d, h, min).toISOString();

describe("date filters sent to the API", () => {
  afterAll(() => {
    if (HOST_ZONE === undefined) delete globalThis.process.env.TZ;
    else globalThis.process.env.TZ = HOST_ZONE;
  });

  it.each([
    ["2026-10-19T00:00", local(2026, 10, 19)],
    ["2026-10-19T08:30:15", new Date(2026, 9, 19, 8, 30, 15).toISOString()],
    ["2026-10-19", local(2026, 10, 19)],
  ])("reads a zone-less %s as local time", (value, instant) => {
    expect(
      buildFilterParams({ startedAfter: value, startedBefore: value }),
    ).toMatchObject({ started_after: instant, started_before: instant });
    expect(
      exportParams({ startedAfter: value, startedBefore: value }),
    ).toMatchObject({ started_after: instant, started_before: instant });
  });

  it.each(["2026-10-19T00:00:00.000Z", "2026-10-19T05:30:00+05:30"])(
    "sends a zoned %s unchanged",
    (value) => {
      expect(buildFilterParams({ startedAfter: value })).toMatchObject({
        started_after: value,
      });
      expect(exportParams({ startedBefore: value })).toMatchObject({
        started_before: value,
      });
    },
  );

  // On a UTC host local time is UTC, so the cases above
  // cannot tell a local reading from a UTC one. In a zone 5:30 ahead the
  // local midnight is 18:30 UTC the day before.
  it("reads a zone-less time in the viewer's zone, not as UTC", () => {
    expect(
      buildFilterParams({ startedAfter: "2026-10-19T00:00" }),
    ).toMatchObject({ started_after: "2026-10-18T18:30:00.000Z" });
    expect(exportParams({ startedBefore: "2026-10-19" })).toMatchObject({
      started_before: "2026-10-18T18:30:00.000Z",
    });
  });

  it("leaves a value it cannot read for the API to refuse", () => {
    expect(buildFilterParams({ startedAfter: "yesterday" })).toMatchObject({
      started_after: "yesterday",
    });
  });
});
