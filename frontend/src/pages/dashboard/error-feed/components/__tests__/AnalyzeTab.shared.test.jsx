import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import AnalyzeTab from "../AnalyzeTab";
import { SharedIssueContext } from "../../sharedIssueContext";
import { useErrorFeedStore } from "../../store";
import { hydrateFromCache } from "../../clusterAnalyzeSocket";

vi.mock("../../useAnalyzeRunner", () => ({
  useFollowUpRunner: () => ({ runFollowUp: vi.fn() }),
}));

const shared = { token: "tok" };
const error = { cluster_id: "E-1", project_id: "p-1" };

const renderShared = () =>
  render(
    <SharedIssueContext.Provider value={shared}>
      <AnalyzeTab error={error} />
    </SharedIssueContext.Provider>,
  );

describe("AnalyzeTab on a shared issue", () => {
  beforeEach(() => {
    useErrorFeedStore.setState({ analyzeThreadsByCluster: {} });
  });

  it("shows the saved analysis as the product does, with no run or follow-up", () => {
    hydrateFromCache({
      clusterId: "E-1",
      rca: {
        synthesis: "The prompt says refunds are usually allowed.",
        fix: "State the 30 day rule.",
        confidence: "H",
      },
    });

    renderShared();

    expect(
      screen.getByText("The prompt says refunds are usually allowed."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Showing the last saved analysis"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /re-run/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("says no analysis was saved and offers no run", () => {
    renderShared();

    expect(
      screen.getByText("No analysis saved for this issue."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
