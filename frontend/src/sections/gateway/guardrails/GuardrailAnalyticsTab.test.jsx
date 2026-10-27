import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import GuardrailAnalyticsTab from "./GuardrailAnalyticsTab";

vi.mock("react-apexcharts", () => ({
  default: () => <div>chart</div>,
}));

vi.mock("./hooks/useGuardrailAnalytics", () => ({
  useGuardrailOverview: () => ({
    isLoading: false,
    data: {
      trigger_rate: 12.34,
      block_count: 2,
      warn_count: 3,
      avg_guardrail_latency_ms: 45,
    },
  }),
  useGuardrailRules: () => ({
    isLoading: false,
    data: [
      {
        rule: "pii-detection",
        trigger_count: 5,
        block_count: 2,
        warn_count: 1,
      },
    ],
  }),
  useGuardrailTrends: () => ({
    isLoading: false,
    data: [
      {
        timestamp: "2026-05-25T00:00:00Z",
        trigger_count: 5,
        block_count: 2,
        warn_count: 1,
      },
    ],
  }),
}));

describe("GuardrailAnalyticsTab", () => {
  it("renders canonical snake_case guardrail analytics fields", () => {
    render(<GuardrailAnalyticsTab gatewayId="default" />);

    expect(screen.getByText("12.3%")).toBeInTheDocument();
    expect(screen.getByText("45ms")).toBeInTheDocument();
    expect(screen.getByText("pii-detection")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getAllByText("2").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("1").length).toBeGreaterThanOrEqual(1);
  });
});
