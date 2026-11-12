import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import BatchJobsTab from "./BatchJobsTab";
import { normalizeGateway } from "../../context/GatewayContext";

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../hooks/useBatchJobs", () => ({
  useSubmitBatch: () => ({ mutate: vi.fn(), isPending: false }),
  useBatchStatus: () => ({ data: undefined, isLoading: false }),
  useCancelBatch: () => ({ mutate: vi.fn(), isPending: false }),
}));

const INTRO =
  "A batch job runs a list of chat completion requests through the gateway in one submission, as your organization. Follow each job here and read or export its results.";
const OFF = "Batch submission is not available in this release.";

// Settings > Batch jobs says what a batch job is, and, when this deployment
// has batch submission off, says so up front in the release's own words:
// Submit stays on screen, disabled, and an existing job can still be opened.
describe("Batch jobs: what they are, and when submission is off", () => {
  it("says what a batch job is", () => {
    render(<BatchJobsTab config={{ batch: {} }} gatewayId="gateway-1" />);
    expect(screen.getByText(INTRO)).toBeInTheDocument();
    expect(screen.queryByText(OFF)).toBeNull();
    expect(
      screen.getByRole("button", { name: "Submit Batch Job" }),
    ).toBeEnabled();
  });

  it("says submission is not available in this release when it is off", async () => {
    const user = userEvent.setup();
    render(
      <BatchJobsTab
        config={{ batch: {} }}
        gatewayId="gateway-1"
        submissionAvailable={false}
      />,
    );
    expect(screen.getByText(INTRO)).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "Submit Batch Job" });
    expect(submit).toBeDisabled();
    expect(submit).toHaveAccessibleDescription(OFF);
    expect(
      screen.getByText(
        "No batch jobs submitted in this view. Batch submission is not available in this release; you can still open a job by its ID.",
      ),
    ).toBeInTheDocument();
    await user.type(
      screen.getByRole("textbox", { name: "Batch job ID" }),
      "batch-earlier",
    );
    expect(screen.getByRole("button", { name: "Open job" })).toBeEnabled();
  });

  it("reads the gateway's delegated_batch_available; only false turns it off", () => {
    expect(
      normalizeGateway({ delegated_batch_available: false })
        .delegatedBatchAvailable,
    ).toBe(false);
    expect(
      normalizeGateway({ delegated_batch_available: true })
        .delegatedBatchAvailable,
    ).toBe(true);
    expect(normalizeGateway({}).delegatedBatchAvailable).toBeUndefined();
  });
});
