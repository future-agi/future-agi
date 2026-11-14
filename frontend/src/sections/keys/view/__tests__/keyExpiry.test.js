import { afterEach, describe, expect, it, vi } from "vitest";
import { expiresAtFromDate } from "../keyExpiry";

afterEach(() => vi.unstubAllEnvs());

describe("picked expiry date timezone", () => {
  it.each([
    ["UTC", "2026-03-08T23:59:59.999Z"],
    // US daylight saving starts on this date: end of day is UTC-7, not UTC-8.
    ["America/Los_Angeles", "2026-03-09T06:59:59.999Z"],
    ["Asia/Kolkata", "2026-03-08T18:29:59.999Z"],
  ])("preserves the administrator's calendar day in %s", (zone, expected) => {
    vi.stubEnv("TZ", zone);
    expect(expiresAtFromDate(new Date(2026, 2, 8, 12))).toBe(expected);
  });

  it("keeps no-expiry and invalid input distinct from a valid timestamp", () => {
    expect(expiresAtFromDate(null)).toBeNull();
    expect(expiresAtFromDate(new Date("invalid"))).toBeNull();
  });
});
