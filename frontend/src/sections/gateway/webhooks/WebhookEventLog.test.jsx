import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import WebhookEventLog from "./WebhookEventLog";

const mockUseWebhookEvents = vi.hoisted(() => vi.fn());
const mockRetryMutate = vi.hoisted(() => vi.fn());

vi.mock("./hooks/useWebhooks", () => ({
  useWebhookEvents: mockUseWebhookEvents,
  useRetryWebhookEvent: () => ({
    mutate: mockRetryMutate,
    isPending: false,
  }),
}));

describe("WebhookEventLog", () => {
  it("filters by webhook_id and renders canonical snake_case event fields", async () => {
    mockUseWebhookEvents.mockReturnValue({
      data: [
        {
          id: "event-1",
          event_type: "error.occurred",
          status: "failed",
          attempts: 2,
          max_attempts: 5,
          last_response_code: 503,
          last_attempt_at: "2026-05-25T10:15:00Z",
          last_error: "temporary delivery failure",
        },
      ],
      isLoading: false,
      error: null,
    });

    render(<WebhookEventLog webhookId="webhook-1" />);

    expect(mockUseWebhookEvents).toHaveBeenCalledWith({
      webhook_id: "webhook-1",
    });

    const table = within(screen.getByRole("table"));
    expect(table.getByText("error.occurred")).toBeInTheDocument();
    expect(table.getByText("failed")).toBeInTheDocument();
    expect(table.getByText("2/5")).toBeInTheDocument();
    expect(table.getByText("503")).toBeInTheDocument();
    expect(table.getByText("temporary delivery failure")).toBeInTheDocument();

    await userEvent.click(table.getByRole("button", { name: /retry/i }));
    expect(mockRetryMutate).toHaveBeenCalledWith(
      "event-1",
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );
  });
});
