import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import AnalyzeTab from "../AnalyzeTab";
import { useErrorFeedStore } from "../../store";

vi.mock("../../useAnalyzeRunner", () => ({
  useFollowUpRunner: () => ({ runFollowUp: vi.fn() }),
}));

const grouped = {
  cluster_id: "S-1A2B3C4D",
  source: "scanner",
  error: { name: "Agent refunds the wrong amount for two-item orders" },
  trace_count: 92,
  description: "The agent refunds the first item only.",
  fix_hypothesis: "Sum every line item before calling refund_order.",
  falsifier: "Two-item refunds stay wrong after the agent reads both items.",
};

const pendingRuns = () =>
  useErrorFeedStore.getState().analyzePendingStartByCluster;

describe("Root cause tab", () => {
  beforeEach(() => {
    useErrorFeedStore.setState({
      analyzeThreadsByCluster: {},
      analyzePendingStartByCluster: {},
    });
  });

  it("opens with the stored analysis and starts no in-depth run", () => {
    render(<AnalyzeTab error={grouped} />);

    expect(screen.getByText("Likely cause")).toBeInTheDocument();
    expect(screen.getByText(grouped.description)).toBeInTheDocument();
    expect(screen.getByText("Suggested change")).toBeInTheDocument();
    expect(screen.getByText(grouped.fix_hypothesis)).toBeInTheDocument();
    expect(screen.getByText("Wrong if")).toBeInTheDocument();
    expect(screen.getByText(grouped.falsifier)).toBeInTheDocument();
    expect(pendingRuns()).toEqual({});
  });

  it("starts the in-depth analysis only from its button", async () => {
    render(<AnalyzeTab error={grouped} />);

    await userEvent.click(
      screen.getByRole("button", { name: "Explain in depth" }),
    );

    expect(pendingRuns()).toEqual({ [grouped.cluster_id]: true });
  });

  it("shows an eval issue's explanation and says why nothing else is stored", () => {
    render(
      <AnalyzeTab
        error={{
          ...grouped,
          cluster_id: "E-9C01E76F",
          source: "eval",
          description: "The reply promised a refund outside the policy.",
          fix_hypothesis: null,
          falsifier: null,
        }}
      />,
    );

    expect(screen.getByText("Eval explanation")).toBeInTheDocument();
    expect(
      screen.getByText("The reply promised a refund outside the policy."),
    ).toBeInTheDocument();
    expect(screen.getByText(/grouped from eval results/)).toBeInTheDocument();
    expect(screen.queryByText("Suggested change")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Explain in depth" }),
    ).toBeInTheDocument();
  });

  it("says an older scan issue was grouped before the analysis was stored", () => {
    render(
      <AnalyzeTab
        error={{
          ...grouped,
          cluster_id: "FT-0001",
          description: null,
          fix_hypothesis: null,
          falsifier: null,
        }}
      />,
    );

    expect(
      screen.getByText(/older version of the Error Feed/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Likely cause")).not.toBeInTheDocument();
  });
});
