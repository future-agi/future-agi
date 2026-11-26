import { describe, it, expect } from "vitest";
import { interpolateColorTokenBasedOnScore } from "../utils";

describe("interpolateColorTokenBasedOnScore", () => {
  const yellowBand = [60, 65.25, 75.5, 79.99];
  const otherBands = [0, 19, 25, 45, 55, 85, 98, 100];

  it("resolves the yellow band through theme-aware tokens", () => {
    yellowBand.forEach((score) => {
      const { bgcolor, color } = interpolateColorTokenBasedOnScore(score, 100);
      expect(bgcolor).toBe("var(--eval-score-yellow-bg)");
      expect(color).toBe("var(--eval-score-yellow-text)");
    });
  });

  it("leaves the other bands on their existing tokens", () => {
    // Designer signed off on green and red in dark theme, so they stay put.
    otherBands.forEach((score) => {
      const { bgcolor } = interpolateColorTokenBasedOnScore(score, 100);
      expect(bgcolor).not.toBe("var(--eval-score-yellow-bg)");
      expect(bgcolor).toBeTruthy();
    });
  });

  it("keeps the band boundaries where they were", () => {
    // 60 opens the yellow band and 80 closes it.
    expect(interpolateColorTokenBasedOnScore(59.99, 100).bgcolor).not.toBe(
      "var(--eval-score-yellow-bg)",
    );
    expect(interpolateColorTokenBasedOnScore(60, 100).bgcolor).toBe(
      "var(--eval-score-yellow-bg)",
    );
    expect(interpolateColorTokenBasedOnScore(80, 100).bgcolor).not.toBe(
      "var(--eval-score-yellow-bg)",
    );
  });

  it("still honours reverse and clamping", () => {
    // reverse flips the score, so a low raw score lands in the yellow band.
    expect(interpolateColorTokenBasedOnScore(35, 100, true).bgcolor).toBe(
      "var(--eval-score-yellow-bg)",
    );
    expect(interpolateColorTokenBasedOnScore(-10, 100).bgcolor).toBe(
      interpolateColorTokenBasedOnScore(0, 100).bgcolor,
    );
    expect(interpolateColorTokenBasedOnScore(150, 100).bgcolor).toBe(
      interpolateColorTokenBasedOnScore(100, 100).bgcolor,
    );
  });
});
