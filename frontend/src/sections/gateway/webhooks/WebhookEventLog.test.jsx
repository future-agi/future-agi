import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within, fireEvent } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { MockAgGrid } from "../settings/testing/agGridMock";
import WebhookEventLog from "./WebhookEventLog";

// These cases act as an Admin: declaring, editing, deleting and test-sending
// are Owner or Admin writes (useGatewayAdmin); ownerOrAdminWrites.test.jsx
// covers the other roles.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <MockAgGrid {...props} />;
  }),
}));

const mockUseWebhookEvents = vi.hoisted(() => vi.fn());
const mockRetryMutate = vi.hoisted(() => vi.fn());
vi.mock("./hooks/useWebhooks", () => ({
  useWebhookEvents: mockUseWebhookEvents,
  useRetryWebhookEvent: () => ({ mutate: mockRetryMutate, isPending: false }),
}));

describe("WebhookEventLog", () => {
  beforeEach(() => {
    mockUseWebhookEvents.mockReset();
    mockRetryMutate.mockClear();
    grid.props = null;
  });

  it("keeps newest-100 scope and endpoint filtering for recorded tests without retrying them on view", () => {
    const rows = Array.from({ length: 100 }, (_, index) => ({
      id: `test-${index}`,
      webhook: "webhook-1",
      webhook_name: "Ops",
      event_type: "test",
      status: "failed",
      attempts: 1,
      max_attempts: 5,
      last_response_code: 500,
    }));
    mockUseWebhookEvents.mockReturnValue({ data: rows, isLoading: false });
    render(<WebhookEventLog webhookId="webhook-1" webhooks={[{ id: "webhook-1", name: "Ops" }]} />);
    expect(mockUseWebhookEvents).toHaveBeenCalledWith({ webhook_id: "webhook-1" });
    expect(screen.getByText("Newest 100 matching deliveries")).toBeVisible();
    expect(grid.props.rowData).toEqual(rows);
    expect(mockRetryMutate).not.toHaveBeenCalled();
  });

  it("R3-CONTROLS-11 filters the server query before its newest-100 cap", async () => {
    const failedAttempt = { id: "event-older", status: "pending", attempts: 1, last_error: "Receiver unavailable", event_type: "budget.exceeded" };
    mockUseWebhookEvents.mockImplementation((params) => ({ data: params?.status === "pending" ? [failedAttempt] : [], isLoading: false }));
    render(<WebhookEventLog webhookId="webhook-1" webhooks={[{ id: "webhook-1", name: "Ops" }]} />);
    await userEvent.click(screen.getByLabelText("Status"));
    await userEvent.click(screen.getByRole("option", { name: "Pending (queued or awaiting retry)" }));
    await userEvent.click(screen.getByLabelText("Event type"));
    await userEvent.click(screen.getByRole("option", { name: "Budget exceeded" }));
    expect(mockUseWebhookEvents).toHaveBeenLastCalledWith({ webhook_id: "webhook-1", status: "pending", event_type: "budget.exceeded" });
    expect(grid.props.rowData).toEqual([failedAttempt]);
    expect(screen.getByText("Failed, will retry")).toBeInTheDocument();
    expect(mockRetryMutate).not.toHaveBeenCalled();
  });

  it("R3-CONTROLS-09 refreshes an open delivery and distinguishes one outside the loaded subset", () => {
    const event = { id: "event-1", event_type: "test", status: "pending", attempts: 0 };
    mockUseWebhookEvents.mockReturnValue({ data: [event], isLoading: false });
    const view = render(<WebhookEventLog />);
    fireEvent.click(screen.getByRole("row"));
    expect(within(screen.getByRole("dialog")).getByText("Queued")).toBeInTheDocument();
    mockUseWebhookEvents.mockReturnValue({ data: [{ ...event, status: "delivered", attempts: 1, last_response_code: 204 }], isLoading: false });
    view.rerender(<WebhookEventLog />);
    const drawer = within(screen.getByRole("dialog"));
    expect(drawer.getByText("Delivered")).toBeInTheDocument();
    expect(drawer.getByText("204")).toBeInTheDocument();
    expect(drawer.queryByText("Queued")).not.toBeInTheDocument();
    mockUseWebhookEvents.mockReturnValue({ data: [], isLoading: false });
    view.rerender(<WebhookEventLog />);
    expect(within(screen.getByRole("dialog")).getByText(/no longer in the loaded deliveries/i)).toBeInTheDocument();
    expect(within(screen.getByRole("dialog")).queryByText("Delivered")).not.toBeInTheDocument();
  });

  it("explains that a rejected test may not appear without making up an event", () => {
    mockUseWebhookEvents.mockReturnValue({ data: [], isLoading: false });
    render(<WebhookEventLog />);
    expect(screen.getByText("No deliveries yet")).toBeVisible();
    expect(screen.getByText(/Tests rejected before sending may not appear/)).toBeVisible();
    expect(grid.props?.rowData || []).toEqual([]);
    expect(mockRetryMutate).not.toHaveBeenCalled();
  });

  it("filters by webhook_id and shows attempts as a count, not as scheduled retries", async () => {
    // R-LIT: visibly synthetic delivery row; preserve literal fixtures.
    /** @type {{ id: string, webhook: string, event_type: string, status: string, attempts: number, max_attempts: number, last_response_code: number, last_attempt_at: string, last_error: string }} */
    const event = {
      id: "event-1",
      webhook: "webhook-1",
      event_type: "error.occurred",
      status: "failed",
      attempts: 2,
      max_attempts: 5,
      last_response_code: 503,
      last_attempt_at: "2026-05-25T10:15:00Z",
      last_error: "temporary delivery failure",
    };
    mockUseWebhookEvents.mockReturnValue({
      data: [event],
      isLoading: false,
      error: null,
    });

    render(<WebhookEventLog webhookId="webhook-1" />);

    expect(mockUseWebhookEvents).toHaveBeenCalledWith({
      webhook_id: "webhook-1",
    });
    expect(grid.props.rowData).toEqual([event]);
    const attempts = grid.props.columnDefs.find((c) => c.field === "attempts");
    expect(attempts.valueFormatter).toBeUndefined();
    const status = grid.props.columnDefs.find((c) => c.field === "status");
    expect(status.valueGetter({ data: event })).toBe("Failed");

    // Dev delivery/response/error and retry assertions run through the candidate grid.
    const row = screen.getByRole("row");
    const cells = within(row);
    expect(cells.getByText("Error occurred")).toBeInTheDocument();
    expect(cells.getByText("Failed")).toBeInTheDocument();
    expect(cells.getByText("2")).toBeInTheDocument();
    expect(cells.queryByText("2/5")).not.toBeInTheDocument();
    expect(cells.getByText("503")).toBeInTheDocument();
    expect(cells.getByText("temporary delivery failure")).toBeInTheDocument();
    fireEvent.click(row);
    const drawer = screen.getByRole("dialog");
    expect(within(drawer).getByText("error.occurred")).toBeInTheDocument();
    expect(mockRetryMutate).not.toHaveBeenCalled();
    await userEvent.click(
      within(drawer).getByRole("button", { name: "Retry now" }),
    );
    expect(mockRetryMutate).toHaveBeenCalledTimes(1);
    expect(mockRetryMutate).toHaveBeenCalledWith(
      "event-1",
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );
  });
});
