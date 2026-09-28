import { describe, expect, it } from "vitest";
import { getDateRange } from "../insights-date-selector";

const formatDate = (date) => date.toISOString().split("T")[0];

const expectedYesterdayRange = (reference) => {
  const yesterday = new Date(reference);
  yesterday.setDate(yesterday.getDate() - 1);
  const date = formatDate(yesterday);

  return `${date}:${date}`;
};

describe("getDateRange", () => {
  it("uses yesterday for both date boundaries", () => {
    const reference = new Date(2026, 5, 15, 12);

    expect(getDateRange("yesterday", reference)).toBe(
      expectedYesterdayRange(reference),
    );
  });

  it("handles yesterday across a month boundary", () => {
    const reference = new Date(2024, 2, 1, 12);

    expect(getDateRange("yesterday", reference)).toBe(
      expectedYesterdayRange(reference),
    );
  });

  it("handles yesterday across a year boundary", () => {
    const reference = new Date(2026, 0, 1, 12);

    expect(getDateRange("yesterday", reference)).toBe(
      expectedYesterdayRange(reference),
    );
  });

  it("does not mutate the reference date", () => {
    const reference = new Date(2026, 5, 15, 12);
    const originalTime = reference.getTime();

    getDateRange("yesterday", reference);

    expect(reference.getTime()).toBe(originalTime);
  });
});
