import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import AlertingMonitoringSection from "./AlertingMonitoringSection";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../testing/adminOnly";

// The gateway admin role check (useGatewayAdmin): rules and channels are Owner or
// Admin only. The cases below run as an Admin unless one says otherwise.
let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

const mockUpdateMutate = vi.fn();
let currentTab = "rules";

const config = {
  alerting: {
    enabled: true,
    rules: [
      {
        name: "high-errors",
        metric: "error_count",
        condition: ">=",
        threshold: 10,
      },
      {
        name: "high-cost",
        metric: "cost_total",
        condition: ">",
        threshold: 50,
      },
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
    mockOrgLevel = 8;
    mockUpdateMutate.mockClear();
  });

  it.each(NOT_ADMIN_LEVELS)(
    "%s sees every rule and channel write disabled, and reads them",
    async (_who, level) => {
      mockOrgLevel = level;
      currentTab = "rules";
      const { unmount } = render(<AlertingMonitoringSection />);
      expect(screen.getByText("high-errors")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "Create Rule" }));
      expectAdminOnly(screen.getByRole("button", { name: "Add Channel" }));
      expectAdminOnly(
        screen.getByRole("button", { name: "Delete rule high-errors" }),
      );
      unmount();

      currentTab = "channels";
      render(<AlertingMonitoringSection />);
      expect(screen.getByText("oncall")).toBeInTheDocument();
      expectAdminOnly(
        screen.getByRole("button", { name: "Delete channel oncall" }),
      );
      expect(mockUpdateMutate).not.toHaveBeenCalled();
    },
  );

  it.each(ADMIN_LEVELS)("%s can add and delete", (_who, level) => {
    mockOrgLevel = level;
    currentTab = "channels";
    render(<AlertingMonitoringSection />);
    expectEditable(screen.getByRole("button", { name: "Create Rule" }));
    expectEditable(screen.getByRole("button", { name: "Add Channel" }));
    expectEditable(
      screen.getByRole("button", { name: "Delete channel oncall" }),
    );
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

  // A credential URL reads back as the mask; the
  // channel list says it is stored and hidden, never shows the mask.
  it("shows a stored credential URL as hidden, never its mask", () => {
    currentTab = "channels";
    config.alerting.channels.push({
      name: "slack-secret",
      type: "slack",
      url: "__encrypted__0123456789abcdef",
    });
    try {
      const { container } = render(<AlertingMonitoringSection />);
      expect(container.textContent).not.toContain("__encrypted__");
      expect(screen.getByText("Stored, hidden")).toBeInTheDocument();
      expect(screen.getAllByText("https://hooks.example/\u2026")).toHaveLength(
        2,
      );
    } finally {
      config.alerting.channels.pop();
    }
  });
});
