import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";
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

vi.mock("ag-grid-react", () => import("../logs/__tests__/fakeAgGrid"));

const mockUpdateMutate = vi.fn();
let currentTab = "rules";

let config = {
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

// The page sets its title through react-helmet-async.
const renderPage = () =>
  render(
    <HelmetProvider>
      <AlertingMonitoringSection />
    </HelmetProvider>,
  );

describe("AlertingMonitoringSection", () => {
  beforeEach(() => {
    mockOrgLevel = 8;
    mockUpdateMutate.mockClear();
  });

  // Each Alerts tab has its own add button: Create rule on Rules, Add
  // channel on Channels. Every write on both tabs is checked.
  it.each(NOT_ADMIN_LEVELS)(
    "%s sees every rule and channel write disabled, and reads them",
    async (_who, level) => {
      mockOrgLevel = level;
      currentTab = "rules";
      const { unmount } = renderPage();
      expect(screen.getByText("high-errors")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "Create rule" }));
      expectAdminOnly(
        screen.getByRole("button", { name: "Delete rule high-errors" }),
      );
      expectAdminOnly(
        screen.getByRole("button", { name: "Delete rule high-cost" }),
      );
      unmount();

      currentTab = "channels";
      renderPage();
      expect(screen.getByText("oncall")).toBeInTheDocument();
      expectAdminOnly(screen.getByRole("button", { name: "Add channel" }));
      expectAdminOnly(
        screen.getByRole("button", { name: "Delete channel oncall" }),
      );
      expect(mockUpdateMutate).not.toHaveBeenCalled();
    },
  );

  it.each(ADMIN_LEVELS)("%s can add and delete", (_who, level) => {
    mockOrgLevel = level;
    currentTab = "rules";
    const { unmount } = renderPage();
    expectEditable(screen.getByRole("button", { name: "Create rule" }));
    expectEditable(
      screen.getByRole("button", { name: "Delete rule high-errors" }),
    );
    unmount();

    currentTab = "channels";
    renderPage();
    expectEditable(screen.getByRole("button", { name: "Add channel" }));
    expectEditable(
      screen.getByRole("button", { name: "Delete channel oncall" }),
    );
  });

  it("deletes a rule by saving the list without it", async () => {
    currentTab = "rules";
    const user = userEvent.setup();
    render(
      <HelmetProvider>
        <AlertingMonitoringSection />
      </HelmetProvider>,
    );

    await user.click(
      screen.getByRole("button", { name: "Delete rule high-errors" }),
    );
    // Asks first, naming the rule.
    expect(mockUpdateMutate).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Delete rule high-errors?");
    await user.click(
      within(dialog).getByRole("button", { name: "Delete rule" }),
    );

    expect(mockUpdateMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      config: { alerting: { rules: [config.alerting.rules[1]] } },
    });
  });

  it("deletes a channel by saving the list without it", async () => {
    currentTab = "channels";
    const user = userEvent.setup();
    render(
      <HelmetProvider>
        <AlertingMonitoringSection />
      </HelmetProvider>,
    );

    await user.click(
      screen.getByRole("button", { name: "Delete channel oncall" }),
    );
    // Asks first, naming the channel.
    expect(mockUpdateMutate).not.toHaveBeenCalled();
    let dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Delete channel oncall?");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(mockUpdateMutate).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "Delete channel oncall" }),
    );
    dialog = screen.getByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Delete channel" }),
    );

    expect(mockUpdateMutate.mock.calls[0][0]).toEqual({
      gatewayId: "default",
      config: { alerting: { channels: [config.alerting.channels[0]] } },
    });
  });

  it.each([
    ["rules", "rule", "high-errors"],
    ["channels", "channel", "oncall"],
  ])(
    "rechecks identity and permission for %s confirmations",
    async (tab, kind, name) => {
      currentTab = tab;
      const user = userEvent.setup();
      const view = renderPage();
      const open = async () => {
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
        await user.click(
          screen.getByRole("button", { name: `Delete ${kind} ${name}` }),
        );
      };
      const confirm = () =>
        user.click(
          within(screen.getByRole("dialog")).getByRole("button", {
            name: `Delete ${kind}`,
          }),
        );
      const rerender = () =>
        view.rerender(
          <HelmetProvider>
            <AlertingMonitoringSection />
          </HelmetProvider>,
        );
      await open();
      mockOrgLevel = 3;
      rerender();
      await confirm();
      expect(mockUpdateMutate).not.toHaveBeenCalled();
      await user.click(
        within(screen.getByRole("dialog")).getByRole("button", {
          name: "Cancel",
        }),
      );
      mockOrgLevel = 8;
      rerender();
      await open();
      const original = config.alerting[tab];
      try {
        config = {
          ...config,
          alerting: {
            ...config.alerting,
            [tab]: original.map((row) => ({ ...row })),
          },
        };
        rerender();
        await confirm();
        expect(mockUpdateMutate).not.toHaveBeenCalled();
        // A fresh confirmation survives a reorder of those same objects.
        await open();
        const target = config.alerting[tab].find((row) => row.name === name);
        config = {
          ...config,
          alerting: {
            ...config.alerting,
            [tab]: [...config.alerting[tab]].reverse(),
          },
        };
        rerender();
        await confirm();
        expect(mockUpdateMutate).toHaveBeenCalledTimes(1);
        expect(mockUpdateMutate.mock.calls[0][0].config.alerting[tab]).toEqual(
          config.alerting[tab].filter((row) => row !== target),
        );
      } finally {
        config.alerting[tab] = original;
      }
    },
  );

  // A credential URL reads back as the mask; the channel list says it is
  // stored and hidden, never shows the mask.
  it("shows a stored credential URL as hidden, never its mask", () => {
    currentTab = "channels";
    config.alerting.channels.push({
      name: "slack-secret",
      type: "slack",
      url: "__encrypted__0123456789abcdef",
    });
    try {
      const { container } = renderPage();
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
