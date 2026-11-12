import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import AnalyzeTab from "../AnalyzeTab";
import ClusterHeadlineCard from "../ClusterHeadlineCard";
import { useErrorFeedStore } from "../../store";

vi.mock("../../useAnalyzeRunner", () => ({
  useFollowUpRunner: () => ({ runFollowUp: vi.fn() }),
}));

const error = {
  cluster_id: "rerun-pricing-cluster",
  project_id: "project-1",
  error: { name: "Repeated tool errors" },
  trace_count: 5,
  fix_layer: "Prompt",
  rca: {
    synthesis: "The prompt omits the required tool arguments.",
    fix: "Describe the required tool arguments.",
    confidence: "H",
  },
};

describe("Error Feed rerun pricing", () => {
  beforeEach(() => {
    useErrorFeedStore.setState({
      analyzeThreadsByCluster: {},
      analyzePendingStartByCluster: {},
      clusterAnalysisCollapsed: false,
    });
  });

  it("discloses usage-based credits in the Fix tab rerun tooltip", async () => {
    const user = userEvent.setup();
    render(<AnalyzeTab error={error} />);

    await user.hover(screen.getByRole("button", { name: "Re-run" }));

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent(
      "Re-run with current cluster state (credits based on usage)",
    );
    expect(tooltip).not.toHaveTextContent(/\b1 credit\b/i);
  });

  it("discloses usage-based credits in the Overview rerun tooltip", async () => {
    const user = userEvent.setup();
    render(<ClusterHeadlineCard error={error} />);

    await user.hover(screen.getByLabelText(/^Re-run analysis/));

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent(
      "Re-run analysis (credits based on usage)",
    );
    expect(tooltip).not.toHaveTextContent(/\b1 credit\b/i);
  });
});
