import React from "react";
import { describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import BatchJobsTab from "./BatchJobsTab";

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../hooks/useBatchJobs", () => ({
  useSubmitBatch: () => ({
    mutate: (_args, { onSuccess }) => onSuccess({ batch_id: "batch-ux330" }),
    isPending: false,
  }),
  useBatchStatus: () => ({
    data: { status: "processing", total: 1, summary: {} },
    isLoading: false,
  }),
  useCancelBatch: () => ({ mutate: vi.fn(), isPending: false }),
}));

describe("GWUX-04 batch details accessible name", () => {
  it("names the icon-only close action and preserves the already named row actions", async () => {
    const user = userEvent.setup();
    render(<BatchJobsTab config={{ batch: {} }} gatewayId="gateway-1" />);
    await user.click(screen.getByRole("button", { name: "Submit Batch Job" }));
    await user.click(screen.getByRole("button", { name: "Submit" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    const table = screen.getByRole("table");
    expect(
      within(table).getByRole("button", { name: "Cancel" }),
    ).toHaveAccessibleName("Cancel");
    await user.click(
      within(table).getByRole("button", { name: "View Details" }),
    );
    const close = within(screen.getByRole("dialog")).getByRole("button", {
      name: "Close batch details",
    });
    await user.click(close);
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });
});
