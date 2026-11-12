// cronModel (notify BUILD-BRIEF §4.0, §8): the shared golden file, DST gaps
// and overlaps, intervals, day_or, rejected syntax, defaultMinute, zones,
// describe and toUtcCron.
import { describe as group, expect, it } from "vitest";
import golden from "../../../../../../api_contracts/schedules/next-runs.golden.json";
import {
  CRON_ERRORS,
  defaultMinute,
  describe,
  describeCron,
  nextRuns,
  nextRunsDetail,
  normalizeZone,
  toUtcCron,
  validate,
} from "../cronModel";
import { localToUtc, offsetMs, zoneAbbr } from "../cronZones";

const at = (iso) => Date.parse(iso);
const isoOf = (ms) => new Date(ms).toISOString();
const HOUR = 3_600_000;

group("next-runs.golden.json (shared with the Python scheduler)", () => {
  it("has the cases the brief names", () => {
    const names = golden.runs.map((c) => c.name).join(" | ");
    [
      "Asia/Kolkata across 8 Mar",
      "Asia/Kolkata across 1 Nov",
      "America/New_York across 8 Mar",
      "America/New_York across 1 Nov",
      "through fall-back",
      "every 6 hours",
      "weekdays",
      "0 9 1 * 1",
    ].forEach((part) => expect(names).toContain(part));
    const rejected = golden.rejected.map((c) => c.cron);
    ["* * * * *", "0 9 L * *", "*/5 * * * *"].forEach((c) =>
      expect(rejected).toContain(c),
    );
  });

  golden.runs.forEach((c) => {
    it(c.name, () => {
      const got = nextRunsDetail(c.cadence, at(c.from), c.n).map((r) => ({
        at: isoOf(r.at_ms),
        shifted_from: r.shifted_from,
      }));
      expect(got).toEqual(c.expect);
    });
  });

  golden.rejected.forEach((c) => {
    it(`rejects ${c.cron} (${c.code})`, () => {
      const v = validate(c.cron);
      expect(v.ok).toBe(false);
      expect(v.code).toBe(c.code);
    });
  });
});

group("daylight saving", () => {
  it("a time that doesn't exist runs at the next valid minute", () => {
    // 8 Mar 2026, America/New_York: 02:00 EST jumps to 03:00 EDT (07:00Z).
    expect(localToUtc(2026, 3, 8, 2, 30, "America/New_York")).toEqual({
      ms: at("2026-03-08T07:00:00Z"),
      shifted: true,
    });
    expect(localToUtc(2026, 3, 8, 2, 0, "America/New_York").ms).toBe(
      at("2026-03-08T07:00:00Z"),
    );
    expect(localToUtc(2026, 3, 8, 3, 0, "America/New_York")).toEqual({
      ms: at("2026-03-08T07:00:00Z"),
      shifted: false,
    });
  });

  it("a time that happens twice runs once, at the first", () => {
    // 1 Nov 2026: 01:30 EDT (05:30Z) and 01:30 EST (06:30Z).
    expect(localToUtc(2026, 11, 1, 1, 30, "America/New_York")).toEqual({
      ms: at("2026-11-01T05:30:00Z"),
      shifted: false,
    });
    const runs = nextRuns(
      { preset: "cron", cron: "30 * * * *", tz: "America/New_York" },
      at("2026-11-01T04:00:00Z"),
      4,
    ).map(isoOf);
    // Local 00:30, 01:30 (first), then 02:30 EST: the second 01:30 is not a run.
    expect(runs).toEqual([
      "2026-11-01T04:30:00.000Z",
      "2026-11-01T05:30:00.000Z",
      "2026-11-01T07:30:00.000Z",
      "2026-11-01T08:30:00.000Z",
    ]);
  });

  it("a gap wall time moves to 03:00 and runs before the hour's later minute", () => {
    const runs = nextRunsDetail(
      { preset: "cron", cron: "15 2,3 * * *", tz: "America/New_York" },
      at("2026-03-08T05:00:00Z"),
      2,
    );
    expect(runs.map((r) => [isoOf(r.at_ms), r.shifted_from])).toEqual([
      ["2026-03-08T07:00:00.000Z", "02:15"],
      ["2026-03-08T07:15:00.000Z", null],
    ]);
  });

  it("hourly through fall-back has no gap and every 6 h is even", () => {
    const hourly = nextRuns(
      { preset: "hourly", interval_min: 60, minute: 7, tz: "America/New_York" },
      at("2026-10-31T20:00:00Z"),
      24,
    );
    hourly.slice(1).forEach((t, i) => expect(t - hourly[i]).toBe(HOUR));
    const six = nextRuns(
      {
        preset: "every_n_hours",
        interval_min: 360,
        anchor_at: "2026-03-01T01:41:00Z",
        tz: "America/New_York",
      },
      at("2026-03-07T00:00:00Z"),
      12,
    );
    six.slice(1).forEach((t, i) => expect(t - six[i]).toBe(6 * HOUR));
  });

  it("an anchor in the future is the first run", () => {
    expect(
      nextRuns(
        {
          preset: "hourly",
          interval_min: 60,
          anchor_at: "2026-10-21T03:41:00Z",
          tz: "UTC",
        },
        at("2026-10-21T00:00:00Z"),
        1,
      ),
    ).toEqual([at("2026-10-21T03:41:00Z")]);
  });

  it("only offers intervals that divide a day", () => {
    expect(() =>
      nextRuns({ preset: "every_n_hours", interval_min: 300, tz: "UTC" }, 0, 1),
    ).toThrow(/interval_min/);
  });
});

group("validate", () => {
  it("reads names, ranges, lists, steps and 7 as Sunday", () => {
    const v = validate("41 3 * JAN-MAR mon-fri");
    expect(v.ok).toBe(true);
    expect(v.fields.month).toEqual([1, 2, 3]);
    expect(v.fields.weekday).toEqual([1, 2, 3, 4, 5]);
    expect(validate("0 9 * * 7").fields.weekday).toEqual([0]);
    expect(validate("0 */6 * * *").fields.hour).toEqual([0, 6, 12, 18]);
    expect(validate("0 9 * jul wed").ok).toBe(true);
  });

  it("restricts day and weekday only when they don't cover everything", () => {
    expect(validate("0 9 1-31 * 0-7")).toMatchObject({
      dayRestricted: false,
      weekdayRestricted: false,
    });
    expect(validate("0 9 */2 * 1")).toMatchObject({
      dayRestricted: true,
      weekdayRestricted: true,
    });
  });

  it("answers in plain words", () => {
    expect(validate("* * * * *").error).toBe(CRON_ERRORS.tooOften);
    expect(validate("0 9 L * *").error).toBe("L, W, # and ? aren't supported.");
    expect(validate("0 24 * * *").error).toBe("Hour must be between 0 and 23.");
    expect(validate("0 9 * *").error).toBe(
      "Use five fields: minute hour day month weekday.",
    );
    expect(validate("0 9 5-1 * *").error).toMatch(/runs backwards/);
    expect(validate("0 9 * * */0").error).toBe(
      "Weekday step must be between 1 and 7.",
    );
    expect(validate("0 9 31 2,4 *").error).toBe(
      "This never runs: no month has that day.",
    );
    expect(validate(null).ok).toBe(false);
  });
});

group("defaultMinute", () => {
  const ids = Array.from(
    { length: 400 },
    (_, i) => `e1000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
  );
  it("is never :00 or :30, stable per id and spread out", () => {
    const mins = ids.map(defaultMinute);
    mins.forEach((m) => {
      expect(m % 30).not.toBe(0);
      expect(m).toBeGreaterThan(0);
      expect(m).toBeLessThan(60);
    });
    expect(ids.map(defaultMinute)).toEqual(mins);
    expect(new Set(mins).size).toBeGreaterThan(40);
  });
});

group("normalizeZone", () => {
  it("normalises aliases and rejects non-zones", () => {
    expect(normalizeZone("Asia/Calcutta")).toBe("Asia/Kolkata");
    expect(normalizeZone("Asia/Kolkata")).toBe("Asia/Kolkata");
    expect(normalizeZone("asia/kolkata")).toBe("Asia/Kolkata");
    expect(normalizeZone("US/Eastern")).toBe("America/New_York");
    expect(normalizeZone("Europe/Kiev")).toBe("Europe/Kyiv");
    expect(normalizeZone("Etc/UTC")).toBe("UTC");
    expect(normalizeZone("Europe/Berlin")).toBe("Europe/Berlin");
    expect(normalizeZone("Mars/Olympus")).toBeNull();
    expect(normalizeZone("")).toBeNull();
  });

  it("offsets and short names", () => {
    expect(offsetMs(at("2026-10-21T00:00:00Z"), "Asia/Kolkata")).toBe(
      5.5 * HOUR,
    );
    expect(zoneAbbr("UTC", 0)).toBe("UTC");
    expect(zoneAbbr("America/New_York", at("2026-07-01T00:00:00Z"))).toBe(
      "EDT",
    );
    expect(zoneAbbr("America/New_York", at("2026-01-01T00:00:00Z"))).toBe(
      "EST",
    );
    expect(zoneAbbr("Asia/Kolkata", at("2026-07-01T00:00:00Z"))).toBe("IST");
  });
});

group("describe", () => {
  const ist = (preset, cron) => ({ preset, cron, tz: "Asia/Kolkata" });
  it("reads presets and Custom back", () => {
    expect(describe(ist("daily", "41 3 * * *"))).toBe("Every day at 03:41");
    expect(describe(ist("weekdays", "41 3 * * 1-5"))).toBe(
      "Every weekday at 03:41",
    );
    expect(describe(ist("weekly", "0 9 * * 1"))).toBe("Every Monday at 09:00");
    expect(describe(ist("cron", "41 3 * * 1-5"))).toBe(
      "At 03:41, Monday to Friday",
    );
    expect(
      describe({ preset: "hourly", interval_min: 60, minute: 41, tz: "UTC" }),
    ).toBe("Every hour at minute :41");
    expect(
      describe({
        preset: "every_n_hours",
        interval_min: 360,
        anchor_at: "2026-10-21T03:11:00Z",
        tz: "Asia/Kolkata",
      }),
    ).toBe("Every 6 hours at minute :41");
    expect(describe(null)).toBeNull();
  });

  it("names the zone when asked", () => {
    expect(
      describe(ist("daily", "17 3 * * *"), {
        zone: true,
        atMs: at("2026-10-20T00:00:00Z"),
      }),
    ).toBe("Every day at 03:17 IST");
    expect(
      describe(
        {
          preset: "daily",
          cron: "41 3 * * *",
          tz: "Asia/Kolkata",
          utc_locked: true,
        },
        { zone: true, atMs: 0 },
      ),
    ).toBe("Every day at 03:41 UTC");
    expect(() => describe(ist("daily", "17 3 * * *"), { zone: true })).toThrow(
      /atMs/,
    );
  });

  it("reads custom crons in words", () => {
    expect(describeCron("0 9 1 * 1")).toBe(
      "At 09:00, on day 1 of the month and on every Monday",
    );
    expect(describeCron("0 9 1,15 * *")).toBe(
      "At 09:00, on days 1 and 15 of the month",
    );
    expect(describeCron("30 8 * * 1,3")).toBe(
      "At 08:30, only on Monday and Wednesday",
    );
    expect(describeCron("5 * * * 1-5")).toBe(
      "Every hour at minute :05, Monday to Friday",
    );
    expect(describeCron("0 */6 * * *")).toBe(
      "At 00:00, 06:00, 12:00 and 18:00, every day",
    );
    expect(describeCron("10 */2 * * *")).toBe(
      "Every 2 hours at minute :10, from 00:10",
    );
    expect(describeCron("0 9 * 1-3 *")).toBe(
      "At 09:00, every day, January to March",
    );
    expect(describeCron("0 9 * * 6,0")).toBe(
      "At 09:00, only on Saturday and Sunday",
    );
    expect(describeCron("* * * * *")).toBeNull();
  });
});

group("toUtcCron", () => {
  it("keeps UTC cadences and shifts fixed-offset zones", () => {
    expect(toUtcCron({ preset: "daily", cron: "41 3 * * *", tz: "UTC" })).toBe(
      "41 3 * * *",
    );
    expect(
      toUtcCron({ preset: "daily", cron: "41 9 * * *", tz: "Asia/Kolkata" }),
    ).toBe("11 4 * * *");
    // 03:41 IST on Monday to Friday = 22:11 UTC on Sunday to Thursday.
    expect(
      toUtcCron({
        preset: "weekdays",
        cron: "41 3 * * 1-5",
        tz: "Asia/Kolkata",
      }),
    ).toBe("11 22 * * 0-4");
    expect(
      toUtcCron({
        preset: "daily",
        cron: "41 3 * * *",
        tz: "Asia/Kolkata",
        utc_locked: true,
      }),
    ).toBe("41 3 * * *");
  });

  it("needs the date for zones that change their clocks", () => {
    const ny = { preset: "daily", cron: "0 9 * * *", tz: "America/New_York" };
    expect(() => toUtcCron(ny)).toThrow(/atMs/);
    expect(toUtcCron(ny, at("2026-07-01T00:00:00Z"))).toBe("0 13 * * *");
    expect(toUtcCron(ny, at("2026-01-01T00:00:00Z"))).toBe("0 14 * * *");
  });

  it("says null when one UTC line can't hold it", () => {
    expect(
      toUtcCron({ preset: "cron", cron: "41 3 1 * *", tz: "Asia/Kolkata" }),
    ).toBeNull();
    expect(
      toUtcCron({
        preset: "cron",
        cron: "41 3,20 * * 1-5",
        tz: "Asia/Kolkata",
      }),
    ).toBeNull();
  });

  it("turns intervals into UTC hours from the anchor", () => {
    expect(
      toUtcCron({
        preset: "hourly",
        interval_min: 60,
        anchor_at: "2026-10-21T00:11:00Z",
        tz: "Asia/Kolkata",
      }),
    ).toBe("11 * * * *");
    expect(
      toUtcCron({
        preset: "every_n_hours",
        interval_min: 360,
        anchor_at: "2026-10-21T22:11:00Z",
        tz: "Asia/Kolkata",
      }),
    ).toBe("11 4,10,16,22 * * *");
    expect(
      toUtcCron(
        { preset: "hourly", interval_min: 60, minute: 41, tz: "Asia/Kolkata" },
        at("2026-10-21T00:00:00Z"),
      ),
    ).toBe("11 * * * *");
    expect(() =>
      toUtcCron({
        preset: "every_n_hours",
        interval_min: 120,
        minute: 5,
        tz: "UTC",
      }),
    ).toThrow(/atMs/);
    expect(toUtcCron(null)).toBeNull();
  });
});
