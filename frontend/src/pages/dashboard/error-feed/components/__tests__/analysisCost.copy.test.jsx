import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ClusterHeadlineCard from "../ClusterHeadlineCard";
import AnalyzeTab from "../AnalyzeTab";
import { useErrorFeedStore } from "../../store";

vi.mock("../../useAnalyzeRunner", () => ({
  useFollowUpRunner: () => ({ runFollowUp: vi.fn() }),
}));

// W10-ERROR-FEED-U02: a cluster analysis is billed from the run's model cost
// (credits = cost x markup / $0.01). The buttons that start the first run say
// that it uses AI credits, and no control quotes a fixed price. The re-run
// tooltips' own wording is pinned in ErrorFeedRerunPricing.test.jsx.
const FIXED_PRICE = /\b1 credits?\b/i;
const COST_NOTE = /^uses AI credits based on usage$/i;

const cluster = { cluster_id: "E-1", project_id: "p-1", fix_layer: "Prompt" };

describe("Cluster analysis cost copy (W10-ERROR-FEED-U02)", () => {
  beforeEach(() => {
    useErrorFeedStore.setState({
      analyzeThreadsByCluster: {},
      analyzePendingStartByCluster: {},
      clusterAnalysisCollapsed: false,
    });
  });

  it("Overview: 'Debug this cluster' says the run uses AI credits", () => {
    render(<ClusterHeadlineCard error={cluster} />);

    const run = screen.getByRole("button", { name: /debug this cluster/i });
    expect(screen.getByText(COST_NOTE)).toBeInTheDocument();
    expect(run).toHaveAccessibleDescription(COST_NOTE);
    expect(screen.queryByLabelText(FIXED_PRICE)).not.toBeInTheDocument();
  });

  it("Fix tab: 'Analyze this cluster' says the run uses AI credits", () => {
    render(<AnalyzeTab error={cluster} />);

    const run = screen.getByRole("button", { name: /analyze this cluster/i });
    expect(screen.getByText(COST_NOTE)).toBeInTheDocument();
    expect(run).toHaveAccessibleDescription(COST_NOTE);
    expect(screen.queryByLabelText(FIXED_PRICE)).not.toBeInTheDocument();
  });
});
