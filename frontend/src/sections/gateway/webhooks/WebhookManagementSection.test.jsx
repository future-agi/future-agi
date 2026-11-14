import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import { enqueueSnackbar } from "notistack";
import WebhookManagementSection from "./WebhookManagementSection";
import { runStatusOf } from "src/utils/runStatus";

const { mockWebhooks, mockUpdate, mockDelete } = vi.hoisted(() => ({
  mockWebhooks: vi.fn(),
  mockUpdate: vi.fn(),
  mockDelete: vi.fn(),
}));

vi.mock("./hooks/useWebhooks", () => ({
  useWebhooks: () => ({ data: mockWebhooks(), isLoading: false, error: null }),
  useCreateWebhook: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateWebhook: () => ({ mutate: mockUpdate, isPending: false }),
  useDeleteWebhook: () => ({ mutate: mockDelete, isPending: false }),
  useTestWebhook: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1" }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const webhook = {
  id: "webhook-1",
  name: "Test endpoint",
  url: "https://example.com/hook",
  created_at: "2026-11-10T00:00:00Z",
  events: ["request.completed"],
  is_active: false,
  disabled_reason: "Disabled after 3 consecutive failed deliveries",
  disabled_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
};

const row = () => screen.getByText("Test endpoint").closest("tr");

describe("WebhookManagementSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockWebhooks.mockReturnValue([webhook]);
  });

  it("shows a danger Disabled badge, reason and relative disable time", () => {
    render(<WebhookManagementSection />);
    const statusCell = within(row()).getByText("Disabled").closest("td");
    expect(statusCell).toHaveTextContent(webhook.disabled_reason);
    expect(statusCell).toHaveTextContent(/hour ago/);
    // The danger role is the webhook row's own; a "disabled" status elsewhere
    // (a provider's health, for one) keeps its neutral look.
    expect(runStatusOf("disabled").role).toBe("neutral");
  });

  it("enables an auto-disabled endpoint and confirms success", async () => {
    render(<WebhookManagementSection />);
    await userEvent.click(
      within(row()).getByRole("button", { name: "Webhook actions" }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: "Enable" }));
    expect(mockUpdate).toHaveBeenCalledWith(
      { id: webhook.id, is_active: true },
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );
    mockUpdate.mock.calls[0][1].onSuccess();
    expect(enqueueSnackbar).toHaveBeenCalledWith("Webhook enabled", {
      variant: "success",
    });
  });

  it("disables an active endpoint and reports an error", async () => {
    mockWebhooks.mockReturnValue([
      { ...webhook, is_active: true, disabled_reason: "", disabled_at: null },
    ]);
    render(<WebhookManagementSection />);
    expect(within(row()).getByText("Active")).toBeInTheDocument();
    await userEvent.click(
      within(row()).getByRole("button", { name: "Webhook actions" }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: "Disable" }));
    expect(mockUpdate).toHaveBeenCalledWith(
      { id: webhook.id, is_active: false },
      expect.any(Object),
    );
    mockUpdate.mock.calls[0][1].onError(new Error("Cannot disable"));
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      expect.stringContaining("Cannot disable"),
      { variant: "error" },
    );
  });

  it("shows Inactive without an automatic disable reason", () => {
    mockWebhooks.mockReturnValue([
      { ...webhook, disabled_reason: "", disabled_at: null },
    ]);
    render(<WebhookManagementSection />);
    expect(within(row()).getByText("Inactive")).toBeInTheDocument();
    expect(within(row()).queryByText("Disabled")).not.toBeInTheDocument();
  });

  it("shows a formatted dropped-event count and relative time under an active status", () => {
    mockWebhooks.mockReturnValue([
      {
        ...webhook,
        is_active: true,
        dropped_events_total: 1234,
        last_dropped_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
      },
    ]);
    render(<WebhookManagementSection />);
    const statusCell = within(row()).getByText("Active").closest("td");
    const caption = within(statusCell).getByText(
      `${(1234).toLocaleString()} events dropped · 5 minutes ago`,
    );
    expect(caption).toHaveAttribute(
      "title",
      "Events dropped because this webhook reached its pending-event limit or they waited longer than the maximum age",
    );
  });

  it("does not show a dropped-event caption for a count of zero", () => {
    mockWebhooks.mockReturnValue([{ ...webhook, dropped_events_total: 0 }]);
    render(<WebhookManagementSection />);
    expect(
      within(row()).queryByText(/events? dropped/),
    ).not.toBeInTheDocument();
  });

  it("uses the singular dropped-event label and omits time when unavailable", () => {
    mockWebhooks.mockReturnValue([
      { ...webhook, dropped_events_total: 1, last_dropped_at: null },
    ]);
    render(<WebhookManagementSection />);
    expect(within(row()).getByText("1 event dropped")).toBeInTheDocument();
  });

  it("shows consecutive failed deliveries only for an active webhook", () => {
    mockWebhooks.mockReturnValue([
      { ...webhook, is_active: true, consecutive_failures: 3 },
    ]);
    render(<WebhookManagementSection />);
    const statusCell = within(row()).getByText("Active").closest("td");
    expect(
      within(statusCell).getByText("3 failed deliveries in a row"),
    ).toBeInTheDocument();
  });

  it("does not show consecutive failures for zero or for a disabled webhook", () => {
    mockWebhooks.mockReturnValue([
      { ...webhook, is_active: true, consecutive_failures: 0 },
      {
        ...webhook,
        id: "webhook-2",
        name: "Disabled endpoint",
        consecutive_failures: 3,
      },
    ]);
    render(<WebhookManagementSection />);
    expect(
      screen.queryByText(/failed deliver(?:y|ies) in a row/),
    ).not.toBeInTheDocument();
    const disabledRow = screen.getByText("Disabled endpoint").closest("tr");
    expect(within(disabledRow).getByText("Disabled")).toBeInTheDocument();
    expect(disabledRow).toHaveTextContent(webhook.disabled_reason);
  });

  it("uses the singular consecutive-failure label", () => {
    mockWebhooks.mockReturnValue([
      { ...webhook, is_active: true, consecutive_failures: 1 },
    ]);
    render(<WebhookManagementSection />);
    expect(
      within(row()).getByText("1 failed delivery in a row"),
    ).toBeInTheDocument();
  });
});
