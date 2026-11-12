import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ClusterHeadlineCard from "../ClusterHeadlineCard";
import { useErrorFeedStore } from "../../store";
import { hydrateFromCache } from "../../clusterAnalyzeSocket";
import { MESSAGE_TYPE, RUN_STATE, TRAIL_FRAME } from "../../constants";

// The page replays a saved analysis into the live thread on every load. The
// card must still date it from when it ran, so a days-old root cause never
// reads as a fresh one right above "Create Linear issue".
const analyzedAt = new Date(2026, 2, 3, 14, 5).toISOString();
const rca = {
  synthesis: "The prompt says refunds are usually allowed.",
  fix: "State the 30 day rule.",
  confidence: "H",
  analyzed_at: analyzedAt,
};
const error = { cluster_id: "E-1", fix_layer: "Prompt", rca };
const savedLabel = "Analyzed Mar 3, 14:05 (3 days ago)";

describe("ClusterHeadlineCard with a saved analysis", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 2, 6, 14, 5));
    useErrorFeedStore.setState({
      analyzeThreadsByCluster: {},
      analyzePendingStartByCluster: {},
      clusterAnalysisCollapsed: false,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("dates a saved analysis from when it ran after the page loads it", () => {
    hydrateFromCache({ clusterId: "E-1", rca });

    render(<ClusterHeadlineCard error={error} />);

    expect(
      screen.getByText("The prompt says refunds are usually allowed."),
    ).toBeInTheDocument();
    expect(screen.getByText(savedLabel)).toBeInTheDocument();
    expect(screen.queryByText("Analyzed just now")).not.toBeInTheDocument();
  });

  it("dates a saved analysis replayed with its reasoning trail", () => {
    hydrateFromCache({
      clusterId: "E-1",
      rca: {
        ...rca,
        trace: [
          {
            type: TRAIL_FRAME.SYNTHESIS,
            synthesis: rca.synthesis,
            fix: rca.fix,
            confidence: rca.confidence,
          },
        ],
      },
    });

    render(<ClusterHeadlineCard error={error} />);

    expect(screen.getByText(savedLabel)).toBeInTheDocument();
    expect(screen.queryByText("Analyzed just now")).not.toBeInTheDocument();
  });

  it("keeps the saved time when a re-run fails", () => {
    hydrateFromCache({ clusterId: "E-1", rca });
    const store = useErrorFeedStore.getState();
    const thread = store.analyzeThreadsByCluster["E-1"];
    store.setAnalyzeThread("E-1", {
      ...thread,
      runState: RUN_STATE.DONE,
      messages: [
        ...thread.messages,
        {
          id: "err-1",
          type: MESSAGE_TYPE.SYNTHESIS,
          headline: "Couldn't start the analysis. Please try again.",
          fix: "",
          confidence: "L",
          category: "error",
        },
      ],
    });

    render(<ClusterHeadlineCard error={error} />);

    expect(
      screen.getByText("The prompt says refunds are usually allowed."),
    ).toBeInTheDocument();
    expect(screen.getByText(savedLabel)).toBeInTheDocument();
  });

  it("says just now for a run that finished in this session", () => {
    useErrorFeedStore.getState().setAnalyzeThread("E-1", {
      conversationId: "c-1",
      runState: RUN_STATE.DONE,
      startedAt: Date.now(),
      messages: [
        {
          id: "synth-1",
          type: MESSAGE_TYPE.SYNTHESIS,
          headline: "The refund tool times out after 5 seconds.",
          fix: "Raise the tool timeout to 20 seconds.",
          confidence: "H",
          category: "high confidence",
        },
      ],
    });

    render(<ClusterHeadlineCard error={error} />);

    expect(
      screen.getByText("The refund tool times out after 5 seconds."),
    ).toBeInTheDocument();
    expect(screen.getByText("Analyzed just now")).toBeInTheDocument();
  });
});
