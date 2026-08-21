/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import useKpis from "src/hooks/useKpis";
import TestRunInsight from "../TestRunInsight";

vi.mock("src/hooks/useKpis", () => ({ default: vi.fn() }));

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useParams: () => ({ executionId: "exec-1" }) };
});

// Isolate the test from KPI parsing internals; the fix only gates which
// cards render based on agent_type, not how the metrics are computed.
vi.mock("../common", () => ({
  extractKpis: () => ({
    systemMetrics: {
      totalCalls: 5,
      avgAgentLatency: 50,
      avgBotWpm: 120,
      agentTalkPercentage: 60,
      customerTalkPercentage: 40,
      avgStopTimeAfterInterruption: 20,
    },
    evalMetrics: {},
  }),
}));

const VOICE_ONLY_TITLES = [
  "Avg. Agent Latency (ms)",
  "Agent WPM",
  "Talk Ratio (A/S%)",
  "Agent Stop Latency (ms)",
];

const setAgentType = (agentType) =>
  useKpis.mockReturnValue({
    data: { agent_type: agentType },
    isPending: false,
  });

describe("TestRunInsight voice-only metrics", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("hides voice-only metrics for a chat (text) simulation", () => {
    setAgentType("text");
    render(<TestRunInsight />);

    // A non-voice card confirms the metrics section actually rendered.
    expect(screen.getByText("Total calls")).toBeInTheDocument();

    // The bug rendered these voice metrics for chat sims too.
    for (const title of VOICE_ONLY_TITLES) {
      expect(screen.queryByText(title)).not.toBeInTheDocument();
    }
  });

  it("shows voice-only metrics for a voice simulation", () => {
    setAgentType("voice");
    render(<TestRunInsight />);

    for (const title of VOICE_ONLY_TITLES) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
  });
});
