// Bad cadence input never computes (notify Step 0 review must-fix 5), and
// the review's cron follow-ups: two wall times in one gap, leap days,
// SAT-SUN ranges, presets that don't fit their cron, offset zones.
import { describe as group, expect, it } from "vitest";
import {
  CADENCE_ERRORS,
  describe,
  nextRuns,
  nextRunsDetail,
  normalizeZone,
  toUtcCron,
  validate,
  validateCadence,
} from "../cronModel";

const at = (iso) => Date.parse(iso);
const isoOf = (ms) => new Date(ms).toISOString();
const FROM = at("2026-10-21T00:00:00Z");

group("a bad zone is refused, never read as UTC", () => {
  const typo = { preset: "daily", cron: "0 9 * * *", tz: "Asia/Kolkatta" };
  it("throws in plain words from every reader", () => {
    expect(() => nextRuns(typo, FROM)).toThrow(
      "“Asia/Kolkatta” isn't a time zone. Use a name like Asia/Kolkata or America/New_York.",
    );
    expect(() => describe(typo)).toThrow(/isn't a time zone/);
    expect(() => toUtcCron(typo, FROM)).toThrow(/isn't a time zone/);
    expect(() =>
      nextRuns({ preset: "hourly", minute: 5, tz: "Mars/Olympus" }, FROM),
    ).toThrow(/isn't a time zone/);
  });

  it("validateCadence says so for forms", () => {
    expect(validateCadence(typo)).toEqual({
      ok: false,
      code: "zone",
      error: CADENCE_ERRORS.zone("Asia/Kolkatta"),
    });
  });

  it("only a missing zone means UTC; aliases still work", () => {
    const utc = nextRuns({ preset: "daily", cron: "0 9 * * *" }, FROM, 1);
    expect(isoOf(utc[0])).toBe("2026-10-21T09:00:00.000Z");
    expect(
      nextRuns({ preset: "daily", cron: "0 9 * * *", tz: "  " }, FROM, 1),
    ).toEqual(utc);
    expect(
      isoOf(
        nextRuns(
          { preset: "daily", cron: "0 9 * * *", tz: "Asia/Calcutta" },
          FROM,
          1,
        )[0],
      ),
    ).toBe("2026-10-21T03:30:00.000Z");
  });

  it("refuses fixed offsets; Etc/GMT zones stay (should-fix 7)", () => {
    ["+05:30", "-08:00", "+0530"].forEach((tz) =>
      expect(normalizeZone(tz), tz).toBeNull(),
    );
    expect(normalizeZone("Etc/GMT-5")).toBe("Etc/GMT-5");
    expect(
      validateCadence({ preset: "daily", cron: "0 9 * * *", tz: "+05:30" })
        .code,
    ).toBe("zone");
  });
});

group("intervals check their minute and anchor", () => {
  const hourly = (extra) => ({
    preset: "hourly",
    interval_min: 60,
    tz: "UTC",
    ...extra,
  });
  it("a minute outside 0-59 or not whole is refused, never 1970", () => {
    [75, -1, 1.5, "21", Number.NaN].forEach((minute) => {
      expect(() => nextRuns(hourly({ minute }), FROM), String(minute)).toThrow(
        /whole number from 0 to 59/,
      );
      expect(validateCadence(hourly({ minute })).code).toBe("minute");
    });
    expect(() => describe(hourly({ minute: 75 }))).toThrow(/0 to 59/);
    expect(isoOf(nextRuns(hourly({ minute: 0 }), FROM, 1)[0])).toBe(
      "2026-10-21T01:00:00.000Z",
    );
    expect(isoOf(nextRuns(hourly({ minute: 59 }), FROM, 1)[0])).toBe(
      "2026-10-21T00:59:00.000Z",
    );
  });

  it("an anchor that isn't a time is refused, never NaN", () => {
    const bad = hourly({ anchor_at: "tomorrow at nine" });
    expect(() => nextRuns(bad, FROM)).toThrow(
      "The first run time “tomorrow at nine” isn't a date and time.",
    );
    expect(() => toUtcCron(bad)).toThrow(/isn't a date and time/);
    expect(validateCadence(bad).code).toBe("anchor");
    const good = nextRuns(
      hourly({ anchor_at: "2026-10-21T03:41:00Z" }),
      FROM,
      2,
    );
    good.forEach((ms) => expect(Number.isFinite(ms)).toBe(true));
  });

  it("an interval that doesn't divide the day, or an unknown preset, is refused", () => {
    expect(
      validateCadence({ preset: "every_n_hours", interval_min: 300, tz: "UTC" })
        .code,
    ).toBe("interval");
    expect(() =>
      nextRuns({ preset: "fortnightly", cron: "0 9 * * *" }, FROM),
    ).toThrow(/isn't a repeat option/);
    expect(validateCadence({ preset: "fortnightly" }).code).toBe("preset");
  });

  it("a good cadence passes; a bad cron gives the cron's own words", () => {
    expect(validateCadence(null)).toEqual({ ok: true });
    expect(validateCadence(hourly({ minute: 21 }))).toEqual({ ok: true });
    expect(
      validateCadence({ preset: "cron", cron: "*/5 * * * *", tz: "UTC" }),
    ).toEqual({
      ok: false,
      code: "tooOften",
      error: validate("*/5 * * * *").error,
    });
  });
});

group("review follow-ups on the cron model", () => {
  it("0 2,3 in New York on 8 Mar is one run at 03:00 (should-fix 9)", () => {
    const runs = nextRunsDetail(
      { preset: "cron", cron: "0 2,3 * * *", tz: "America/New_York" },
      at("2026-03-08T05:00:00Z"),
      2,
    );
    expect(runs.map((r) => [isoOf(r.at_ms), r.shifted_from])).toEqual([
      ["2026-03-08T07:00:00.000Z", "02:00"],
      ["2026-03-09T06:00:00.000Z", null],
    ]);
  });

  it("a leap-day cron returns every run asked for (should-fix 2)", () => {
    const runs = nextRuns(
      { preset: "cron", cron: "0 9 29 2 *", tz: "UTC" },
      FROM,
      3,
    );
    expect(runs.map(isoOf)).toEqual([
      "2028-02-29T09:00:00.000Z",
      "2032-02-29T09:00:00.000Z",
      "2036-02-29T09:00:00.000Z",
    ]);
  });

  it("SAT-SUN and FRI-SUN read SUN as 7 (should-fix 3)", () => {
    expect(validate("0 9 * * SAT-SUN").fields.weekday).toEqual([0, 6]);
    expect(validate("0 9 * * FRI-SUN").fields.weekday).toEqual([0, 5, 6]);
    expect(validate("0 9 * * 5-7").fields.weekday).toEqual([0, 5, 6]);
    expect(validate("0 9 * * 5-0").ok).toBe(false);
    expect(validate("0 9 * * SUN-SAT").fields.weekday).toHaveLength(7);
  });

  it("a preset that doesn't fit its cron reads the cron (should-fix 4)", () => {
    expect(describe({ preset: "daily", cron: "0 9 * * 1", tz: "UTC" })).toBe(
      "At 09:00, only on Monday",
    );
    expect(describe({ preset: "weekly", cron: "0 9 * * *", tz: "UTC" })).toBe(
      "At 09:00, every day",
    );
    expect(
      describe({ preset: "weekdays", cron: "0 9 * * 1-3", tz: "UTC" }),
    ).toBe("At 09:00, Monday to Wednesday");
    expect(describe({ preset: "daily", cron: "0 9 * * *", tz: "UTC" })).toBe(
      "Every day at 09:00",
    );
  });
});
