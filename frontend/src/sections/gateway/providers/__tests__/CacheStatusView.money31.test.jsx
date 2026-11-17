import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CacheStatusView from "../CacheStatusView";

const overview = vi.hoisted(() => ({ data: undefined, isError: false }));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => overview,
}));
vi.mock("../hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../../settings/OrgConfigEditor", () => ({ default: () => null }));

function savings(data) {
  overview.data = data;
  render(<CacheStatusView config={{ cache: { enabled: true } }} />);
  return screen.getByText("Est. savings").parentElement.textContent;
}

describe("MONEY31 r2 cache exact savings", () => {
  beforeEach(() => {
    overview.isError = false;
  });

  it.each([
    ["0.1", 3, 2, "$0.20"],
    ["1e-9", 3, 2, "$0.000000002"],
    ["12345678901234567.89", 3, 2, "$24691357802469135.78"],
  ])(
    "keeps original cost %s through exact multiplication",
    (cost, requests, hits, expected) => {
      expect(
        savings({
          total_cost: cost,
          total_requests: requests,
          cache_hits: hits,
        }),
      ).toBe(`Est. savings${expected}`);
    },
  );

  it("shows the exact rational estimate rather than rounding a repeating decimal", () => {
    expect(
      savings({ total_cost: "0.1", total_requests: 4, cache_hits: 1 }),
    ).toBe("Est. savings$0.10 / 3");
  });

  it.each([null, undefined, "", NaN, "unpriced"])(
    "never invents zero savings from unknown cost %s",
    (cost) => {
      const text = savings({
        total_cost: cost,
        total_requests: 3,
        cache_hits: 2,
      });
      expect(text).toBe("Est. savings—");
      expect(text).not.toMatch(/\$|%/);
    },
  );

  it.each([null, "", NaN, "unpriced"])(
    "keeps explicitly unknown server savings %s unknown",
    (cacheSavings) => {
      expect(
        savings({
          total_cost: "1",
          total_requests: 3,
          cache_hits: 2,
          cacheSavings,
        }),
      ).toBe("Est. savings—");
    },
  );

  it.each([0, "0"])("preserves genuine zero cost %s", (cost) => {
    expect(
      savings({ total_cost: cost, total_requests: 3, cache_hits: 2 }),
    ).toBe("Est. savings$0.00");
  });

  it.each([
    ["0.1", "$0.10"],
    ["0.2", "$0.20"],
    ["1e-9", "$0.000000001"],
    ["12345678901234567.89", "$12345678901234567.89"],
    [0, "$0.00"],
    ["0", "$0.00"],
  ])("preserves server decimal savings %s", (cacheSavings, expected) => {
    expect(
      savings({
        total_cost: "1",
        total_requests: 3,
        cache_hits: 2,
        cacheSavings,
      }),
    ).toBe(`Est. savings${expected}`);
  });

  it("keeps wholly unpriced requests unknown", () => {
    expect(
      savings({
        total_cost: { value: "0", unpriced_requests: 3 },
        total_requests: 3,
        cache_hits: 2,
      }),
    ).toBe("Est. savings—");
  });

  it("preserves the exact rational lower bound for partially unpriced requests", () => {
    expect(
      savings({
        total_cost: { value: "0.1", unpriced_requests: 1 },
        total_requests: 4,
        cache_hits: 1,
      }),
    ).toBe("Est. savings≥ $0.10 / 3");
  });

  it("keeps a partial unpriced zero bound unknown without a denominator", () => {
    expect(
      savings({
        total_cost: { value: "0", unpriced_requests: 1 },
        total_requests: 4,
        cache_hits: 1,
      }),
    ).toBe("Est. savings—");
  });

  it("keeps failed overview data unknown", () => {
    overview.isError = true;
    expect(
      savings({
        total_cost: "1",
        total_requests: 3,
        cache_hits: 2,
        cacheSavings: "1",
      }),
    ).toBe("Est. savings—");
  });
});
