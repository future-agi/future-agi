import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import { paths } from "src/routes/paths";
import AnalyzeTab from "../AnalyzeTab";
import { useErrorFeedStore } from "../../store";
import { MESSAGE_TYPE, RUN_STATE } from "../../constants";

vi.mock("../../useAnalyzeRunner", () => ({
  useFollowUpRunner: () => ({ runFollowUp: vi.fn() }),
}));

const error = {
  cluster_id: "E-1",
  project_id: "p-1",
  error: { name: "Refund policy drift" },
  trace_count: 12,
};

// A finished run whose only outcome is a failure, shaped exactly as
// clusterAnalyzeSocket pushes it (SYNTHESIS, LOW confidence, category error).
const seedFailedRun = (failure) =>
  useErrorFeedStore.setState({
    analyzeThreadsByCluster: {
      "E-1": {
        runState: RUN_STATE.DONE,
        startedAt: Date.now(),
        conversationId: "conv-1",
        messages: [
          {
            id: "err-1",
            type: MESSAGE_TYPE.SYNTHESIS,
            fix: "",
            confidence: "L",
            category: "error",
            ...failure,
          },
        ],
      },
    },
    analyzePendingStartByCluster: {},
  });

describe("AnalyzeTab when the analysis run fails", () => {
  beforeEach(() => {
    useErrorFeedStore.setState({
      analyzeThreadsByCluster: {},
      analyzePendingStartByCluster: {},
    });
  });

  it("shows a paused budget as an error with a way to manage the budget, not as a low-confidence result", async () => {
    seedFailedRun({
      headline: "Usage paused — you set a budget limit for falcon_ai",
      errorCode: "BUDGET_PAUSED",
    });

    render(<AnalyzeTab error={error} />);

    const alert = screen.getByRole("alert");
    expect(
      within(alert).getByText("Analysis didn't finish"),
    ).toBeInTheDocument();
    expect(
      within(alert).getByText(
        "Usage paused — you set a budget limit for falcon_ai",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Synthesis")).not.toBeInTheDocument();
    expect(screen.queryByText("Low confidence")).not.toBeInTheDocument();

    expect(
      within(alert).getByRole("link", { name: "Manage budget" }),
    ).toHaveAttribute("href", paths.dashboard.settings.billing);

    const rerun = within(alert).getByRole("button", { name: "Re-run" });
    expect(rerun).toBeEnabled();
    await userEvent.click(rerun);
    // The shared runner picks the run up from the pending-start flag (it
    // holds the request guard captured at click time).
    expect(
      useErrorFeedStore.getState().analyzePendingStartByCluster["E-1"],
    ).toEqual(expect.any(Function));
  });

  it("shows a dropped connection as an error with no budget link", () => {
    seedFailedRun({
      headline:
        "Couldn't reach the investigator — the connection dropped. Hit Re-run.",
    });

    render(<AnalyzeTab error={error} />);

    const alert = screen.getByRole("alert");
    expect(
      within(alert).getByText("Analysis didn't finish"),
    ).toBeInTheDocument();
    expect(
      within(alert).getByText(
        "Couldn't reach the investigator — the connection dropped. Hit Re-run.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Synthesis")).not.toBeInTheDocument();
    expect(screen.queryByText("Low confidence")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Manage budget" }),
    ).not.toBeInTheDocument();
    expect(within(alert).getByRole("button", { name: "Re-run" })).toBeEnabled();
  });

  it("still shows a real low-confidence result as the synthesis", () => {
    seedFailedRun({
      headline: "The prompt says refunds are usually allowed.",
      category: "low confidence",
    });

    render(<AnalyzeTab error={error} />);

    expect(screen.getByText("Synthesis")).toBeInTheDocument();
    expect(screen.getByText("Low confidence")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
