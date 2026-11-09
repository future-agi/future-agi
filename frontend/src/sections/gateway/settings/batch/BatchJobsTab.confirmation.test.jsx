import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render,
  fireEvent,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import BatchJobsTab from "./BatchJobsTab";

const batch = vi.hoisted(() => ({ cancel: vi.fn(), status: "processing" }));
const batchId = "batch-ux330-confirm";
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("../hooks/useBatchJobs", () => ({
  useSubmitBatch: () => ({
    mutate: (_args, { onSuccess }) =>
      onSuccess({ batch_id: "batch-ux330-confirm" }),
    isPending: false,
  }),
  useBatchStatus: () => ({
    data: { status: batch.status, total: 1, summary: {} },
    isLoading: false,
  }),
  useCancelBatch: () => ({ mutate: batch.cancel, isPending: false }),
}));

async function renderTrackedJob() {
  const user = userEvent.setup();
  render(<BatchJobsTab config={{ batch: {} }} gatewayId="gateway-1" />);
  await user.click(screen.getByRole("button", { name: "Submit Batch Job" }));
  await user.click(screen.getByRole("button", { name: "Submit" }));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  return user;
}

const cancelTrigger = () =>
  within(screen.getByRole("table")).getByRole("button", { name: "Cancel" });

beforeEach(() => {
  batch.cancel.mockClear();
  batch.status = "processing";
});

describe("GWUX-02 batch cancellation confirmation", () => {
  it.each(["confirm", "dismiss", "Escape", "backdrop"])(
    "keeps the target text stable throughout %s exit",
    async (action) => {
      const user = await renderTrackedJob();
      await user.click(cancelTrigger());
      const dialog = screen.getByRole("dialog", {
        name: `Cancel batch job ${batchId}?`,
      });
      if (action === "Escape") {
        fireEvent.keyDown(dialog, { key: "Escape", code: "Escape" });
      } else if (action === "backdrop") {
        fireEvent.mouseDown(dialog.parentElement);
        fireEvent.click(dialog.parentElement);
      } else {
        fireEvent.click(
          within(dialog).getByRole("button", {
            name: action === "confirm" ? "Cancel job" : "Keep running",
          }),
        );
      }
      // Inspect synchronously: the real MUI Fade still owns the closing DOM.
      expect(dialog).toBeInTheDocument();
      expect(dialog).toHaveAccessibleName(`Cancel batch job ${batchId}?`);
      expect(dialog).toHaveTextContent(
        `Cancelling batch job ${batchId} cancels queued requests and asks requests in progress to stop. Requests already sent may still finish and incur charges. Cancellation cannot be undone. Cancelling does not delete completed results.`,
      );
      expect(dialog).not.toHaveTextContent(/null|undefined/);
      if (action === "confirm") {
        expect(batch.cancel).toHaveBeenCalledTimes(1);
        expect(batch.cancel).toHaveBeenCalledWith({
          gatewayId: "gateway-1",
          batchId,
        });
      } else {
        expect(batch.cancel).not.toHaveBeenCalled();
      }
      await waitFor(() => expect(dialog).not.toBeInTheDocument());
    },
  );

  it.each(["pending", "processing"])(
    "confirms before cancelling a %s job",
    async (status) => {
      batch.status = status;
      const user = await renderTrackedJob();
      await user.click(cancelTrigger());
      expect(batch.cancel).not.toHaveBeenCalled();
      const dialog = screen.getByRole("dialog", {
        name: `Cancel batch job ${batchId}?`,
      });
      expect(dialog).toHaveTextContent(
        `Cancelling batch job ${batchId} cancels queued requests and asks requests in progress to stop. Requests already sent may still finish and incur charges. Cancellation cannot be undone. Cancelling does not delete completed results.`,
      );
      const confirm = within(dialog).getByRole("button", {
        name: "Cancel job",
      });
      expect(confirm).toHaveClass("MuiButton-containedError");
      expect(
        within(dialog).getByRole("button", { name: "Keep running" }),
      ).toBeInTheDocument();
      await user.click(confirm);
      expect(batch.cancel).toHaveBeenCalledTimes(1);
      expect(batch.cancel).toHaveBeenCalledWith({
        gatewayId: "gateway-1",
        batchId,
      });
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
    },
  );

  it("Keep running dismisses without cancelling and a fresh attempt still asks", async () => {
    const user = await renderTrackedJob();
    await user.click(cancelTrigger());
    expect(batch.cancel).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Keep running" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(batch.cancel).not.toHaveBeenCalled();
    await user.click(cancelTrigger());
    expect(screen.getByRole("dialog")).toHaveTextContent(batchId);
    expect(batch.cancel).not.toHaveBeenCalled();
  });

  it("Escape dismisses without cancelling", async () => {
    const user = await renderTrackedJob();
    await user.click(cancelTrigger());
    expect(batch.cancel).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(batch.cancel).not.toHaveBeenCalled();
  });
});
