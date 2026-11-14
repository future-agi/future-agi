import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import AlertingMonitoringSection from "./AlertingMonitoringSection";

const mockUpdateMutate = vi.fn();
let currentTab = "rules";

const config = {
  alerting: {
    enabled: true,
    rules: [
      { name: "high-errors", metric: "error_count", condition: ">=", threshold: 10 },
      { name: "high-cost", metric: "cost_total", condition: ">", threshold: 50 },
    ],
    channels: [
      { name: "ops", type: "webhook", url: "https://hooks.example/ops" },
      { name: "oncall", type: "slack", url: "https://hooks.example/oncall" },
    ],
  },
};

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ tab: currentTab }),
  useNavigate: () => vi.fn(),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "default", isLoading: false }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: config, isLoading: false }),
  useProviderHealth: () => ({ data: null }),
  useUpdateConfig: () => ({
    mutate: mockUpdateMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: null }),
}));

describe("AlertingMonitoringSection", () => {
  beforeEach(() => {
    mockUpdateMutate.mockClear();
  });

  it("deletes a rule by saving the list without it", async () => {
    currentTab = "rules";
    const user = userEvent.setup();
    render(<AlertingMonitoringSection />);

    await user.click(
      screen.getByRole("button", { name: "Delete rule high-errors" }),
    );

    expect(mockUpdateMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      config: { alerting: { rules: [config.alerting.rules[1]] } },
    });
  });

  it("deletes a channel by saving the list without it", async () => {
    currentTab = "channels";
    const user = userEvent.setup();
    render(<AlertingMonitoringSection />);

    await user.click(
      screen.getByRole("button", { name: "Delete channel oncall" }),
    );

    expect(mockUpdateMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      config: { alerting: { channels: [config.alerting.channels[0]] } },
    });
  });
});
