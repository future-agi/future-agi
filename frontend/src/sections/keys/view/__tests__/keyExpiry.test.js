import { describe, expect, it } from "vitest";
import { formatApiKeyExpiry, isOrgApiKeyExpired } from "../keyExpiry";

describe("isOrgApiKeyExpired", () => {
  it("treats a null expiry as never expired", () => {
    expect(isOrgApiKeyExpired(null, Date.parse("2026-08-18T12:00:00Z"))).toBe(
      false,
    );
  });

  it("treats a future expiry as active", () => {
    expect(
      isOrgApiKeyExpired(
        "2026-12-01T00:00:00Z",
        Date.parse("2026-08-18T12:00:00Z"),
      ),
    ).toBe(false);
  });

  it("treats a past expiry as expired", () => {
    expect(
      isOrgApiKeyExpired(
        "2026-01-01T00:00:00Z",
        Date.parse("2026-08-18T12:00:00Z"),
      ),
    ).toBe(true);
  });
});

describe("formatApiKeyExpiry", () => {
  it("renders Never when expiry is missing", () => {
    expect(formatApiKeyExpiry(null)).toBe("Never");
    expect(formatApiKeyExpiry("")).toBe("Never");
  });

  it("formats a real expiry date", () => {
    expect(formatApiKeyExpiry("2026-12-01T15:30:00Z")).toBe("12-01-2026");
  });
});
