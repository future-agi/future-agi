import { describe, it, expect } from "vitest";
import { formatLatency, formatCost, formatTokenCount } from "../formatters";

describe("formatLatency", () => {
  it("returns '-' for null", () => {
    expect(formatLatency(null)).toBe("-");
  });

  it("returns '-' for undefined", () => {
    expect(formatLatency(undefined)).toBe("-");
  });

  it("returns '-' for NaN", () => {
    expect(formatLatency(NaN)).toBe("-");
  });

  it("formats 0ms", () => {
    expect(formatLatency(0)).toBe("0ms");
  });

  it("formats sub-second values in ms", () => {
    expect(formatLatency(500)).toBe("500ms");
    expect(formatLatency(1)).toBe("1ms");
    expect(formatLatency(999)).toBe("999ms");
  });

  it("formats seconds with one decimal", () => {
    expect(formatLatency(1000)).toBe("1.0s");
    expect(formatLatency(1500)).toBe("1.5s");
    expect(formatLatency(59999)).toBe("60.0s");
  });

  it("formats minutes and seconds", () => {
    expect(formatLatency(60000)).toBe("1m");
    expect(formatLatency(65000)).toBe("1m 5s");
    expect(formatLatency(125000)).toBe("2m 5s");
  });

  it("carries rounded seconds into the minute instead of showing 60s", () => {
    expect(formatLatency(119600)).toBe("2m");
    expect(formatLatency(179500)).toBe("3m");
  });

  it("formats multi-minute voice calls as minutes and seconds", () => {
    expect(formatLatency(139833)).toBe("2m 20s");
  });
});

describe("formatCost", () => {
  it("returns '-' for null", () => {
    expect(formatCost(null)).toBe("-");
  });

  it("returns '-' for undefined", () => {
    expect(formatCost(undefined)).toBe("-");
  });

  it("returns '-' for NaN", () => {
    expect(formatCost(NaN)).toBe("-");
  });

  it("formats zero as $0.00", () => {
    expect(formatCost(0)).toBe("$0.00");
  });

  it("formats small values with 4 decimals", () => {
    expect(formatCost(0.0015)).toBe("$0.0015");
    expect(formatCost(0.0001)).toBe("$0.0001");
  });

  it("formats larger values with 2 decimals", () => {
    expect(formatCost(0.01)).toBe("$0.01");
    expect(formatCost(1.23)).toBe("$1.23");
    expect(formatCost(100.5)).toBe("$100.50");
  });
});

describe("formatTokenCount", () => {
  it("returns '-' for null", () => {
    expect(formatTokenCount(null)).toBe("-");
  });

  it("returns '-' for undefined", () => {
    expect(formatTokenCount(undefined)).toBe("-");
  });

  it("returns '-' for NaN", () => {
    expect(formatTokenCount(NaN)).toBe("-");
  });

  it("formats zero", () => {
    expect(formatTokenCount(0)).toBe("0");
  });

  it("formats small numbers with locale separator", () => {
    expect(formatTokenCount(999)).toBe("999");
  });

  it("formats thousands with K suffix", () => {
    expect(formatTokenCount(10000)).toBe("10.0K");
    expect(formatTokenCount(12345)).toBe("12.3K");
    expect(formatTokenCount(100000)).toBe("100.0K");
  });
});
