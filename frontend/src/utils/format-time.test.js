import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fDate,
  fDateLocal,
  fDateTime,
  fDateTimeLocal,
  describeInstant,
  fTimestamp,
  fToNow,
  fToNowStrict,
  relativeTime,
  toValidDate,
} from "./format-time";

const UNREADABLE = ["0000-00-00 00:00:00", "not-a-date", "2026-13-45"];
const VALID_ISO = "2026-03-15T10:30:00.000Z";

describe("toValidDate", () => {
  it("returns null for nullish and blank values", () => {
    expect(toValidDate(null)).toBeNull();
    expect(toValidDate(undefined)).toBeNull();
    expect(toValidDate("")).toBeNull();
  });

  it("returns null for truthy values the Date parser can't read", () => {
    expect(toValidDate("0000-00-00 00:00:00")).toBeNull();
    expect(toValidDate("not-a-date")).toBeNull();
  });

  it("returns a Date for a valid ISO string", () => {
    const parsed = toValidDate(VALID_ISO);
    expect(parsed).toBeInstanceOf(Date);
    expect(parsed.toISOString()).toBe(VALID_ISO);
  });

  it("returns an equivalent Date for a Date instance", () => {
    const input = new Date(VALID_ISO);
    const parsed = toValidDate(input);
    expect(parsed).toBeInstanceOf(Date);
    expect(parsed.getTime()).toBe(input.getTime());
  });

  it("returns a Date for a numeric epoch", () => {
    const parsed = toValidDate(1773567000000);
    expect(parsed).toBeInstanceOf(Date);
    expect(parsed.getTime()).toBe(1773567000000);
  });
});

const INSTANT = "2025-10-31T00:00:00Z";
const NativeDateTimeFormat = Intl.DateTimeFormat;

describe("local instant formatters", () => {
  afterEach(() => vi.restoreAllMocks());

  it.each([
    ["Asia/Kolkata", "31 Oct 2025", "5:30 AM", "UTC+05:30"],
    ["America/Los_Angeles", "30 Oct 2025", "5:00 PM", "UTC-07:00"],
    ["UTC", "31 Oct 2025", "12:00 AM", "UTC+00:00"],
    ["Europe/Berlin", "31 Oct 2025", "1:00 AM", "UTC+01:00"],
  ])("formats the same instant in %s", (timeZone, date, time, offset) => {
    const options = { timeZone };
    expect(fDateLocal(INSTANT, options)).toBe(date);
    expect(fDateTimeLocal(INSTANT, options)).toBe(`${date}, ${time}`);
    expect(describeInstant(INSTANT, options)).toEqual({
      local: `${date}, ${time}`,
      zone: timeZone,
      offset,
      utc: "2025-10-31T00:00:00.000Z",
    });
  });

  it.each([
    ["Asia/Kolkata", "1 Jan 2026", "5:00 AM"],
    ["America/New_York", "31 Dec 2025", "6:30 PM"],
  ])("handles the year boundary in %s", (timeZone, date, time) => {
    const value = "2025-12-31T23:30:00Z";
    expect(fDateLocal(value, { timeZone })).toBe(date);
    expect(fDateTimeLocal(value, { timeZone })).toBe(`${date}, ${time}`);
  });

  it.each([
    ["2025-03-09T09:30:00Z", "9 Mar 2025, 1:30 AM", "UTC-08:00"],
    ["2025-03-09T10:30:00Z", "9 Mar 2025, 3:30 AM", "UTC-07:00"],
    ["2025-11-02T08:30:00Z", "2 Nov 2025, 1:30 AM", "UTC-07:00"],
    ["2025-11-02T09:30:00Z", "2 Nov 2025, 1:30 AM", "UTC-08:00"],
  ])("preserves DST offset and UTC identity for %s", (value, local, offset) => {
    const options = { timeZone: "America/Los_Angeles" };
    expect(fDateTimeLocal(value, options)).toBe(local);
    expect(describeInstant(value, options)).toEqual({
      local,
      zone: options.timeZone,
      offset,
      utc: new Date(value).toISOString(),
    });
  });

  it.each([
    "2025-10-31T05:30:00+05:30",
    "2025-10-30T17:00:00-0700",
    new Date(INSTANT),
    Date.parse(INSTANT),
  ])("accepts equivalent zoned strings, Dates and epoch-ms: %s", (value) => {
    const options = { timeZone: "Asia/Kolkata" };
    expect(fDateLocal(value, options)).toBe("31 Oct 2025");
    expect(fDateTimeLocal(value, options)).toBe("31 Oct 2025, 5:30 AM");
    expect(describeInstant(value, options)).toEqual(
      describeInstant(INSTANT, options),
    );
  });

  it("accepts epoch zero without treating it as missing", () => {
    const options = { timeZone: "UTC" };
    expect(fDateLocal(0, options)).toBe("1 Jan 1970");
    expect(fDateTimeLocal(0, options)).toBe("1 Jan 1970, 12:00 AM");
    expect(describeInstant(0, options).utc).toBe("1970-01-01T00:00:00.000Z");
  });

  it.each([
    "2025-10-31T05:30:00",
    "2025-10-31",
    null,
    undefined,
    "",
    "garbage",
    {},
    NaN,
    Infinity,
    new Date("x"),
    "2025-13-31T00:00:00Z",
    "2025-02-30T00:00:00Z",
    "Fri, 31 Oct 2025 00:00:00 GMT",
    true,
    [],
  ])("rejects missing, invalid or non-instant input: %s", (value) => {
    const options = { timeZone: "UTC" };
    expect(fDateLocal(value, options)).toBe("");
    expect(fDateTimeLocal(value, options)).toBe("");
    expect(describeInstant(value, options)).toBeNull();
  });

  it.each(["en-US", "en-GB", "en-IN"])(
    "keeps English month names and the requested zone under host locale %s",
    (hostLocale) => {
      vi.spyOn(Intl, "DateTimeFormat").mockImplementation(
        (locale, options) =>
          new NativeDateTimeFormat(locale || hostLocale, options),
      );
      const options = { timeZone: "Asia/Kolkata" };
      expect(fDateLocal(INSTANT, options)).toBe("31 Oct 2025");
      expect(fDateTimeLocal(INSTANT, options)).toBe("31 Oct 2025, 5:30 AM");
      expect(describeInstant(INSTANT, options).local).toBe(
        "31 Oct 2025, 5:30 AM",
      );
    },
  );

  it("falls back to UTC for an invalid explicit display zone", () => {
    const options = { timeZone: "Invalid/Zone" };
    expect(fDateLocal(INSTANT, options)).toBe("31 Oct 2025");
    expect(fDateTimeLocal(INSTANT, options)).toBe("31 Oct 2025, 12:00 AM");
    expect(describeInstant(INSTANT, options).zone).toBe("UTC");
  });
});

describe("getDisplayTimeZone", () => {
  beforeEach(() => vi.resetModules());
  afterEach(() => vi.restoreAllMocks());

  function mockDetectedZone(resolvedOptions) {
    return vi
      .spyOn(Intl, "DateTimeFormat")
      .mockImplementation((locale, options) =>
        locale === undefined
          ? { resolvedOptions }
          : new NativeDateTimeFormat(locale, options),
      );
  }

  it("detects one zone for all helpers and retains it until page reload", async () => {
    const resolvedOptions = vi
      .fn()
      .mockReturnValue({ timeZone: "Asia/Kolkata" });
    mockDetectedZone(resolvedOptions);
    const helpers = await import("./format-time");
    expect(helpers.getDisplayTimeZone()).toBe("Asia/Kolkata");
    resolvedOptions.mockReturnValue({ timeZone: "America/Los_Angeles" });
    expect(helpers.fDateLocal(INSTANT)).toBe("31 Oct 2025");
    expect(helpers.fDateTimeLocal(INSTANT)).toBe("31 Oct 2025, 5:30 AM");
    expect(helpers.describeInstant(INSTANT).zone).toBe("Asia/Kolkata");
    expect(helpers.getDisplayTimeZone()).toBe("Asia/Kolkata");
    expect(resolvedOptions).toHaveBeenCalledTimes(1);
  });

  it.each([{}, { timeZone: "" }, { timeZone: "Invalid/Zone" }])(
    "uses UTC when the detected zone is missing or invalid: %s",
    async (options) => {
      mockDetectedZone(() => options);
      const helpers = await import("./format-time");
      expect(helpers.getDisplayTimeZone()).toBe("UTC");
      expect(helpers.fDateTimeLocal(INSTANT)).toBe("31 Oct 2025, 12:00 AM");
    },
  );

  it("uses UTC when resolvedOptions throws", async () => {
    mockDetectedZone(() => {
      throw new Error("No timezone available");
    });
    const { getDisplayTimeZone } = await import("./format-time");
    expect(getDisplayTimeZone()).toBe("UTC");
  });

  it("uses UTC when reading the timeZone property throws", async () => {
    mockDetectedZone(() => ({
      get timeZone() {
        throw new Error("No timezone available");
      },
    }));
    const { getDisplayTimeZone } = await import("./format-time");
    expect(getDisplayTimeZone()).toBe("UTC");
  });

  it("uses UTC when constructing the detector throws", async () => {
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(() => {
      throw new Error("No timezone available");
    });
    const { getDisplayTimeZone } = await import("./format-time");
    expect(getDisplayTimeZone()).toBe("UTC");
  });
});

describe("relativeTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-15T12:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  const ago = (ms) => new Date(Date.now() - ms).toISOString();

  it("returns an em dash for missing or invalid input", () => {
    expect(relativeTime(undefined)).toBe("—");
    expect(relativeTime("not-a-date")).toBe("—");
  });

  it("formats sub-minute as just now", () => {
    expect(relativeTime(ago(30 * 1000))).toBe("just now");
  });

  it("formats minutes, hours, days, months and years", () => {
    expect(relativeTime(ago(60 * 1000))).toBe("1 minute ago");
    expect(relativeTime(ago(3 * 3600 * 1000))).toBe("3 hours ago");
    expect(relativeTime(ago(2 * 86400 * 1000))).toBe("2 days ago");
    expect(relativeTime(ago(45 * 86400 * 1000))).toBe("1 month ago");
    expect(relativeTime(ago(400 * 86400 * 1000))).toBe("1 year ago");
  });
});

describe("date formatters on unreadable input", () => {
  const formatters = [
    ["fDate", fDate],
    ["fDateTime", fDateTime],
    ["fTimestamp", fTimestamp],
    ["fToNow", fToNow],
    ["fToNowStrict", fToNowStrict],
  ];

  for (const [name, fn] of formatters) {
    it(`${name} returns an empty string instead of throwing`, () => {
      for (const value of UNREADABLE) {
        expect(() => fn(value)).not.toThrow();
        expect(fn(value)).toBe("");
      }
    });
  }
});

describe("date formatters on readable input", () => {
  it("still formats a valid date", () => {
    expect(fDate(VALID_ISO)).toBe("15 Mar 2026");
    expect(fTimestamp(VALID_ISO)).toBe(new Date(VALID_ISO).getTime());
    expect(fToNow(VALID_ISO)).toMatch(/ago|in /);
    expect(fToNowStrict(VALID_ISO)).toMatch(/ago|in /);
    expect(fDateTime(VALID_ISO)).toContain("15 Mar 2026");
  });

  it("still returns an empty string for nullish input", () => {
    for (const [, fn] of [
      ["fDate", fDate],
      ["fDateTime", fDateTime],
      ["fTimestamp", fTimestamp],
      ["fToNow", fToNow],
      ["fToNowStrict", fToNowStrict],
    ]) {
      expect(fn(null)).toBe("");
      expect(fn(undefined)).toBe("");
      expect(fn("")).toBe("");
    }
  });
});
