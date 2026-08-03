import { describe, expect, it } from "vitest";

import {
  fDate,
  fDateTime,
  fTimestamp,
  fToNow,
  fToNowStrict,
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
