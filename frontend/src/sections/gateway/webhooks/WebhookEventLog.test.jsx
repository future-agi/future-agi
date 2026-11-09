import React, { forwardRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "src/utils/test-utils";
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
    return <div data-testid="ag-grid" />;
  }),
}));

const mockUseWebhookEvents = vi.hoisted(() => vi.fn());
vi.mock("./hooks/useWebhooks", () => ({
  useWebhookEvents: mockUseWebhookEvents,
  useRetryWebhookEvent: () => ({ mutate: vi.fn(), isPending: false }),
}));

describe("WebhookEventLog", () => {
  it("filters by webhook_id and shows attempts as a count, not as scheduled retries", () => {
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
  });
});
