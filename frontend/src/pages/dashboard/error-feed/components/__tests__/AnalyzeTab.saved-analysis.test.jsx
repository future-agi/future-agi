import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import AnalyzeTab from "../AnalyzeTab";
import { useErrorFeedStore } from "../../store";
import { hydrateFromCache } from "../../clusterAnalyzeSocket";
import { MESSAGE_TYPE, RUN_STATE } from "../../constants";

vi.mock("../../useAnalyzeRunner", () => ({
  useFollowUpRunner: () => ({ runFollowUp: vi.fn() }),
}));

const error = { cluster_id: "E-1", project_id: "p-1" };

// A saved analysis from another day must say which day it started, not only
// the time, so the Fix tab never passes an old run off as today's.
describe("AnalyzeTab start time", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 2, 6, 14, 5));
    useErrorFeedStore.setState({
      analyzeThreadsByCluster: {},
      analyzePendingStartByCluster: {},
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the date of a saved analysis from another day", () => {
    hydrateFromCache({
      clusterId: "E-1",
      rca: {
        synthesis: "The prompt says refunds are usually allowed.",
        fix: "State the 30 day rule.",
        confidence: "H",
        analyzed_at: new Date(2026, 2, 3, 14, 5).toISOString(),
      },
    });

    render(<AnalyzeTab error={error} />);

    expect(screen.getByText(/started Mar 3, 14:05/)).toBeInTheDocument();
  });

  it("shows only the time for a run started today", () => {
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

    render(<AnalyzeTab error={error} />);

    // "· started" is the context strip; the "Try asking" chips also say
    // "started", so match the strip's own text only.
    expect(screen.getByText(/· started /)).toBeInTheDocument();
    expect(screen.queryByText(/started Mar/)).not.toBeInTheDocument();
  });
});
