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
    expect(runStatusOf("disabled").role).toBe("danger");
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
});
